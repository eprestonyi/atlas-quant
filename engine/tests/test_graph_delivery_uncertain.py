"""Real HTTP decoders and encrypted evidence, synthetic source only; no F/provider."""

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from atlas_quant.dataset_runner.protocol import LIMITS, encode, sha
from atlas_quant.dataset_runner.source_spool import store_sources
from atlas_quant.graph_dataset_runner import service
from atlas_quant.graph_dataset_runner.client import GraphDatasetClient
from atlas_quant.graph_dataset_runner.spool import GraphDatasetSpool
from atlas_quant.runner import RunnerError
from test_dataset_client import Response
from test_graph_dataset_components import configuration
from test_graph_dataset_service import prepared, writer, Queue
from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source


class HTTPQueue:
    """Accept the mutation first, then fault only its HTTP response."""

    def __init__(self, queue, fault_at=None, fault=None):
        self.queue, self.fault_at, self.fault = queue, fault_at, fault
        self.requests = []
        self.readback_failure = None
        self.status_failure = None
        self.write_rejection = None

    def request(self, method, url, *, data, headers, **kwargs):
        parsed = urlsplit(url)
        route = parsed.path.split('/runner/dataset-graphs/', 1)[1]
        self.requests.append((method, route, parsed.query))
        assert kwargs['allow_redirects'] is False
        if method == 'GET' and route.endswith('/publication') and self.readback_failure:
            return self.readback_failure
        if method == 'GET' and route.endswith('/status') and self.status_failure:
            return self.status_failure
        if method == 'POST' and route.endswith('/publication') and self.write_rejection:
            return Response(b'PRIVATE REJECTION TEXT', status=self.write_rejection)
        if method == 'POST':
            import json
            value = self.queue.post(route, json.loads(data))
        elif method == 'GET':
            value = self.queue.get(route + ('?' + parsed.query if parsed.query else ''), headers['X-Dataset-Lease'])
        else:
            assert method == 'PUT'
            _, _, _, publication_id, _, component, ordinal = route.split('/')
            part = next(p for c in self.queue.manifest['components'] if c['componentId'] == component for p in c['parts'] if p['ordinal'] == int(ordinal))
            value = self.queue.upload(self.queue.task, publication_id, parse_qs(parsed.query)['datasetRoot'][0], component, part, data)
        stage = 'part' if method == 'PUT' else 'begin' if method == 'POST' and route.endswith('/publication') else 'complete' if method == 'POST' and route.endswith('/complete') else 'heartbeat' if route == 'heartbeat' else None
        if stage and self.fault_at == stage:
            self.fault_at = None
            return self.fault(deepcopy(value))
        return Response(encode(value))


def without(key):
    def change(value):
        value.pop(key)
        return Response(encode(value))
    return change


def encrypted_files(spool):
    return {str(path.relative_to(spool.root)): path.read_bytes() for path in spool.root.rglob('*.enc') if path.name != 'current.enc'}


def existing_publication(tmp_path, prepared, fault_at, fault):
    task, data, manifest, parts = prepared
    settings = configuration(tmp_path)
    spool = GraphDatasetSpool(settings)
    state = spool.save(dict(spool.current_or_create(), phase='publishing', job=task))
    spool.remember_input(task, data[0])
    store_sources(spool, task, data, lambda: None)
    writer(manifest, parts, [])(spool, task, data, None)
    queue = Queue(data, task)
    network = HTTPQueue(queue, fault_at, fault)
    client = GraphDatasetClient(settings, network)
    return settings, spool, state, queue, network, client


def run(settings, spool, client):
    service.run_once(settings, spool, client, client, bounded_compute=lambda *a, **k: pytest.fail('delivery recomputed sources'))


@pytest.mark.parametrize('stage,fault', [
    ('begin', without('publicationId')),
    ('begin', lambda value: Response(encode({**value, 'datasetRoot': 'f' * 64}))),
    ('begin', lambda value: Response(encode({**value, 'missing': [{'componentId': 'schema'}]}))),
    ('begin', lambda value: Response(encode({**value, 'status': 'committed'}))),
    ('begin', lambda _: Response(b'[]')),
    ('begin', lambda _: Response(b'{invalid json')),
    ('begin', lambda value: Response(encode(value), headers={'Content-Encoding': 'gzip'})),
    ('begin', lambda value: Response(encode(value), headers={'Content-Length': str(LIMITS['inputMetadataBytes'] + 1)})),
    ('part', lambda _: Response(b'{invalid json')),
    ('complete', without('datasetRef')),
    ('complete', lambda value: Response(encode({**value, 'datasetRef': {**value['datasetRef'], 'version': 2}}))),
], ids=['missing-id', 'wrong-root', 'bad-missing-list', 'committed-with-missing-parts', 'non-object', 'bad-json', 'bad-encoding', 'oversized-ack', 'part-json', 'missing-completion-ref', 'legacy-completion-ref'])
def test_success_ack_ambiguity_keeps_exact_evidence_and_recovers_by_readback(prepared, tmp_path, stage, fault):
    settings, spool, original, queue, network, client = existing_publication(tmp_path, prepared, stage, fault)
    before = encrypted_files(spool)
    with pytest.raises(RunnerError) as error:
        run(settings, spool, client)
    assert error.value.code == 'DATASET_PUBLICATION_UNCERTAIN'
    pending = spool.read()
    assert pending['requestId'] == original['requestId'] and pending['phase'] == 'publishing'
    assert pending['publication']['datasetRoot'] == sha(encode(prepared[2]))
    assert encrypted_files(spool) == before
    assert queue.error is None and not (spool.root / 'quarantine').exists()
    assert not any(route.endswith('/fail') for _, route, _ in network.requests)

    # New service instance resumes the same durable intent; no duplicate begin,
    # no duplicate accepted part, and no second complete after terminal readback.
    run(settings, GraphDatasetSpool(settings), client)
    assert queue.status == 'completed' and queue.parts == prepared[3]
    assert GraphDatasetSpool(settings).read() is None
    assert queue.input_calls == 0 and len(set(queue.claims)) == 1
    assert sum(method == 'POST' and route.endswith('/publication') for method, route, _ in network.requests) == 1
    puts = [route for method, route, _ in network.requests if method == 'PUT']
    assert len(puts) == len(set(puts)) == len(prepared[3])
    assert sum(route.endswith('/complete') for _, route, _ in network.requests) == 1
    if stage != 'complete':
        assert any(method == 'GET' and route.endswith('/publication') and query == 'datasetRoot=' + queue.root for method, route, query in network.requests)


@pytest.mark.parametrize('response', [Response(b'{}', status=404), Response(b'{}', status=409), Response(b'{broken'), Response(b'[]')], ids=['404', '409', 'json', 'shape'])
def test_failed_readback_is_not_a_result_rejection_or_permission_to_republish(prepared, tmp_path, response):
    settings, spool, _, queue, network, client = existing_publication(tmp_path, prepared, 'begin', without('publicationId'))
    with pytest.raises(RunnerError):
        run(settings, spool, client)
    before, intent = encrypted_files(spool), spool.read()
    network.readback_failure = response
    for _ in range(2):
        with pytest.raises(RunnerError) as error:
            run(settings, GraphDatasetSpool(settings), client)
        assert error.value.code == 'DATASET_PUBLICATION_UNCERTAIN'
        assert spool.read() == intent and encrypted_files(spool) == before
    assert queue.status == 'running' and queue.error is None
    assert not (spool.root / 'quarantine').exists()
    assert sum(method == 'POST' and route.endswith('/publication') for method, route, _ in network.requests) == 1
    network.readback_failure = None
    run(settings, GraphDatasetSpool(settings), client)
    assert queue.status == 'completed' and spool.read() is None


def test_durable_intent_before_send_never_guesses_that_a_404_permits_begin(prepared, tmp_path):
    settings, spool, state, queue, network, client = existing_publication(tmp_path, prepared, None, None)
    state = spool.save(dict(state, publication={'datasetRoot': sha(encode(prepared[2]))}))
    before = encrypted_files(spool)
    network.readback_failure = Response(b'{}', status=404)
    with pytest.raises(RunnerError):
        run(settings, spool, client)
    assert spool.read() == state and encrypted_files(spool) == before
    assert queue.root is None and queue.status == 'running'
    assert not any(method == 'POST' and route.endswith(('/publication', '/fail', '/complete')) for method, route, _ in network.requests)


@pytest.mark.parametrize('fault', [lambda _: Response(b'[]'), lambda _: Response(b'{bad json'), lambda _: Response(b'{}', status=403)], ids=['shape', 'json', 'http403-is-not-a-publication-rejection'])
def test_malformed_recovery_heartbeat_cannot_discard_completed_local_publication(prepared, tmp_path, fault):
    settings, spool, state, queue, network, client = existing_publication(tmp_path, prepared, 'heartbeat', fault)
    before = encrypted_files(spool)
    with pytest.raises(RunnerError):
        run(settings, spool, client)
    assert spool.read() == state and encrypted_files(spool) == before
    assert queue.status == 'running' and queue.error is None
    assert not any(route.endswith('/fail') for _, route, _ in network.requests)
    run(settings, GraphDatasetSpool(settings), client)
    assert queue.status == 'completed' and spool.read() is None


def test_malformed_terminal_readback_cannot_release_original_claim_or_evidence(prepared, tmp_path):
    settings, spool, _, queue, network, client = existing_publication(tmp_path, prepared, 'complete', without('datasetRef'))
    before = encrypted_files(spool)
    with pytest.raises(RunnerError):
        run(settings, spool, client)
    intent = spool.read()
    assert queue.status == 'completed'
    network.status_failure = Response(encode({'job': {'id': queue.task['id'], 'status': 'completed'}, 'datasetRef': {**queue.ref(), 'datasetId': None}}))
    with pytest.raises(RunnerError) as error:
        run(settings, GraphDatasetSpool(settings), client)
    assert error.value.code == 'DATASET_PUBLICATION_UNCERTAIN'
    assert spool.read() == intent and encrypted_files(spool) == before
    assert not any(route.endswith('/fail') for _, route, _ in network.requests)
    network.status_failure = None
    run(settings, GraphDatasetSpool(settings), client)
    assert spool.read() is None
    assert sum(route.endswith('/complete') for _, route, _ in network.requests) == 1


@pytest.mark.parametrize('status', [400, 403, 404, 409, 413, 422])
def test_only_explicit_write_rejection_quarantines_then_settles_failure(prepared, tmp_path, status):
    settings, spool, _, queue, network, client = existing_publication(tmp_path, prepared, None, None)
    network.write_rejection = status
    before = encrypted_files(spool)
    run(settings, spool, client)
    assert queue.status == 'failed' and queue.error['code'] == 'DATASET_RESULT_REJECTED'
    assert spool.read() is None
    after = encrypted_files(spool)
    assert {path: after[path] for path in before} == before
    assert len(list((spool.root / 'quarantine').glob('*.enc'))) == 1
    assert all(b'PRIVATE REJECTION TEXT' not in raw for raw in after.values())
    assert sum(route.endswith('/fail') for _, route, _ in network.requests) == 1
    assert sum(method == 'POST' and route.endswith('/publication') for method, route, _ in network.requests) == 1
