"""Financial bundle/2 persistence and delivery, distinct from bundle/1."""
from .bundle_spool import BundleSpool, deliver_bundle
from .financial_bundle_v2 import (CAPABILITY, COLLECTIONS, CHUNK_COUNT_LIMIT, sha, fail,
    FinancialGraphBundleReader, build_financial_graph_bundle, validate_manifest)


class FinancialGraphBundleSpool(BundleSpool):
    DIRECTORY = "financial-graph-bundles"
    AAD_TAG = b":financial-bundle-v2:"
    MAGIC = b"AQF2"

    @staticmethod
    def chunk_name(collection, ordinal):
        if collection not in COLLECTIONS or type(ordinal) is not int or not 0 <= ordinal < CHUNK_COUNT_LIMIT:
            fail("BUNDLE_FORMAT", "新版金融分片引用无效。")
        return f"{collection}-{ordinal}"

    def build(self, report, snapshot_raw, coverage, source_evidence):
        raw = build_financial_graph_bundle(report, snapshot_raw, coverage, source_evidence,
                                           self.write_chunk, self.read_chunk)
        self.write("manifest", raw)
        return {"bundleId": sha(raw), "_bundleKey": self.key, "_bundleFormat": CAPABILITY}

    def reader(self, expected_id=None):
        return FinancialGraphBundleReader(self.read("manifest"), self.read_chunk, expected_id)

    def import_manifest(self, raw, expected_id):
        validate_manifest(raw, expected_id)
        self.write("manifest", raw)


def deliver_graph_bundle(client, spool, payload, *, deadline):
    if payload.get("_bundleFormat") != CAPABILITY:
        fail("DELIVERY_INTEGRITY", "新版金融回传需要明确的 bundle/2 格式。")
    return deliver_bundle(client, spool, payload, deadline=deadline,
                          store_type=FinancialGraphBundleSpool, route_prefix="financial-graph-bundles")
