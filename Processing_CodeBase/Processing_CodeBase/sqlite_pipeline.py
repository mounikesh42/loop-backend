#!/usr/bin/env python3
"""Root wrapper to run the SQLite pipeline CLI from repository root."""
from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

root = Path(__file__).resolve().parent
script_path = root / "scripts" / "sqlite_pipeline.py"

spec = spec_from_file_location("processing_sqlite_pipeline", script_path)
if spec is None or spec.loader is None:
    raise SystemExit(f"Unable to load wrapper module from {script_path}")
module = module_from_spec(spec)
assert module is not None and spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

if __name__ == "__main__":
    raise SystemExit(module.main())
