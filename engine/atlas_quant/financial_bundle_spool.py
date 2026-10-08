"""Independent authenticated financial transport, sharing fsync primitives only."""

from .bundle import sha, fail
from .bundle_spool import BundleSpool, deliver_bundle
from .financial_bundle import (
    CAPABILITY,
    FinancialBundleReader,
    build_financial_bundle,
    validate_manifest,
)


class FinancialBundleSpool(BundleSpool):
    DIRECTORY = "financial-bundles"
    AAD_TAG = b":financial-bundle-v1:"
    MAGIC = b"AQF1"

    def build(self, report, snapshot_raw, coverage, source_evidence):
        raw = build_financial_bundle(
            report,
            snapshot_raw,
            coverage,
            source_evidence,
            self.write_chunk,
            self.read_chunk,
        )
        self.write("manifest", raw)
        return {
            "bundleId": sha(raw),
            "_bundleKey": self.key,
            "_bundleFormat": CAPABILITY,
        }

    def reader(self, expected_id=None):
        return FinancialBundleReader(
            self.read("manifest"), self.read_chunk, expected_id
        )

    def import_manifest(self, raw, expected_id):
        validate_manifest(raw, expected_id)
        self.write("manifest", raw)


def deliver_financial_bundle(client, spool, payload, *, deadline):
    """Resume exact encrypted bytes; caller confirms terminal state before cleanup.

    QueueClient.bundle_chunk must admit only the registered namespace enum.
    Completion is separately POSTed to financial-bundles/complete, and the
    ordinary claim-intent terminal receipt still controls durable cleanup.
    """
    if payload.get("_bundleFormat") != CAPABILITY:
        fail("DELIVERY_INTEGRITY", "金融回传缺少明确的持久传输格式。")
    return deliver_bundle(
        client,
        spool,
        payload,
        deadline=deadline,
        store_type=FinancialBundleSpool,
        route_prefix="financial-bundles",
    )
