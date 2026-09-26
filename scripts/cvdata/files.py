"""Writing the generated data files."""

from __future__ import annotations

from pathlib import Path


def yaml_str(value: str) -> str:
    """Double-quoted YAML scalar, safe for arbitrary text."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_yaml_list(path: Path, rows: list[dict]) -> None:
    """Write rows as a YAML list, with lists of mappings (such as authors) in block style."""
    lines = []
    for row in rows:
        first = True
        for key, value in row.items():
            if value is None:
                continue
            prefix = "- " if first else "  "
            if isinstance(value, list):
                # Block mappings: the form that Prettier keeps (make check).
                lines.append(f"{prefix}{key}:")
                for item in value:
                    fields = [(k, v) for k, v in item.items() if v is not None]
                    for i, (k, v) in enumerate(fields):
                        lines.append(f"    {'- ' if i == 0 else '  '}{k}: {yaml_str(str(v))}")
            else:
                text = str(value) if isinstance(value, int) and not isinstance(value, bool) else yaml_str(str(value))
                lines.append(f"{prefix}{key}: {text}")
            first = False
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
