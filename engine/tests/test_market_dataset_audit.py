"""Independent auditor acceptance and adversarial source-closure tests; no fit/network."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from market_dataset_audit import (
    AuditError,
    audit_market_dataset,
    encode,
    sha,
    tar_header,
)


@pytest.fixture(scope="module")
def source():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "engine")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/helpers/market-fixture.py")],
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )
    value = json.loads(result.stdout)
    value["scope"] = json.loads(
        (ROOT / "contracts/fixtures/market-scope-v1.json").read_text()
    )
    return value


def payloads(value):
    return {
        "manifest.json": encode(value["manifest"]),
        "plan.json": encode(value["plan"]),
        "scope.json": encode(value["scope"]),
        **{
            f"parts/{name}/{ordinal}.bin": raw.encode()
            for name, parts in value["chunks"].items()
            for ordinal, raw in parts.items()
        },
    }


def write_dir(tmp_path, value):
    path = tmp_path / "source"
    path.mkdir()
    for name, raw in payloads(value).items():
        p = path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    return path


def archive_bytes(value):
    parts = payloads(value)
    names = ["manifest.json", "plan.json", "scope.json"]
    for collection in [*sorted(value["manifest"]["collections"]), "raw"]:
        names.extend(
            f"parts/{collection}/{p['ordinal']}.bin"
            for p in (
                value["manifest"]["rawArchive"]
                if collection == "raw"
                else value["manifest"]["collections"][collection]
            )["chunks"]
        )
    return b"".join(
        tar_header(name, len(parts[name]))
        + parts[name]
        + bytes((-len(parts[name])) % 512)
        for name in names
    ) + bytes(1024)


def replace_collection(value, name, rows):
    raw = encode(rows)
    value["chunks"][name] = {"0": raw.decode()}
    value["manifest"]["collections"][name] = {
        "chunks": [
            {
                "ordinal": 0,
                "sha256": sha(raw),
                "byteLength": len(raw),
                "rowCount": len(rows),
            }
        ],
        "rowCount": len(rows),
        "byteLength": len(raw),
    }
    if name == "rows":
        value["manifest"]["rowCount"] = len(rows)


def receipt_bodies(value):
    receipts = json.loads(value["chunks"]["receipts"]["0"])
    bodies = []
    for r in receipts:
        loc = r["rawLocation"]
        raw = value["chunks"]["raw"][str(loc["ordinal"])].encode()
        bodies.append(raw[loc["offset"] : loc["offset"] + loc["byteLength"]])
    return receipts, bodies


def repack_raw(value, bodies, split=None):
    receipts = json.loads(value["chunks"]["receipts"]["0"])
    groups, pending = [], bytearray()
    for i, (r, body) in enumerate(zip(receipts, bodies)):
        if split == i:
            groups.append(bytes(pending))
            pending.clear()
        r.update(
            sha256=sha(body),
            byteLength=len(body),
            rawLocation={
                "ordinal": len(groups),
                "offset": len(pending),
                "byteLength": len(body),
            },
        )
        pending.extend(body)
    groups.append(bytes(pending))
    value["chunks"]["raw"] = {str(i): raw.decode() for i, raw in enumerate(groups)}
    value["manifest"]["rawArchive"] = {
        "chunks": [
            {"ordinal": i, "sha256": sha(raw), "byteLength": len(raw)}
            for i, raw in enumerate(groups)
        ],
        "byteLength": sum(map(len, groups)),
        "receiptCount": len(receipts),
    }
    replace_collection(value, "receipts", receipts)


def test_real_normalizer_directory_and_exact_tar(source, tmp_path):
    directory = write_dir(tmp_path, source)
    expected = sha(encode(source["manifest"]))
    report = audit_market_dataset(directory, expected_root=expected)
    assert report["status"] == "PASS" and report["rootPinned"] is True
    assert (report["symbolCount"], report["rowCount"], report["receiptCount"]) == (
        2,
        15,
        8,
    )
    assert (
        report["normalizationVerified"] is True
        and report["missingMaskVerified"] is True
    )
    assert (
        report["originalProviderWireAvailable"] is False
        and report["sourceAuthorityVerified"] is False
    )
    path = tmp_path / "source.tar"
    path.write_bytes(archive_bytes(source))
    second = audit_market_dataset(path, expected_root=expected)
    assert second["status"] == "PASS" and second["marketDatasetRoot"] == expected


def test_exact_noncanonical_wire_and_multichunk_raw_supported(source, tmp_path):
    value = copy.deepcopy(source)
    _, bodies = receipt_bodies(value)
    # Reordering/whitespace of provider JSON must preserve the delivered byte identity.
    bodies[2] = json.dumps(json.loads(bodies[2]), indent=1).encode()
    repack_raw(value, bodies, split=4)
    report = audit_market_dataset(write_dir(tmp_path, value))
    assert report["status"] == "PASS" and report["rootPinned"] is False


@pytest.mark.parametrize(
    "case",
    [
        "normalized_price",
        "missing_session",
        "extra_session",
        "raw_price",
        "adjustment",
        "calendar",
        "duplicate_provider_key",
        "raw_location",
        "raw_tail",
        "provenance",
        "whole_scope",
        "budget",
        "bool_number",
        "source_kind",
        "receipt_count",
    ],
)
def test_rehashed_semantic_and_shape_forgery_rejected(source, tmp_path, case):
    value = copy.deepcopy(source)
    rows = json.loads(value["chunks"]["rows"]["0"])
    receipts, bodies = receipt_bodies(value)
    if case == "normalized_price":
        rows[0]["close"] += 0.25
        replace_collection(value, "rows", rows)
    elif case == "missing_session":
        rows.pop(0)
        replace_collection(value, "rows", rows)
    elif case == "extra_session":
        row = next(r.copy() for r in rows if r["ts_code"] == "600000.SH")
        row["trade_date"] = "20240103"
        rows.append(row)
        replace_collection(
            value, "rows", sorted(rows, key=lambda r: (r["trade_date"], r["ts_code"]))
        )
    elif case in {"raw_price", "adjustment", "calendar"}:
        idx = {"raw_price": 2, "adjustment": 3, "calendar": 0}[case]
        raw = json.loads(bodies[idx])
        field = {
            "raw_price": "close",
            "adjustment": "adj_factor",
            "calendar": "is_open",
        }[case]
        raw["data"]["items"][0][raw["data"]["fields"].index(field)] = (
            11.25 if case == "raw_price" else 3 if case == "adjustment" else 0
        )
        bodies[idx] = encode(raw)
        repack_raw(value, bodies)
    elif case == "duplicate_provider_key":
        bodies[2] = bodies[2].replace(b'"code":0', b'"code":0,"code":0')
        repack_raw(value, bodies)
    elif case == "raw_location":
        receipts[0]["rawLocation"]["offset"] = 1
        replace_collection(value, "receipts", receipts)
    elif case == "raw_tail":
        raw = value["chunks"]["raw"]["0"].encode() + b" "
        value["chunks"]["raw"]["0"] = raw.decode()
        value["manifest"]["rawArchive"]["chunks"][0].update(
            sha256=sha(raw), byteLength=len(raw)
        )
        value["manifest"]["rawArchive"]["byteLength"] = len(raw)
    elif case == "provenance":
        p = json.loads(value["chunks"]["provenance"]["0"])
        p[0]["originalProviderWireAvailable"] = True
        replace_collection(value, "provenance", p)
    elif case == "whole_scope":
        value["manifest"]["scope"]["symbols"].pop()
    elif case == "budget":
        value["manifest"]["rawArchive"]["chunks"][0]["byteLength"] = 4 * 1024 * 1024 + 1
    elif case == "bool_number":
        rows[0]["close"] = True
        replace_collection(value, "rows", rows)
    elif case == "source_kind":
        value["manifest"]["sourceKind"] = "unknown"
    elif case == "receipt_count":
        value["manifest"]["rawArchive"]["receiptCount"] -= 1
    with pytest.raises(AuditError):
        audit_market_dataset(write_dir(tmp_path, value))


@pytest.mark.parametrize(
    "case",
    [
        "truncated",
        "trailer",
        "padding",
        "link",
        "traversal",
        "duplicate",
        "huge_member",
    ],
)
def test_strict_tar_rejects_nonprofile_bytes(source, tmp_path, case):
    raw = archive_bytes(source)
    if case == "truncated":
        raw = raw[:-1]
    elif case == "trailer":
        raw += b"\0"
    elif case == "padding":
        offset = 512 + len(encode(source["manifest"]))
        assert offset % 512
        raw = raw[:offset] + b"x" + raw[offset + 1 :]
    elif case == "link":
        header = bytearray(raw[:512])
        header[156] = ord("2")
        header[148:156] = b" " * 8
        header[148:156] = f"{sum(header):06o}\0 ".encode()
        raw = bytes(header) + raw[512:]
    elif case == "traversal":
        raw = (
            tar_header("../manifest.json", len(encode(source["manifest"]))) + raw[512:]
        )
    elif case == "duplicate":
        first_size = (
            512
            + len(encode(source["manifest"]))
            + (-len(encode(source["manifest"]))) % 512
        )
        raw = raw[:first_size] + raw
    elif case == "huge_member":
        raw = tar_header("manifest.json", 257 * 1024) + raw[512:]
    path = tmp_path / "bad.tar"
    path.write_bytes(raw)
    with pytest.raises(AuditError):
        audit_market_dataset(path)


@pytest.mark.parametrize(
    "case",
    [
        "extra",
        "leaf_symlink",
        "directory_symlink",
        "root_symlink",
        "wrong_pin",
        "duplicate_json",
    ],
)
def test_directory_and_pin_boundaries(source, tmp_path, case):
    path = write_dir(tmp_path, source)
    if case == "extra":
        (path / "secret.txt").write_text("not part of closure")
    elif case == "leaf_symlink":
        part = path / "parts/raw/0.bin"
        target = tmp_path / "raw"
        part.rename(target)
        part.symlink_to(target)
    elif case == "directory_symlink":
        part = path / "parts/raw"
        target = tmp_path / "raw"
        part.rename(target)
        part.symlink_to(target, target_is_directory=True)
    elif case == "root_symlink":
        link = tmp_path / "alias"
        link.symlink_to(path, target_is_directory=True)
        path = link
    elif case == "duplicate_json":
        f = path / "manifest.json"
        f.write_bytes(
            f.read_bytes().replace(b'"version":1', b'"version":1,"version":1', 1)
        )
    with pytest.raises((AuditError, OSError)):
        audit_market_dataset(
            path, expected_root="0" * 64 if case == "wrong_pin" else None
        )


def test_cli_zero_provider_and_no_overwrite(source, tmp_path):
    path = write_dir(tmp_path, source)
    output = tmp_path / "report.json"
    args = [
        sys.executable,
        str(ROOT / "scripts/audit-market-dataset.py"),
        str(path),
        "--expected-root",
        sha(encode(source["manifest"])),
        "--output",
        str(output),
    ]
    result = subprocess.run(args, check=True, capture_output=True, timeout=60)
    report = json.loads(result.stdout)
    assert report["providerCalls"] == 0 and report["modelFitted"] is False
    original = output.read_bytes()
    result = subprocess.run(args, capture_output=True, timeout=60)
    assert result.returncode == 2 and output.read_bytes() == original


def test_receipt_explicit_timezone_offset_is_preserved(source, tmp_path):
    value = copy.deepcopy(source)
    receipts = json.loads(value["chunks"]["receipts"]["0"])
    receipts[0]["retrievedAt"] = "2026-10-08T08:00:00+08:00"
    replace_collection(value, "receipts", receipts)
    assert audit_market_dataset(write_dir(tmp_path, value))["status"] == "PASS"


@pytest.mark.parametrize(
    "case",
    ["scope_alias_root", "catalog_root", "malformed_source_kind", "malformed_row_date"],
)
def test_internal_evidence_aliases_and_malformed_shapes(source, tmp_path, case):
    value = copy.deepcopy(source)
    if case in {"scope_alias_root", "catalog_root"}:
        if case == "scope_alias_root":
            value["plan"]["scope"]["scopeRoot"] = "0" * 64
            value["manifest"]["scope"]["scopeRoot"] = "0" * 64
        else:
            value["plan"]["catalog"]["snapshotHash"] = "0" * 64
        value["plan"]["planRoot"] = sha(
            encode({k: v for k, v in value["plan"].items() if k != "planRoot"})
        )
        value["manifest"]["planRoot"] = value["plan"]["planRoot"]
        provenance = json.loads(value["chunks"]["provenance"]["0"])
        provenance[0]["planRoot"] = value["plan"]["planRoot"]
        replace_collection(value, "provenance", provenance)
    elif case == "malformed_source_kind":
        value["manifest"]["sourceKind"] = {}
    else:
        rows = json.loads(value["chunks"]["rows"]["0"])
        rows[0]["trade_date"] = []
        replace_collection(value, "rows", rows)
    with pytest.raises(AuditError):
        audit_market_dataset(write_dir(tmp_path, value))


@pytest.fixture(scope="module")
def paired_source():
    # A full-year SYNTHETIC source supports the declared 61-session warmup and
    # terminal clock. Generating raw tables/normalization invokes no estimator.
    script = r"""
import importlib.util,json,uuid
from pathlib import Path
from atlas_quant.market_acquisition.normalize import build_publication
from atlas_quant.market_acquisition.protocol import encode,sha
spec=importlib.util.spec_from_file_location("fixture",Path("scripts/fixtures/market_source.py"))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
scope,plan,_=fixture.source_plan(2)
plan["fields"]=sorted(plan["fields"]+["pb"])
for symbol in scope["symbols"]:
    d={"provider":"TUSHARE_PRO","authorizationScope":plan["authorizationScope"],"apiName":"daily_basic","params":{"ts_code":symbol,"start_date":scope["start"],"end_date":scope["end"]},"fields":"ts_code,trade_date,pb","responseBytes":262144,"maxAttempts":1}
    plan["requests"].append({"ordinal":len(plan["requests"]),**d,"requestKey":sha(encode(d))})
plan["requests"].sort(key=lambda r:(r["params"].get("ts_code",""),["trade_cal","daily","adj_factor","daily_basic"].index(r["apiName"])))
for i,r in enumerate(plan["requests"]):r["ordinal"]=i
plan["budget"].update(declaredRequests=len(plan["requests"]),materializedRequests=len(plan["requests"]),rawResponseCeilingBytes=sum(r["responseBytes"] for r in plan["requests"]))
plan["planRoot"]=sha(encode({k:v for k,v in plan.items() if k!="planRoot"}))
provider=fixture.SyntheticMarketProvider({"allow_market_fixtures":True})
receipts={};chunks={}
for r in plan["requests"]:
    raw=encode({"code":0,"data":{"fields":r["fields"].split(","),"items":[]}}) if r["apiName"]=="daily_basic" else provider.call_once(r,maximum_bytes=r["responseBytes"]).raw
    receipts[r["requestKey"]]={"requestKey":r["requestKey"],"receiptId":str(uuid.uuid5(uuid.NAMESPACE_URL,r["requestKey"])),"raw":raw,"sha256":sha(raw),"byteLength":len(raw),"httpStatus":200,"retrievedAt":"2026-10-08T00:00:00Z","sourceKind":"fixture"}
manifest=build_publication({},plan,lambda r:receipts[r["requestKey"]],lambda n,i,b:chunks.__setitem__((n,i),b))
print(json.dumps({"scope":scope,"plan":plan,"manifest":manifest,"chunks":{n:{str(i):raw.decode() for (c,i),raw in chunks.items() if c==n} for n in ("rows","receipts","provenance","raw")}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "engine")},
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def paired_fixture(source, damage=None, clock="daily"):
    # Structural SYNTHETIC result only: no model is run or imported here.
    script = r"""
import fs from 'node:fs';
import {bundleFixture,canonical,hash} from './tests/fixtures/bundle-fixture.mjs';
const source=JSON.parse(fs.readFileSync(0,'utf8')), m=source.manifest, scope=source.scope;
const damage=process.argv[1],clock=process.argv[2];
const evidence={admissionProfile:'pooled_asset_1000_v1',marketDatasetRef:{datasetId:'22222222-2222-2222-2222-222222222222',datasetRoot:source.root,format:'atlas.quant.market_dataset',version:1},universeScopeRef:m.universeScopeRef,rowValueRoot:'7'.repeat(64)};
if(damage==='different_source')evidence.marketDatasetRef.datasetRoot='8'.repeat(64);
if(damage==='phantom_profile')evidence.admissionProfile='pooled_asset_999999_v9';
if(damage==='wrong_auto')evidence.admissionProfile='pooled_asset_1000_auto_candidate_v1';
if(['trend_auto','trend_auto_wrong_family'].includes(damage))evidence.admissionProfile='pooled_asset_1000_trend_auto_v1';
if(damage==='mean_auto_wrong_family')evidence.admissionProfile='pooled_asset_1000_auto_candidate_v1';
if(damage==='unverified_id')evidence.marketDatasetRef.datasetId='33333333-3333-3333-3333-333333333333';
const fixture=bundleFixture({count:1,rowsPerChunk:100,mutate:({forecast,report,snapshot,coverage})=>{
 const u=forecast.sourceStrategy.universe;
 for(const key of ['symbols','start','end','selection','snapshotHash','resolutionHash'])u[key]=scope[key];
 u.subsetPolicy='all';
 const s=forecast.sourceStrategy, observation=clock==='stride'?7:clock==='nested'?5:1;
 if(['trend_auto','trend_auto_wrong_family','mean_auto_wrong_family'].includes(damage)){
  s.model.estimator='auto';s.model.family=damage==='trend_auto_wrong_family'?'mean_reversion':'trend';
 }
 s.research.observationDays=observation;
 s.factors=[{id:'f1',expression:clock==='nested'?'lag(ts_mean(close,70),3)':'close',direction:1,role:'predictor'}];
 if(clock==='no_factors')s.factors=[];
 s.model.refitDays=20;s.validation.holdoutFraction=0.2;
 report.strategy=forecast.sourceStrategy;
 const start=clock==='nested'?73:61, horizon=s.target.horizonSessions, calendar=m.calendar;
 const holdout=calendar[start+Math.floor((calendar.length-start)*0.8)];
 const definition=symbol=>{const d={kind:'asset_price',symbols:[symbol],quantities:[1],unit:'CNY_adjusted_research_price',construction:'single_asset',formationStart:null,formationEnd:null,hedgeAudit:{}};return {id:'target_'+hash(canonical(d)).slice(0,24),...d};};
 forecast.targetDefinitions=scope.symbols.map(definition);
 forecast.hedgeFits=[];let lastConstruction=-100000;
 for(let t=start;t<calendar.length;t+=observation)if(t-lastConstruction>=s.model.refitDays){
  forecast.hedgeFits.push({date:calendar[t],informationCutoff:calendar[t-1],targetIds:[...scope.symbols].sort().map(s=>definition(s).id),status:'valid'});lastConstruction=t;
 }
 const template=forecast.rows[0];let sampleCount=0;
 forecast.rows=[];
 for(let t=start;t<calendar.length;t+=observation){sampleCount+=scope.symbols.length;if(calendar[t]<holdout)continue;
  for(const target of forecast.targetDefinitions){const entry=calendar[t+1]??null,end=calendar[t+1+horizon]??null;
   forecast.rows.push({...template,forecastId:'review-'+t+'-'+target.id,date:calendar[t],targetId:target.id,informationCutoff:calendar[t]+'_AFTER_CLOSE',entryDate:entry,targetDate:end,horizonSessions:horizon,labelMaturedAt:entry&&end?end:null,realizedEntry:entry?10:null,realizedFuture:end?11:null,forecastError:end?0:null,status:entry&&end?'valid':'invalid',invalidReason:entry&&end?null:'target_outside_available_calendar'});
  }
 }
 forecast.modelFits[0].fitDate=holdout;forecast.modelFits[0].labelEndMax='20231229';
 forecast.modelFits[0].trainStart='20230101';forecast.modelFits[0].trainEnd='20231229';
 coverage.holdoutStart=holdout;coverage.baselineRequired=true;
 coverage.origins=forecast.rows.map(r=>({date:r.date,targetId:r.targetId,entryDate:r.entryDate,targetDate:r.targetDate,inputValid:true}));
 forecast.diagnostics={...forecast.diagnostics,holdoutStart:holdout,holdoutEnd:calendar.at(-1),factorIncrement:{status:'available',baselineRows:structuredClone(forecast.rows),baselineModelFits:structuredClone(forecast.modelFits),baselineValidation:{holdoutStart:holdout,holdoutEnd:calendar.at(-1)}}};
 report.validation={holdoutStart:holdout,holdoutEnd:calendar.at(-1)};
 report.research.observationDays=observation;
 report.capacity={holdoutStart:holdout,symbols:scope.symbols,sampleRows:sampleCount,completeGridRows:calendar.length*scope.symbols.length,inputRows:m.rowCount,forecastRows:forecast.rows.length,baselineRequired:true};
 const provenance={marketSource:evidence,synthetic:true,source:'SYNTHETIC_MARKET_FIXTURE',tradingDates:m.calendar};
 report.provenance={...provenance,dataSha256:forecast.dataFingerprint};report.execution.enabled=false;
 snapshot.rows=Object.keys(source.chunks.rows).sort((a,b)=>+a-+b).flatMap(i=>JSON.parse(source.chunks.rows[i]));
 snapshot.provenance={...provenance,dataFingerprint:'d'.repeat(64)};snapshot.sourceDataFingerprint='d'.repeat(64);snapshot.fingerprintVersion='research_input_v1';
 if(damage==='missing_row')snapshot.rows.shift();
 if(damage==='changed_row')snapshot.rows[0].close+=0.125;
 if(damage==='null_to_zero')snapshot.rows.find(r=>r.pb===null).pb=0;
 if(damage==='reordered_rows')snapshot.rows.reverse();
 if(damage==='wrong_kind')report.provenance.synthetic=false;
 if(damage==='mismatched_assertion')snapshot.provenance.marketSource={...evidence,rowValueRoot:'9'.repeat(64)};
 if(damage==='prediction_arithmetic')forecast.rows[0].expectedChange=999;
 const baseline=forecast.diagnostics.factorIncrement;
 if(['half_pool','whole_day','tail'].includes(damage)){
  const firstDate=forecast.rows[0].date,firstTarget=forecast.targetDefinitions[0].id;
  const retain=r=>damage==='half_pool'?r.targetId===firstTarget:damage==='whole_day'?r.date!==firstDate:r.targetDate!==null;
  forecast.rows=forecast.rows.filter(retain);coverage.origins=coverage.origins.filter(retain);baseline.baselineRows=baseline.baselineRows.filter(retain);
 }
 if(damage==='duplicate_symbol')forecast.targetDefinitions[1]={...forecast.targetDefinitions[1],symbols:[scope.symbols[0]]};
 if(damage==='extra_target')forecast.targetDefinitions.push(definition('999999.SZ'));
 if(damage==='missing_target'){
  const keep=forecast.targetDefinitions[0].id;forecast.targetDefinitions.pop();
  forecast.rows=forecast.rows.filter(r=>r.targetId===keep);coverage.origins=coverage.origins.filter(r=>r.targetId===keep);baseline.baselineRows=baseline.baselineRows.filter(r=>r.targetId===keep);
 }
 if(damage==='hidden_baseline'){coverage.baselineRequired=false;delete baseline.baselineRows;delete baseline.baselineModelFits;delete baseline.baselineValidation;}
 if(damage==='coverage_clock')coverage.holdoutStart=calendar[calendar.indexOf(holdout)-1];
 if(damage==='report_clock')report.validation.holdoutStart=calendar[calendar.indexOf(holdout)-1];
 if(damage==='baseline_clock')baseline.baselineValidation.holdoutStart=calendar[calendar.indexOf(holdout)-1];
 if(damage==='end_clock')forecast.diagnostics.holdoutEnd=calendar.at(-2);
 if(damage==='endpoint')for(const rows of [forecast.rows,coverage.origins,baseline.baselineRows])rows[0].entryDate=calendar[calendar.indexOf(rows[0].date)+2];
 if(damage==='invalid_tail_valid')for(const rows of [forecast.rows,baseline.baselineRows]){const row=rows.find(r=>r.targetDate===null);row.status='valid';}
 if(damage==='shifted_declared_clock'){
  const shifted=calendar[calendar.indexOf(holdout)+1];coverage.holdoutStart=shifted;forecast.diagnostics.holdoutStart=shifted;report.validation.holdoutStart=shifted;baseline.baselineValidation.holdoutStart=shifted;report.capacity.holdoutStart=shifted;
  forecast.rows=forecast.rows.filter(r=>r.date>=shifted);coverage.origins=coverage.origins.filter(r=>r.date>=shifted);baseline.baselineRows=baseline.baselineRows.filter(r=>r.date>=shifted);
 }
 if(damage==='phase'){
  for(const rows of [forecast.rows,coverage.origins,baseline.baselineRows])for(const row of rows){const t=calendar.indexOf(row.date)-1;row.date=calendar[t];row.entryDate=calendar[t+1]??null;row.targetDate=calendar[t+1+horizon]??null;if('informationCutoff' in row)row.informationCutoff=row.date+'_AFTER_CLOSE';}
  for(const fits of [forecast.modelFits,baseline.baselineModelFits])fits[0].fitDate=holdout;
 }
 if(clock==='no_factors'){coverage.baselineRequired=false;report.capacity.baselineRequired=false;delete forecast.diagnostics.factorIncrement;}
 if(damage==='factor_shape')s.factors.push(1);
 if(damage==='capacity_shape')report.capacity=[];
 if(damage==='validation_shape')report.validation=[];
 if(damage==='selection_shape')forecast.diagnostics.selectionAudit=[];
 forecast.totalRows=forecast.rows.length;
}});
process.stdout.write(JSON.stringify({manifest:fixture.manifest,manifestText:fixture.manifestText,chunks:Object.fromEntries(fixture.chunks)}));
"""
    value = {**source, "root": sha(encode(source["manifest"]))}
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, damage or "valid", clock],
        cwd=ROOT,
        input=json.dumps(value),
        text=True,
        check=True,
        capture_output=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def write_result(tmp_path, value, tar=False):
    path = tmp_path / ("result.tar" if tar else "result")
    entries = [("manifest.json", value["manifestText"].encode())]
    for c in value["manifest"]["collections"]:
        entries.extend(
            (
                f"chunks/{c['id']}/{d['ordinal']}.json",
                value["chunks"][f"{c['id']}:{d['ordinal']}"].encode(),
            )
            for d in c["chunks"]
        )
    if tar:
        path.write_bytes(
            b"".join(
                tar_header(name, len(raw)) + raw + bytes((-len(raw)) % 512)
                for name, raw in entries
            )
            + bytes(1024)
        )
    else:
        path.mkdir()
        for name, raw in entries:
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return path


@pytest.mark.parametrize("tar", [False, True])
def test_paired_complete_result_audit_and_full_source_comparison(
    paired_source, tmp_path, tar
):
    source = paired_source
    dataset = write_dir(tmp_path, source)
    result = write_result(tmp_path, paired_fixture(source), tar)
    report = audit_market_dataset(
        dataset, expected_root=sha(encode(source["manifest"])), result_bundle=result
    )
    assert report["status"] == "PASS"
    paired = report["pairedResult"]
    assert (
        paired["bundleAudit"]["status"] == "passed"
        and paired["bundleAudit"]["forecastRows"] == 82
    )
    assert paired["fullSnapshotRowsVerified"] is True
    assert paired["snapshotRows"] == source["manifest"]["rowCount"]
    assert paired["fullAssetCoverageVerified"] is True
    assert paired["expectedForecastRows"] == 82
    assert paired["expectedOriginDates"] == 41
    assert paired["baselineRequired"] is True
    assert paired["sourceContentRootVerified"] is True
    assert (
        paired["rowValueRootInternallyConsistent"] is True
        and paired["rowValueRootRecomputed"] is False
    )
    assert (
        paired["ownershipVerified"] is False
        and paired["datasetIdAuthenticated"] is False
    )
    assert paired["serverAdmissionAuthenticated"] is False


def test_trend_auto_pair_preserves_complete_old_source_without_claiming_model_refit(paired_source, tmp_path):
    dataset = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, paired_fixture(paired_source, "trend_auto"))
    report = audit_market_dataset(dataset, expected_root=sha(encode(paired_source["manifest"])), result_bundle=result)
    assert report["status"] == "PASS"
    assert report["pairedResult"]["expectedForecastRows"] == 82
    assert report["pairedResult"]["fullSnapshotRowsVerified"] is True
    assert report["pairedResult"]["fullAssetCoverageVerified"] is True
    assert report["providerCalls"] == 0 and report["modelFitted"] is False


@pytest.mark.parametrize("damage", ["trend_auto_wrong_family", "mean_auto_wrong_family"])
def test_rehashed_result_cannot_swap_registered_auto_mechanism(paired_source, tmp_path, damage):
    dataset = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, paired_fixture(paired_source, damage))
    with pytest.raises(AuditError) as error:
        audit_market_dataset(dataset, expected_root=sha(encode(paired_source["manifest"])), result_bundle=result)
    assert error.value.code == "RESULT_PROFILE"


@pytest.mark.parametrize(
    "damage",
    [
        "different_source",
        "missing_row",
        "changed_row",
        "null_to_zero",
        "reordered_rows",
        "phantom_profile",
        "wrong_auto",
        "mismatched_assertion",
        "wrong_kind",
        "prediction_arithmetic",
    ],
)
def test_pair_rejects_individually_rehashed_but_unbound_or_invalid_result(
    paired_source, tmp_path, damage
):
    source = paired_source
    dataset = write_dir(tmp_path, source)
    result = write_result(tmp_path, paired_fixture(source, damage))
    with pytest.raises(AuditError):
        audit_market_dataset(dataset, result_bundle=result)


def test_pair_cannot_authenticate_dataset_id_and_cli_accepts_result_tar(
    paired_source, tmp_path
):
    source = paired_source
    dataset = write_dir(tmp_path, source)
    result = write_result(tmp_path, paired_fixture(source, "unverified_id"), tar=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/audit-market-dataset.py"),
            str(dataset),
            "--expected-root",
            sha(encode(source["manifest"])),
            "--result-bundle",
            str(result),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert (
        report["pairedResult"]["marketDatasetId"]
        == "33333333-3333-3333-3333-333333333333"
    )
    assert report["pairedResult"]["datasetIdAuthenticated"] is False


@pytest.mark.parametrize(
    "damage", ["tar_trailer", "tar_traversal", "directory_link", "directory_extra"]
)
def test_pair_result_input_strict_paths_and_archive_eof(
    paired_source, tmp_path, damage
):
    source = paired_source
    dataset = write_dir(tmp_path, source)
    result = write_result(
        tmp_path, paired_fixture(source), tar=damage.startswith("tar")
    )
    if damage == "tar_trailer":
        result.write_bytes(result.read_bytes() + b"\0")
    elif damage == "tar_traversal":
        raw = result.read_bytes()
        size = int(raw[124:135], 8)
        result.write_bytes(tar_header("../manifest.json", size) + raw[512:])
    elif damage == "directory_extra":
        (result / "extra").write_text("unexpected")
    else:
        file = result / "chunks/snapshotRows/0.json"
        external = tmp_path / "outside.json"
        file.rename(external)
        file.symlink_to(external)
    with pytest.raises((AuditError, OSError)):
        audit_market_dataset(dataset, result_bundle=result)


def test_paired_numeric_comparison_does_not_round_changed_integers():
    from market_dataset_audit import Checks, number

    with pytest.raises(AuditError, match="Integer loses precision"):
        number(2**53 + 1, Checks())
    assert number(2**53, Checks()) == 2**53
    assert encode(number(-0.0, Checks())) == encode(number(0, Checks()))


@pytest.mark.parametrize(
    "damage",
    [
        "half_pool",
        "whole_day",
        "tail",
        "duplicate_symbol",
        "extra_target",
        "missing_target",
        "hidden_baseline",
        "coverage_clock",
        "report_clock",
        "baseline_clock",
        "end_clock",
        "endpoint",
        "shifted_declared_clock",
    ],
)
def test_self_consistent_rehashed_result_cannot_remove_complete_source_grid(
    paired_source, tmp_path, damage
):
    from market_dataset_audit import open_result_bundle

    dataset = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, paired_fixture(paired_source, damage))
    # Rehashing every changed collection, document and manifest remains sufficient
    # for the unchanged legacy audit. The paired check must independently reject.
    with open_result_bundle(result) as (_, report):
        assert report["status"] == "passed"
    with pytest.raises(AuditError) as error:
        audit_market_dataset(dataset, result_bundle=result)
    assert error.value.code in {"RESULT_COVERAGE", "RESULT_TARGETS", "RESULT_CLOCK"}


@pytest.mark.parametrize(
    "clock,start,lookback", [("stride", 61, 0), ("nested", 73, 72)]
)
def test_origin_phase_and_nested_factor_warmup_use_complete_source_calendar(
    paired_source, tmp_path, clock, start, lookback
):
    dataset = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, paired_fixture(paired_source, clock=clock))
    report = audit_market_dataset(dataset, result_bundle=result)["pairedResult"]
    calendar = paired_source["manifest"]["calendar"]
    step = 7 if clock == "stride" else 5
    boundary = start + int((len(calendar) - start) * 0.8)
    expected = [i for i in range(start, len(calendar), step) if i >= boundary]
    assert expected[0] > boundary  # Catch resetting the sampling phase at holdout.
    assert report["holdoutStart"] == calendar[boundary]
    assert report["expectedForecastRows"] == 2 * len(expected)
    assert report["sampleStartIndex"] == start and report["factorLookback"] == lookback
    assert report["invalidTailOriginsRetained"] is True


def test_self_consistent_sampling_phase_shift_is_rejected(paired_source, tmp_path):
    from market_dataset_audit import open_result_bundle

    dataset = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, paired_fixture(paired_source, "phase", "stride"))
    with open_result_bundle(result) as (_, report):
        assert report["status"] == "passed"
    with pytest.raises(AuditError) as error:
        audit_market_dataset(dataset, result_bundle=result)
    assert error.value.code == "RESULT_COVERAGE"


def test_factor_free_full_grid_does_not_require_a_baseline(paired_source, tmp_path):
    dataset = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, paired_fixture(paired_source, clock="no_factors"))
    report = audit_market_dataset(dataset, result_bundle=result)["pairedResult"]
    assert report["fullAssetCoverageVerified"] is True
    assert report["expectedForecastRows"] == 82
    assert report["baselineRequired"] is False
    assert report["originalRequestedStrategyAuthenticated"] is False


@pytest.mark.parametrize(
    "damage", ["factor_shape", "capacity_shape", "validation_shape", "selection_shape"]
)
def test_malformed_declared_clock_shapes_return_audit_failure(
    paired_source, tmp_path, damage
):
    dataset = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, paired_fixture(paired_source, damage))
    with pytest.raises(AuditError):
        audit_market_dataset(dataset, result_bundle=result)


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("close", 0),
        ("lag(close,61)", 61),
        ("ts_mean(close,61)", 60),
        ("lag(ts_mean(close,70),3)", 72),
        ("max(delta(close,10),ts_std(returns(close,20),70))", 89),
        ("clip(-rank(ts_mean(close,20)),-1,1)", 19),
    ],
)
def test_independent_registered_causal_lookback_clock(expression, expected):
    from market_dataset_audit import Checks, factor_lookback

    assert factor_lookback(expression, ["close"], Checks()) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "unknown(close)",
        "lag(close,253)",
        "lag(close,True)",
        "lag(close,1,)",
        "lag(close,-1)",
        "close.__class__",
        "lag(close,252)+missing",
        "1e999+close",
        "(" * 129 + "close" + ")" * 129,
    ],
)
def test_independent_clock_rejects_unregistered_factor_syntax(expression):
    from market_dataset_audit import Checks, factor_lookback

    with pytest.raises(AuditError):
        factor_lookback(expression, ["close"], Checks())
