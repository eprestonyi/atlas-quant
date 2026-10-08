"""Fixed composition limits; no client-provided budget can enlarge them."""

from ..financial_runner.protocol import decode, digest, encode, identifier, sha
from ..runner import RunnerError

CAPABILITY = "research-dataset/1"
PROFILE = "financial_snapshot_view_50_v1"
LIMITS = {
    "inputMetadataBytes": 262144,
    "sourceManifestBytes": 524288,
    "sourceSnapshotBytes": 25165824,
    "sourceChunkBytes": 8388608,
    "registryEntryBytes": 262144,
    "registryTotalBytes": 33554432,
    "packageBytes": 25165824,
    "closureBytes": 67108864,
    "manifestBytes": 262144,
    "partBytes": 524288,
    "parts": 256,
    "components": 32,
    "financialInputs": 8,
    "marketRows": 110000,
    "symbols": 50,
    "leaseSeconds": 120,
    "heartbeatSeconds": 20,
    "deadlineSeconds": 600,
    "registryEntries": 2057,
}
PHASES = {"checking_sources", "deriving_scope", "composing_states", "writing_evidence"}


def fail(code, message="数据集任务未完成；请按错误代码检查冻结来源。"):
    raise RunnerError(code, message)


def require(value, code="DATASET_PROTOCOL", message="数据集请求或回执不符合固定协议。"):
    if not value:
        fail(code, message)


def keys(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected))


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, "DATASET_BYTE_BUDGET")
    return value
