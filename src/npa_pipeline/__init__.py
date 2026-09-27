"""npa-pipeline: поиск и скачивание официальных НПА."""

from npa_pipeline.models import MatchType, Query, Result, Status
from npa_pipeline.service import fetch_document

__all__ = [
    "MatchType",
    "Query",
    "Result",
    "Status",
    "fetch_document",
]
