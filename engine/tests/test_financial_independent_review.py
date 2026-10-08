"""Independent cross-language financial evidence adversaries, entirely offline.

The numerical package is produced by an explicitly injected synthetic provider.
Worker tests use real local Miniflare D1/R2; no provider, deployed resource or fit.
"""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from atlas_quant.runner import RunnerError
from atlas_quant.financial_runner.publication import compute_publication
from atlas_quant.financial_runner.protocol import encode, sha
from atlas_quant.financial_statements.results import canonical_hash
from test_financial_adapter import run
from test_financial_package import declarations, freeze
from test_financial_publication import (
    document_package,
    publish,
    records,
    task_inputs,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def review_source():
    acquired = run()
    return acquired, freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "OTHER_PROVIDER"),
        ("symbol", "000001.SZ"),
        ("currency", "USD"),
        ("reference", "Different source PDF despite equal value"),
    ],
)
def test_same_value_document_proof_cannot_replace_authorized_proof(
    review_source, field, value
):
    original = document_package(review_source[0])
    candidate = deepcopy(original)
    entry = candidate["bindings"]["balancesheet.total_assets"][0]["value"]
    if field in {"currency", "reference"}:
        entry["evidence"][field] = value
    else:
        entry[field] = value
    candidate["packRoot"] = canonical_hash(
        {key: val for key, val in candidate.items() if key != "packRoot"}
    )
    approved = [
        item
        for values in original["bindings"].values()
        for item in values
        if item["type"] != "DeclaredUnitBinding"
    ]
    with pytest.raises(RunnerError) as error:
        compute_publication(
            *task_inputs(candidate, "financial_validate", approved),
            lambda *_: pytest.fail("Unauthorized proof must write no chunk"),
        )
    assert error.value.code == "UNIT_PROOF_REGISTRY_MISMATCH"


def test_wholly_missing_preparation_preserves_every_selected_coordinate(review_source):
    package = freeze(
        review_source[0], declarations(review_source[0]), trusted_unit_proofs=False
    )
    manifest, chunks = publish(task_inputs(package))
    selected = set(package["selection"]["selectedStates"])
    coverage = records(chunks, "coverage")
    panel = records(chunks, "panel")
    assert {(x["symbol"], x["stateId"]) for x in coverage} == {
        (symbol, state)
        for symbol in package["selection"]["universe"]["symbols"]
        for state in selected
    }
    assert manifest["summary"]["hasUsableStates"] is False
    for row in coverage:
        count = sum(x["ts_code"] == row["symbol"] for x in panel)
        assert row["okRows"] == 0 and row["missingRows"] == count
        assert row["reasonCounts"]
    assert all(row[state] is None for row in panel for state in selected)
    assert all(e["result"]["status"] == "missing" for e in records(chunks, "events"))


def test_projected_dependencies_reconstruct_original_hash_and_order(review_source):
    _, chunks = publish(task_inputs(review_source[1]))
    dependencies = records(chunks, "dependencies")
    used = []
    for projected in records(chunks, "events"):
        event = deepcopy(projected)
        start = event.pop("dependencyStart")
        count = event.pop("dependencyCount")
        group = dependencies[start : start + count] if count else []
        assert [x["index"] for x in group] == list(range(count))
        assert all(x["eventId"] == event["id"] for x in group)
        used.extend(range(start, start + count))
        event["result"]["dependencies"] = [x["dependency"] for x in group]
        result = event["result"]
        assert (
            canonical_hash({k: v for k, v in result.items() if k != "lineageHash"})
            == result["lineageHash"]
        )
        assert (
            canonical_hash({k: v for k, v in event.items() if k != "id"}) == event["id"]
        )
    assert used == list(range(len(dependencies)))


# Seed frozen jobs directly to isolate finalization from the UI. The artifacts
# are genuine outputs above, then altered and self-consistently rehashed below.
# The public runner routes and real D1/R2 still perform all admission/upload work.
EDGE_REVIEW = r"""
const {default:fs}=await import('node:fs/promises');
const {randomUUID,createHash}=await import('node:crypto');
const {Miniflare}=await import('miniflare');
const {buildWorkerSource}=await import('./scripts/worker-source.mjs');
let input=''; for await(const p of process.stdin) input+=p;
const cases=JSON.parse(input), output={};
const digest=x=>createHash('sha256').update(x).digest('hex');
const mf=new Miniflare({modules:true,script:await buildWorkerSource({buildId:'independent-financial-review'}),
 compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],
 bindings:{RUNNER_SECRET:'local-review-only',FINANCIAL_WORKSPACE_ENABLED:'true'}});
try {
 const db=await mf.getD1Database('DB'), bucket=await mf.getR2Bucket('ARTIFACTS');
 await db.exec((await fs.readFile('./edge/schema.sql','utf8')).replaceAll('\n',' '));
 async function request(path,method='GET',value=null,lease=null,raw=null) {
   const headers={authorization:'Bearer local-review-only','content-type':'application/json'};
   if(lease) headers['X-Financial-Lease']=lease;
   const response=await mf.dispatchFetch('https://review.test/quant/api/runner/financial/'+path,
      {method,headers,body:raw??(value===null?undefined:JSON.stringify(value))});
   const text=await response.text(); let body;try{body=JSON.parse(text);}catch{body={text};}
   return {status:response.status,body};
 }
 for(const sample of cases) {
   const jobId=randomUUID(),inputId=randomUUID(),lease=randomUUID(),owner=randomUUID(),registryId=randomUUID();
   const now=new Date().toISOString(), until=new Date(Date.now()+120000).toISOString(),deadline=new Date(Date.now()+600000).toISOString();
   const m=sample.manifest; m.inputId=inputId;
   const calendarRaw=JSON.stringify(sample.calendar),calendarKey='review/calendar/'+registryId;
   await bucket.put(calendarKey,calendarRaw);
   await db.prepare('INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)')
    .bind(registryId,'calendar',owner,calendarKey,digest(calendarRaw),Buffer.byteLength(calendarRaw),'{}',now).run();
   await db.prepare('INSERT INTO financial_inputs(id,owner,name,status,calendar_ref,proof_refs,declared_bytes,roots,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)')
    .bind(inputId,owner,'independent synthetic review','preparing',registryId,'[]',0,JSON.stringify({...m.roots,preparedRoot:null}),randomUUID(),'a'.repeat(64),now,now).run();
   await db.prepare('INSERT INTO financial_jobs(id,owner,input_id,kind,status,spec,request_id,request_hash,lease_token,lease_until,deadline,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)')
    .bind(jobId,owner,inputId,'financial_prepare','running',JSON.stringify({expectedPackRoot:m.roots.packRoot}),randomUUID(),'b'.repeat(64),lease,until,deadline,now,now).run();
   let response=await request('publications/begin','POST',{jobId,leaseToken:lease,manifest:m});
   if(response.status!==200){output[sample.name]={...response,stage:'begin'};continue;}
   const receipt=response.body;
   for(const part of sample.chunks){
     response=await request('publications/'+receipt.publicationId+'/chunks/'+part.name+'/'+part.ordinal+'?manifestSha256='+receipt.manifestSha256,
       'PUT',null,lease,part.raw);
     if(response.status!==200) break;
   }
   if(response.status!==200){output[sample.name]={...response,stage:'upload'};continue;}
   if(sample.name==='cancelled_complete')
     await db.prepare("UPDATE financial_jobs SET status='cancel_requested' WHERE id=?").bind(jobId).run();
   if(sample.name==='expired_complete')
     await db.prepare("UPDATE financial_jobs SET lease_until='2020-01-01T00:00:00.000Z' WHERE id=?").bind(jobId).run();
   response=await request('complete','POST',{jobId,leaseToken:lease,publicationId:receipt.publicationId,manifestSha256:receipt.manifestSha256});
   const saved=await db.prepare('SELECT status FROM financial_publications WHERE id=?').bind(receipt.publicationId).first();
   output[sample.name]={...response,stage:'complete',publicationStatus:saved.status};
   if(sample.name==='valid' && response.status===200) {
     const token=randomUUID()+'.'+randomUUID(), otherToken=randomUUID()+'.'+randomUUID();
     for(const [who,t] of [[owner,token],[randomUUID(),otherToken]])
       await db.prepare('INSERT INTO workspaces(id,token_hash,name,created_at) VALUES(?,?,?,?)')
         .bind(who,digest(t),'independent review',now).run();
     const url='https://review.test/quant/api/financial/inputs/'+inputId+'/download?packRoot='+m.roots.packRoot;
     const downloaded=await mf.dispatchFetch(url,{headers:{cookie:'aq_session='+token}});
     output.valid.packageDownloadMatches = digest(Buffer.from(await downloaded.arrayBuffer()))===m.collections.package.sha256;
     const denied=await mf.dispatchFetch(url,{headers:{cookie:'aq_session='+otherToken}});
     output.valid.otherOwnerStatus=denied.status; await denied.text();
     const eventUrl='https://review.test/quant/api/financial/preparations/'+response.body.preparationId+
       '/events/'+sample.downloadEvent.id+'/download?preparedRoot='+m.roots.preparedRoot;
     const eventResponse=await mf.dispatchFetch(eventUrl,{headers:{cookie:'aq_session='+token}});
     output.valid.downloadedEvent=await eventResponse.json();
     const part=await db.prepare("SELECT object_key FROM financial_chunks WHERE publication_id=? AND collection='package' ORDER BY ordinal DESC LIMIT 1")
       .bind(receipt.publicationId).first();
     await bucket.delete(part.object_key);
     try {
       // dispatchFetch does not enforce TCP Content-Length truncation. Exercise
       // the real local HTTP export just as the browser/runner must consume it.
       const localUrl=new URL(new URL(url).pathname+new URL(url).search,await mf.ready);
       const broken=await fetch(localUrl,{headers:{cookie:'aq_session='+token}});
       const raw=await broken.arrayBuffer();
       output.valid.brokenDownload={status:broken.status,byteLength:raw.byteLength,headers:Object.fromEntries(broken.headers)};
       output.valid.truncatedDownloadRejected=broken.status!==200;
     } catch {output.valid.truncatedDownloadRejected=true;}
   }
 }
 process.stdout.write(JSON.stringify(output));
} finally {await mf.dispose();}
"""


def replace_records(manifest, chunks, name, values):
    for key in list(chunks):
        if key[0] == name:
            del chunks[key]
    raw = encode(values)
    if values:
        chunks[(name, 0)] = raw
    manifest["collections"][name] = {
        "encoding": "json_records",
        "rowCount": len(values),
        "byteLength": len(raw) if values else 0,
        "chunks": (
            [
                {
                    "ordinal": 0,
                    "startRow": 0,
                    "rowCount": len(values),
                    "byteLength": len(raw),
                    "sha256": sha(raw),
                }
            ]
            if values
            else []
        ),
    }


@pytest.fixture(scope="module")
def edge_review_results(review_source):
    if not shutil.which("node") or not (ROOT / "node_modules/miniflare").exists():
        pytest.skip(
            "Cross-language review needs the repository's existing Node runtime"
        )
    manifest, chunks = publish(task_inputs(review_source[1]))
    event = max(records(chunks, "events"), key=lambda row: row["dependencyCount"])
    download_event = deepcopy(event)
    start = download_event.pop("dependencyStart")
    count = download_event.pop("dependencyCount")
    download_event["result"]["dependencies"] = [
        row["dependency"]
        for row in records(chunks, "dependencies")[start : start + count]
    ]
    _, meta, _, registry = task_inputs(review_source[1])
    calendar = json.loads(registry[meta["calendar"]["ref"]])
    cases = []
    for name in [
        "valid",
        "missing_coverage",
        "empty_panel",
        "unknown_assignment_event",
        "missing_assignment",
        "dependency_order",
        "false_usable_summary",
        "assignment_mask_mismatch",
        "cancelled_complete",
        "expired_complete",
    ]:
        altered, payload = deepcopy(manifest), dict(chunks)
        if name == "missing_coverage":
            replace_records(
                altered, payload, "coverage", records(payload, "coverage")[1:]
            )
        elif name == "empty_panel":
            replace_records(altered, payload, "panel", [])
        elif name == "unknown_assignment_event":
            rows = records(payload, "assignments")
            rows[0]["states"][next(iter(rows[0]["states"]))] = "f" * 64
            replace_records(altered, payload, "assignments", rows)
        elif name == "missing_assignment":
            replace_records(
                altered, payload, "assignments", records(payload, "assignments")[1:]
            )
        elif name == "dependency_order":
            values = records(payload, "dependencies")
            values[0], values[1] = values[1], values[0]
            replace_records(altered, payload, "dependencies", values)
        elif name == "false_usable_summary":
            assert altered["summary"]["hasUsableStates"] is True
            altered["summary"]["hasUsableStates"] = False
        elif name == "assignment_mask_mismatch":
            field = "model_fin_cash_asset_share"
            panel = records(payload, "panel")
            for row in panel:
                row[field] = row[field + "__available_date"] = None
            coverage = records(payload, "coverage")
            for row in coverage:
                if row["stateId"] == field:
                    row.update(okRows=0, missingRows=len(panel), status="missing")
            replace_records(altered, payload, "panel", panel)
            replace_records(altered, payload, "coverage", coverage)
        cases.append(
            {
                "name": name,
                "manifest": altered,
                "calendar": calendar,
                "downloadEvent": download_event,
                "chunks": [
                    {"name": key[0], "ordinal": key[1], "raw": raw.decode()}
                    for key, raw in payload.items()
                ],
            }
        )
    result = subprocess.run(
        [
            "node",
            "-e",
            "(async()=>{"
            + EDGE_REVIEW
            + "})().catch(e=>{console.error(e);process.exitCode=1;})",
        ],
        cwd=ROOT,
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout)


def test_worker_accepts_unmodified_real_synthetic_preparation(edge_review_results):
    actual = edge_review_results["valid"]
    assert (
        actual["status"] == 200 and actual["publicationStatus"] == "committed"
    ), actual
    assert actual["packageDownloadMatches"] is True
    assert actual["otherOwnerStatus"] == 404
    assert actual["truncatedDownloadRejected"] is True, actual.get("brokenDownload")


def test_full_event_download_preserves_original_dependency_order_and_lineage(
    edge_review_results,
):
    event = edge_review_results["valid"]["downloadedEvent"]
    result = event["result"]
    assert len(result["dependencies"]) == 10
    assert (
        canonical_hash(
            {key: value for key, value in result.items() if key != "lineageHash"}
        )
        == result["lineageHash"]
    )
    assert (
        canonical_hash({key: value for key, value in event.items() if key != "id"})
        == event["id"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_coverage",
        "empty_panel",
        "unknown_assignment_event",
        "missing_assignment",
        "dependency_order",
        "false_usable_summary",
        "assignment_mask_mismatch",
        "cancelled_complete",
        "expired_complete",
    ],
)
def test_worker_rejects_self_consistent_incomplete_financial_evidence(
    edge_review_results, mutation
):
    actual = edge_review_results[mutation]
    assert actual["status"] in {400, 409}, actual
    assert actual.get("publicationStatus") != "committed", actual
