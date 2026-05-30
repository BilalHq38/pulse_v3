from __future__ import annotations

import os
import re

PIPELINE_SERVICE_LABEL = "data-pipeline"
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _pipeline_schema() -> str:
    schema = (os.environ.get("DATA_PIPELINE_DB_SCHEMA") or "data_pipeline_service").strip() or "data_pipeline_service"
    if not _SCHEMA_RE.fullmatch(schema):
        raise ValueError(f"Invalid DATA_PIPELINE_DB_SCHEMA value: {schema}")
    return schema


PIPELINE_SCHEMA = _pipeline_schema()

RAW_EVENT_TABLE = "raw_events"
RAW_MESSAGE_TABLE = "raw_messages"
RAW_LEAD_TABLE = "raw_leads"
ANALYTICS_EVENT_TABLE = "analytics_events"
LEAD_METRICS_TABLE = "lead_metrics"
CONVERSATION_METRICS_TABLE = "conversation_metrics"
SENTIMENT_LOGS_TABLE = "sentiment_logs"

RAW_TABLES = {
    RAW_EVENT_TABLE,
    RAW_MESSAGE_TABLE,
    RAW_LEAD_TABLE,
}

PROCESSED_TABLES = {
    ANALYTICS_EVENT_TABLE,
    LEAD_METRICS_TABLE,
    CONVERSATION_METRICS_TABLE,
    SENTIMENT_LOGS_TABLE,
}

DEFAULT_BATCH_INTERVAL_SECONDS = 3600
DEFAULT_BATCH_INITIAL_DELAY_SECONDS = 15
