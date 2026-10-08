"""Prepare complete deterministic Samples for a future shared F scheduler.

This module neither calls that scheduler nor grants an existing profile access.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from ..statistical_quant.targets import Samples
from .contract import digest, validate_declaration
from .features import _origin_features
from .research_contract import STATE_COLUMNS, validate_research_contract
from .targets import build_targets


@dataclass
class PairSamplePreparation:
    samples: Samples
    targets: dict
    research: dict
    evidence: dict


def build_samples(source, declaration, research):
    """Keep every T x origin, with independent state/features/label statuses."""
    research = validate_research_contract(research, source, declaration)
    spec = validate_declaration(declaration)
    targets = build_targets(spec, source.price_input)
    dates = source.domain["calendar"]
    feature_columns = [*STATE_COLUMNS, *("factor:" + f["id"] for f in research["factors"])]
    features, labels, meta, input_rows = [], [], [], []
    for row, feature, status in _origin_features(source, spec, research, targets):
        features.append(feature)
        labels.append([row["entry"]["normalizedChange"], row["exit"]["normalizedChange"]])
        meta.append({"date": row["origin"], "dateIndex": dates.index(row["origin"]),
                     "targetId": row["targetVersionId"], "pairId": row["pairId"],
                     "currentState": row["current"]["state"], "scale": row["current"]["grossScale"],
                     "entryDate": row["entry"]["date"], "targetDate": row["exit"]["date"],
                     "realizedEntry": row["entry"]["state"], "realizedFuture": row["exit"]["state"],
                     "inputValid": status["featureInputValid"], "eventObserved": False,
                     "targetStateValid": row["current"]["status"] == "complete",
                     "entryLabelStatus": row["entry"]["labelStatus"],
                     "exitLabelStatus": row["exit"]["labelStatus"],
                     "modelAvailable": False, **status})
        input_rows.append({"date": row["origin"], "targetId": row["targetVersionId"],
                           "prefixRoot": status["featurePrefixRoot"],
                           "X": [float(feature[k]) if math.isfinite(feature[k]) else None for k in feature_columns]})
    meta_columns = ["date", "dateIndex", "targetId", "pairId", "currentState", "scale", "entryDate", "targetDate",
                    "realizedEntry", "realizedFuture", "inputValid", "eventObserved", "targetStateValid",
                    "entryLabelStatus", "exitLabelStatus", "modelAvailable", "featureInputValid", "invalidReason",
                    "missingHistoryLegs", "missingFactorLegs", "featureCutoff", "featurePrefixRoot"]
    metadata = pd.DataFrame(meta, columns=meta_columns)
    for key in ("currentState", "scale", "realizedEntry", "realizedFuture"):
        metadata[key] = metadata[key].astype(float)
    for key in ("inputValid", "eventObserved", "targetStateValid", "modelAvailable", "featureInputValid"):
        metadata[key] = metadata[key].astype(bool)
    definitions = {t["targetVersionId"]: {**t, "id": t["targetVersionId"], "kind": "explicit_pair"}
                   for t in targets["targets"]}
    samples = Samples(pd.DataFrame(features, columns=feature_columns).astype(float),
                      pd.DataFrame(labels, columns=["entry", "exit"]).astype(float), metadata,
                      definitions, dates.index(spec["origins"][0]), list(dates), [])
    evidence = {"format": "atlas.quant.pair_samples.preparation", "version": 1,
                "status": "prepared_no_fit" if definitions else "empty_target_scope_no_fit",
                "researchRoot": research["researchRoot"], "source": source.evidence,
                "declarationRoot": spec["declarationRoot"], "targetScopeRoot": spec["targetScopeRoot"],
                "featureInputRoot": digest({"researchRoot": research["researchRoot"],
                                             "columns": feature_columns, "rows": input_rows}),
                "featureColumns": feature_columns, "coverage": targets["coverage"],
                "memberStates": targets["memberStates"], "targetTiming": spec["targetTiming"],
                "quantityProvenanceVerified": False, "pairSelectionLeakageVerified": False,
                "sourceAuthorityVerified": False, "historicalMembershipVerified": False,
                "historicalRevisionVintageVerified": False,
                "evaluationScope": research["evaluationScope"], "fits": 0,
                "quantityFits": 0, "providerCalls": 0, "cloudMutations": 0}
    return PairSamplePreparation(samples, targets, research, evidence)
