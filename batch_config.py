"""Configuration binding shared by the pipeline and its standalone AI stages."""
from datetime import date as Date
from pathlib import Path
import os

from config_schema import ConfigError
from runtime_config import bind_process, get_store


def validate_date(date):
    try:
        if Date.fromisoformat(date).isoformat() != date:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise ConfigError("日期必须为 YYYY-MM-DD") from exc
    return date


def prepare_batch(date, keyword=None, adopt_existing=False, requested_version=None, output_root="output"):
    validate_date(date)
    requested = requested_version or os.environ.get("SERP_CONFIG_REVISION") or None
    snapshot, keywords = get_store().pin_batch(Path(output_root) / date, keyword, requested, adopt_existing)
    bind_process(snapshot)
    return snapshot, keywords
