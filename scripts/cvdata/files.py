"""Generated YAML files."""

from pathlib import Path

import yaml


def without_nulls(value):
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_nulls(item) for item in value]
    return value


def write_yaml_list(path: Path, rows: list[dict], header: str = "") -> None:
    """Rows are saved as standard YAML; missing values are omitted."""
    text = yaml.safe_dump(without_nulls(rows), allow_unicode=True, sort_keys=False, width=100)
    path.write_text(header + text, encoding="utf-8")
