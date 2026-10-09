import json
from pathlib import Path

import pytest

from atlas_quant.statistical_quant import ai_review_transport as transport


def preflight():
    thread = {"thread": {"id": "ephemeral-test", "ephemeral": True},
              "instructionSources": [], "runtimeWorkspaceRoots": []}
    features = {"data": [{"name": n, "enabled": False} for n in transport.DISABLED_FEATURES]
                + [{"name": "skip_host_skill_discovery", "enabled": True},
                   {"name": "unified_exec", "enabled": True}], "nextCursor": None}
    return thread, features, {"data": [], "nextCursor": None}, {"config": {"web_search": "disabled", "agents": {"enabled": False}}}


@pytest.mark.parametrize("feature", transport.DISABLED_FEATURES)
def test_each_unsafe_feature_fails_closed(feature):
    data = preflight()
    next(x for x in data[1]["data"] if x["name"] == feature)["enabled"] = True
    with pytest.raises(transport.ReviewTransportError, match="FEATURE_NOT_DISABLED"):
        transport.validate_preflight(*data)


def test_missing_feature_is_not_assumed_disabled():
    data = preflight()
    data[1]["data"] = [x for x in data[1]["data"] if x["name"] != "view_image"]
    with pytest.raises(transport.ReviewTransportError, match="FEATURE_NOT_DISABLED"):
        transport.validate_preflight(*data)


@pytest.mark.parametrize("change,code", [
    (lambda d: d[0]["thread"].update(ephemeral=False), "NOT_EPHEMERAL"),
    (lambda d: d[0].update(instructionSources=["/private/instructions"]), "CONTEXT_PRESENT"),
    (lambda d: d[0].update(runtimeWorkspaceRoots=["/private"]), "CONTEXT_PRESENT"),
    (lambda d: d[1].update(nextCursor="more"), "INCOMPLETE"),
    (lambda d: d[2].update(nextCursor="more"), "INCOMPLETE"),
    (lambda d: d[2].update(data=[{"name": "disabled-but-present"}]), "NOT_EMPTY"),
    (lambda d: d[3]["config"].update(web_search="cached"), "WEB_SEARCH"),
    (lambda d: d[3]["config"].update(agents={"enabled": True}), "AGENTS_NOT_DISABLED"),
])
def test_context_and_inventory_gates(change, code):
    data = preflight()
    change(data)
    with pytest.raises(transport.ReviewTransportError, match=code):
        transport.validate_preflight(*data)


def test_safe_preflight_does_not_misread_forced_pty_flag_as_environment_access():
    assert transport.validate_preflight(*preflight())["executionEnvironments"] == []


def test_disabled_mcp_metadata_is_retained_but_no_runtime_or_resources_are_allowed():
    data = preflight()
    item = {"name": "node_repl", "runtimeStatus": "disabled", "tools": {},
            "resources": [], "resourceTemplates": [], "serverCapabilities": None,
            "serverInfo": None, "toolsError": None}
    data[2]["data"] = [item]
    assert transport.validate_preflight(*data)["activeMcpServerCount"] == 0
    for field, value in (("runtimeStatus", "ready"), ("tools", {"js": {}}),
                         ("resources", [{"uri": "secret"}]), ("resourceTemplates", [{}]),
                         ("serverCapabilities", {"resources": {}})):
        previous = item[field]
        item[field] = value
        with pytest.raises(transport.ReviewTransportError, match="NOT_EMPTY"):
            transport.validate_preflight(*data)
        item[field] = previous


def test_thread_has_no_environment_or_dynamic_capability():
    value = transport.thread_params(Path("/private/review"), "gpt-6.1-sol", "max", transport.isolated_config())
    for key in ("environments", "runtimeWorkspaceRoots", "selectedCapabilityRoots", "dynamicTools"):
        assert value[key] == []
    assert value["ephemeral"] is True
    assert value["config"]["model_reasoning_effort"] == "max"


def test_inherited_mcp_name_is_literal_not_new_quoted_name():
    value = transport.isolated_config(["computer-use", "node_repl"])
    assert value["mcp_servers.computer-use.enabled"] is False
    assert value["mcp_servers.node_repl.enabled"] is False
    for unsafe in ('name.with.dot', 'name"quoted', '', 'x\napp', None):
        with pytest.raises(transport.ReviewTransportError, match="NAME_INVALID"):
            transport.isolated_config([unsafe])


def test_discovery_never_receives_prompt_and_residual_mcp_blocks_inference(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    seen = []

    class FakeSession:
        def __init__(self, executable, directory, config, deadline, label):
            self.config, self.label = config, label
            seen.append((label, "start", config))

        def start(self, *args):
            data = preflight()
            data[2]["data"] = [{"name": "node_repl"}]
            return data

        def run(self, *args):
            pytest.fail("No prompt may reach inference while any MCP remains")

        def close(self):
            seen.append((self.label, "close"))

    monkeypatch.setattr(transport, "_Session", FakeSession)
    with pytest.raises(transport.ReviewTransportError, match="NOT_EMPTY"):
        transport.run_isolated_review("unused", tmp_path, "PRIVATE PROMPT", {})
    assert [x[:2] for x in seen] == [("transport-0", "start"), ("transport-0", "close"),
                                   ("transport-1", "start"), ("transport-1", "close")]
    assert seen[2][2]["mcp_servers.node_repl.enabled"] is False
    assert "PRIVATE PROMPT" not in repr(seen)


def test_empty_inventory_sends_exactly_one_turn(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    calls = []

    class FakeSession:
        def __init__(self, *args):
            pass

        def start(self, *args):
            return preflight()

        def run(self, *args):
            calls.append(args)
            return '{"candidateId":"ridge:0"}'

        def close(self):
            pass

    monkeypatch.setattr(transport, "_Session", FakeSession)
    text, receipt = transport.run_isolated_review("unused", tmp_path, "private", {}, effort="max")
    assert json.loads(text)["candidateId"] == "ridge:0"
    assert len(calls) == 1
    assert calls[0][2:] == ("gpt-6.1-sol", "max")
    assert receipt["disabledInheritedMcpServerCount"] == 0


def test_no_retry_after_model_dispatch_failure(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    calls = []

    class FakeSession:
        def __init__(self, *args):
            pass

        def start(self, *args):
            return preflight()

        def run(self, *args):
            calls.append(1)
            raise transport.ReviewTransportError("UNKNOWN")

        def close(self):
            pass

    monkeypatch.setattr(transport, "_Session", FakeSession)
    with pytest.raises(transport.ReviewTransportError, match="UNKNOWN"):
        transport.run_isolated_review("unused", tmp_path, "private", {})
    assert calls == [1]


def buffered_session(tmp_path, message):
    session = transport._Session.__new__(transport._Session)
    session.buffer = (json.dumps(message) + "\n").encode()
    session.events = (tmp_path / "events").open("wb")
    return session


def test_all_server_requests_denied_without_callback_execution(tmp_path):
    session = buffered_session(tmp_path, {"id": 10, "method": "item/tool/call",
        "params": {"tool": "read_secret", "arguments": {"path": "/private"}}})
    sent = []
    session.send = sent.append
    try:
        with pytest.raises(transport.ReviewTransportError, match="SERVER_REQUEST_REJECTED"):
            session.receive()
    finally:
        session.events.close()
    assert sent == [{"id": 10, "error": {"code": -32601,
        "message": "This review client does not execute server requests."}}]


@pytest.mark.parametrize("kind", ["commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall"])
def test_nontext_notifications_fail_integrity_check(tmp_path, kind):
    session = buffered_session(tmp_path, {"method": "item/started", "params": {"item": {"type": kind}}})
    try:
        with pytest.raises(transport.ReviewTransportError, match="NON_TEXT_ITEM"):
            session.receive()
    finally:
        session.events.close()


@pytest.mark.parametrize("method", ["item/mcpToolCall/progress", "item/commandExecution/outputDelta", "new/unknown/toolEvent"])
def test_unknown_or_tool_progress_events_are_not_silently_ignored(tmp_path, method):
    session = buffered_session(tmp_path, {"method": method, "params": {}})
    try:
        with pytest.raises(transport.ReviewTransportError, match="EVENT_NOT_VERIFIED"):
            session.receive()
    finally:
        session.events.close()


def test_turn_reasserts_empty_environment_and_handles_early_notifications():
    session = transport._Session.__new__(transport._Session)
    session.thread_id = "test"
    session.pending = [
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": '{"ok":true}'}}},
        {"method": "turn/completed", "params": {"turn": {"id": "t", "status": "completed"}}},
    ]
    sent = []
    session.request = lambda method, params: sent.append((method, params)) or {"turn": {"id": "t"}}
    result = session.run("text", {}, "gpt-6.1-sol", "high")
    assert result == '{"ok":true}'
    assert sent[0][1]["environments"] == []
    assert sent[0][1]["runtimeWorkspaceRoots"] == []
    assert sent[0][1]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
