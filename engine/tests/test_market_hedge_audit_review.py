"""Whole-pool raw construction references; stdlib audit, no model fit or network."""

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from market_dataset_audit import (
    AuditError,
    Checks,
    audit_market_dataset,
    open_result_bundle,
    validate_asset_hedge_fits,
)
from test_market_dataset_audit import (
    paired_source,
    paired_fixture,
    write_dir,
    write_result,
)


class RawAudit:
    def __init__(self, rows, count=None):
        self.raw = rows
        self.total = len(rows) if count is None else count

    def count(self, name):
        assert name == "hedgeFits"
        return self.total

    def rows(self, name):
        assert name == "hedgeFits"
        return iter(self.raw)


def target_id(symbol):
    # Separate literal contract, never derive expected IDs from uploaded targets.
    raw = json.dumps(
        {
            "kind": "asset_price",
            "symbols": [symbol],
            "quantities": [1],
            "unit": "CNY_adjusted_research_price",
            "construction": "single_asset",
            "formationStart": None,
            "formationEnd": None,
            "hedgeAudit": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "target_" + hashlib.sha256(raw).hexdigest()[:24]


def calendar():
    first = date(2024, 1, 1)
    return [
        (first + timedelta(days=n)).strftime("%Y%m%d")
        for n in range(366)
        if (first + timedelta(days=n)).weekday() < 5
    ]


def fit_rows(symbols, positions):
    days = calendar()
    return [
        {
            "date": days[t],
            "informationCutoff": days[t - 1],
            "targetIds": [target_id(s) for s in sorted(symbols)],
            "status": "valid",
        }
        for t in positions
    ]


def verify(rows, symbols, *, start=61, observation=1, refit=20, count=None):
    return validate_asset_hedge_fits(
        RawAudit(rows, count), calendar(), symbols, start, observation, refit, Checks()
    )


def test_all_1000_raw_targets_survive_every_preholdout_and_tail_refit():
    symbols = [f"{100000 + n:06d}.SZ" for n in range(1000)]
    positions = list(range(61, 262, 20))
    rows = fit_rows(symbols, positions)
    assert len(rows) == 11 and len(rows[0]["targetIds"]) == 1000
    assert len(json.dumps(rows[0])) > 8192  # Raw data never becomes a compact index.
    report = verify(rows, symbols)
    assert report == {
        "hedgeFitClockVerified": True,
        "fullHedgeTargetReferencesVerified": True,
        "expectedHedgeFits": 11,
        "hedgeFitTargetCount": 1000,
    }
    assert rows[-1]["date"] == calendar()[-1] and rows[-1]["status"] == "valid"
    for location in (0, 5, 10):
        damaged = deepcopy(rows)
        damaged[location]["targetIds"].pop()
        with pytest.raises(AuditError, match="complete ordered"):
            verify(damaged, symbols)


@pytest.mark.parametrize(
    "start,observation,positions",
    [
        (61, 7, list(range(61, 262, 21))),
        (73, 5, list(range(73, 262, 20))),
        (61, 60, [61, 121, 181, 241]),
    ],
)
def test_refits_count_full_calendar_sessions_but_only_happen_on_sample_ticks(
    start, observation, positions
):
    symbols = ["100000.SZ", "100001.SZ"]
    rows = fit_rows(symbols, positions)
    report = verify(rows, symbols, start=start, observation=observation)
    assert report["expectedHedgeFits"] == len(positions)
    wrong = deepcopy(rows)
    wrong[1]["informationCutoff"] = calendar()[positions[1] - observation]
    with pytest.raises(AuditError):
        verify(wrong, symbols, start=start, observation=observation)


def test_hedge_refs_use_symbol_order_not_hash_order_and_never_accept_compact_raw():
    symbols = ["100002.SZ", "100000.SZ", "100001.SZ"]
    rows = fit_rows(symbols, range(61, 262, 20))
    verify(rows, symbols)
    assert rows[0]["targetIds"] != sorted(rows[0]["targetIds"])
    wrong = deepcopy(rows)
    wrong[0]["targetIds"].sort()
    with pytest.raises(AuditError):
        verify(wrong, symbols)
    compact = deepcopy(rows)
    del compact[0]["targetIds"]
    compact[0].update(targetIndexPolicy="source_asset_targets_v1", targetCount=3)
    with pytest.raises(AuditError):
        verify(compact, symbols)


@pytest.mark.parametrize("refit", [True, 0, 19, 127, 20.0, "20"])
def test_declared_market_refit_range_and_type_remain_strict(refit):
    with pytest.raises(AuditError) as error:
        verify(fit_rows(["100000.SZ"], range(61, 262, 20)), ["100000.SZ"], refit=refit)
    assert error.value.code == "RESULT_CLOCK"


def test_raw_iterator_must_match_count_even_when_metadata_claims_complete():
    symbols = ["100000.SZ"]
    rows = fit_rows(symbols, range(61, 262, 20))
    for bad in [rows[:-1], rows + rows[-1:]]:
        with pytest.raises(AuditError):
            verify(bad, symbols, count=len(rows))


def rehash_result(value, damage):
    """Materialize public fixture documents and rebuild every affected hash."""
    script = r"""
import fs from 'node:fs';
import {bundleFixture,canonical} from './tests/fixtures/bundle-fixture.mjs';
const input=JSON.parse(fs.readFileSync(0,'utf8')), value=input.value, damage=input.damage;
const collection=name=>{
 const c=value.manifest.collections.find(c=>c.id===name);
 return '['+c.chunks.map(d=>value.chunks[name+':'+d.ordinal].slice(1,-1)).join(',')+']';
};
const document=name=>value.manifest.documents[name].parts.map(p=>{
 if('literal' in p)return p.literal;
 if('collection' in p)return collection(p.collection);
 return canonical({artifactId:p.wrapArtifactId,...JSON.parse(document('forecast'))});
}).join('');
const docs=Object.fromEntries(['forecast','report','snapshot','coverage'].map(n=>[n,JSON.parse(document(n))]));
const fits=docs.forecast.hedgeFits;
if(damage==='empty')fits.splice(0);
if(damage==='missing_preholdout')fits.shift();
if(damage==='holdout_only')docs.forecast.hedgeFits=fits.filter(f=>f.date>=docs.coverage.holdoutStart);
if(damage==='missing_tail')fits.pop();
if(damage==='duplicate_fit')fits[1]=structuredClone(fits[0]);
if(damage==='reverse_fit_order')fits.reverse();
if(damage==='missing_target')fits[0].targetIds.pop();
if(damage==='duplicate_target')fits[0].targetIds[1]=fits[0].targetIds[0];
if(damage==='reverse_target_order')fits[0].targetIds.reverse();
if(damage==='extra_target')fits[0].targetIds.push('target_'+ '0'.repeat(24));
if(damage==='all_partial_targets')for(const f of fits)f.targetIds=f.targetIds.slice(0,1);
if(damage==='wrong_cutoff')fits[0].informationCutoff=fits[0].date;
if(damage==='holdout_rephase')fits[0].date=docs.coverage.holdoutStart;
if(damage==='incomplete_formation')fits.at(-1).status='incomplete_formation';
if(damage==='extra_raw_key')fits[0].targetCount=fits[0].targetIds.length;
if(damage==='compact_raw')for(const f of fits){f.targetCount=f.targetIds.length;delete f.targetIds;}
const out=bundleFixture({sourceForecast:docs.forecast,rowsPerChunk:100,mutate:({report,snapshot,coverage})=>{
 Object.assign(report,docs.report);Object.assign(snapshot,docs.snapshot);Object.assign(coverage,docs.coverage);
}});
process.stdout.write(JSON.stringify({manifest:out.manifest,manifestText:out.manifestText,chunks:Object.fromEntries(out.chunks)}));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        input=json.dumps({"value": value, "damage": damage}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def complete_result(paired_source):
    return paired_fixture(paired_source)


def test_rebuilt_complete_pair_accepts_full_raw_refs_with_invalid_tail(
    paired_source, complete_result, tmp_path
):
    source = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, rehash_result(complete_result, "unchanged"))
    report = audit_market_dataset(source, result_bundle=result)["pairedResult"]
    assert report["expectedHedgeFits"] == 11 and report["hedgeFitTargetCount"] == 2
    assert report["fullHedgeTargetReferencesVerified"] is True
    assert report["hedgeFitClockVerified"] is True
    assert report["invalidTailOriginsRetained"] is True


@pytest.mark.parametrize(
    "damage",
    [
        "empty", "missing_preholdout", "holdout_only", "missing_tail", "duplicate_fit",
        "reverse_fit_order", "missing_target", "duplicate_target", "reverse_target_order",
        "extra_target", "all_partial_targets", "wrong_cutoff", "holdout_rephase",
        "incomplete_formation", "extra_raw_key", "compact_raw",
    ],
)
def test_fully_rehashed_result_cannot_discard_or_rewrite_raw_construction_refs(
    paired_source, complete_result, tmp_path, damage
):
    source = write_dir(tmp_path, paired_source)
    result = write_result(tmp_path, rehash_result(complete_result, damage))
    # Transport, arithmetic and legacy reference checks still pass; the new
    # independently source-derived construction audit must detect the omission.
    with open_result_bundle(result) as (_, report):
        assert report["status"] == "passed"
    with pytest.raises(AuditError) as error:
        audit_market_dataset(source, result_bundle=result)
    assert error.value.code == "RESULT_HEDGE_REFERENCES"
