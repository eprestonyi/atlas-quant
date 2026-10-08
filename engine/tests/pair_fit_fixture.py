"""Fixed tiny Stage 2B unit fixture, unrelated to any real/capacity acceptance."""
from datetime import date

from test_pair_research_samples import A, B, C, RANK, archive
from atlas_quant.pair_research import declare_targets
from atlas_quant.pair_research.source import read_market_source
from atlas_quant.pair_research.research_contract import declare_research, research_origins

MODEL = {"family": "pair_reversion", "estimator": "auto", "trainWindow": 120, "refitDays": 20}
PREPROCESS = {"winsorize": True, "standardize": True, "decorrelation": "drop_correlated", "correlationThreshold": .9}
VALIDATION = {"holdoutFraction": .2, "minTrainDates": 40, "innerFolds": 2, "outerFolds": 2}
RESOURCES = {"maxFitAttempts": 182, "maxForecastRows": 25000, "maxWallSeconds": 120,
             "maxRssBytes": 1024 * 1024 * 1024, "maxResultBytes": 24 * 1024 * 1024}


def fixture(*, future=False, factors=None):
    def edit(row, t, symbol):
        if symbol == A:
            row["close"] -= 20
            row["open"] -= 20
        if future and t > 230:
            row["close"] *= 1.2 + int(symbol[:6]) * .03
            row["open"] *= 1.3 + int(symbol[:6]) * .02
            row["vol"] *= 2
    reader, pins, _, _ = archive(start=date(2024, 1, 1), end=date(2024, 12, 31),
                                 edit=edit, missing={(250, B)})
    source = read_market_source(reader, expected_domain=pins)
    dates = pins["calendar"]
    selected = RANK if factors is None else factors
    cutoff = dates[1]
    origins = research_origins(source, quantity_cutoff=cutoff, factors=selected, observation_days=1)
    pairs = [
        {"pairId": "AB_zero", "legs": [{"symbol": A, "quantity": 1}, {"symbol": B, "quantity": -2}]},
        {"pairId": "CB_negative", "legs": [{"symbol": C, "quantity": 1}, {"symbol": B, "quantity": -1}]},
    ]
    declaration = declare_targets(source.price_input, pairs, quantity_cutoff=cutoff,
                                  origins=origins, horizon_sessions=1)
    research = declare_research(source, declaration, factors=selected, observation_days=1)
    return source, declaration, research, reader


def old_fixture():
    source, _, _, reader = fixture()
    data, provenance = reader.research_input()
    strategy = {"schemaVersion": 2, "name": "Stage2B tiny synthetic OLD full differential",
                "universe": {k: source.domain[k] for k in ("symbols", "start", "end")},
                "research": {"mode": "statistical_quant", "observationDays": 1},
                "target": {"kind": "asset_price", "horizonSessions": 1},
                "model": {**MODEL, "family": "mean_reversion"}, "preprocess": dict(PREPROCESS),
                "validation": dict(VALIDATION), "execution": {"enabled": False}, "factors": RANK}
    return strategy, data, provenance
