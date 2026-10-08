"""Exact graph source protocol, independent of legacy composer capabilities."""
import re
from ..dataset_runner.protocol import LIMITS

CAPABILITY = "research-dataset-graph/1"
PROFILE = "financial_snapshot_graph_50_v1"
NAMESPACE = "dataset-graphs"
COMPONENT = re.compile(r"(?:registryEvidence|marketOrigin|marketDataset|researchColumns|schema|coverage|financialInput[0-7]|financialGraph[0-7])")
