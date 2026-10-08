"""Explicit discriminators only; unknown mixtures reach the strict graph validator."""
from ..research_dataset.graph_v3.snapshot import RESEARCH_PROFILE


def requested(job):
    if job.get('dataSource')!='ready_dataset':return False
    reference=job.get('datasetRef')
    transport=job.get('resultTransport')
    return (job.get('admissionProfile')==RESEARCH_PROFILE
            or isinstance(reference,dict) and reference.get('version')==3
            or isinstance(transport,dict) and transport.get('format')=='atlas.quant.financial_bundle' and transport.get('version')==2)
