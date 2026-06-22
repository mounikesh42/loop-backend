#!/usr/bin/env python3
"""Shared helpers for the Check Point PPK pipeline.

Every stage writes the same envelope shape (template rule 2) and obeys the
determinism rules (rule 3): sort_keys on output, no timestamps inside the
data block. These helpers are the single home for that contract.

Lifted as-is from the GCP PPK build (subsystem-agnostic).
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    """ISO-8601 UTC timestamp with 6-digit microseconds, e.g.
    2026-05-30T19:30:00.123456Z. Only ever used in an envelope's
    generated_at - never inside a data block."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_config(config_path) -> dict:
    with Path(config_path).open(encoding="utf-8") as fh:
        return json.load(fh)


def load_spec(root: Path, config: dict) -> dict:
    with (root / config["spec_file"]).open(encoding="utf-8") as fh:
        return json.load(fh)


def resolve_path(root: Path, path_value) -> Path:
    """Resolve a config/inventory path.

    Relative paths are anchored at the project root. Absolute paths are kept
    absolute so uploaded inputs can live outside the application code folder.
    """
    path = Path(path_value)
    if path.is_absolute():
        return path
    return root / path


def display_path(path: Path, root: Path) -> str:
    """Return a root-relative path when possible, otherwise an absolute path."""
    path = Path(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def make_envelope(stage: str, data: dict, config: dict, spec_version: str) -> dict:
    """Template rule 2 envelope. spec_version comes from the spec's
    _meta.version, not the config, so the artifact records what was
    actually scored against."""
    return {
        "spec_version": spec_version,
        "config_used": config,
        "generated_at": now_iso(),
        "stage": stage,
        "data": data,
    }


def write_envelope(out_path: Path, envelope: dict) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
