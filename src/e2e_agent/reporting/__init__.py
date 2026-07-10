"""Unified JSON, HTML and JUnit reporting."""

from .failure_taxonomy import normalize_failure
from .writers import write_report_bundle

__all__ = ["normalize_failure", "write_report_bundle"]
