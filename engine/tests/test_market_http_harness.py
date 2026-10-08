"""A lost user mutation ACK must never create another experiment or forecast."""

import importlib.util
import json
from pathlib import Path
import sys
import pytest


@pytest.fixture
def harness(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "market_http_harness", scripts / "market-http-acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["experiment.json", "forecast-start.json"])
def test_lost_ack_leaves_intent_and_does_not_repeat_post(tmp_path, harness, name):
    calls = []

    def api(route, body):
        calls.append((route, body))
        raise OSError("The response was lost after server commit")

    with pytest.raises(OSError):
        harness.create_once(
            tmp_path,
            name,
            "/mutation",
            {"version": 1},
            api=api,
            save=lambda *_: pytest.fail("No receipt"),
        )
    with pytest.raises(RuntimeError, match="will not repeat"):
        harness.create_once(
            tmp_path, name, "/mutation", {"version": 1}, api=api, save=lambda *_: None
        )
    assert len(calls) == 1
    assert (tmp_path / name).with_suffix(".intent.json").stat().st_mode & 0o077 == 0


def test_existing_receipt_is_read_without_network(tmp_path, harness):
    value = {"job": {"id": "fixed"}}
    (tmp_path / "forecast-start.json").write_text(json.dumps(value))
    assert (
        harness.create_once(
            tmp_path,
            "forecast-start.json",
            "/run",
            {},
            api=lambda *_: pytest.fail("No second fit"),
            save=lambda *_: None,
        )
        == value
    )
