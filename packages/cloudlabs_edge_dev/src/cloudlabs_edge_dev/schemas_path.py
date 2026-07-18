"""Locate Edge Contract v1 JSON Schema files."""

from __future__ import annotations

import os
from pathlib import Path


def contract_schemas_dir() -> Path:
    """Return directory containing ``*.schema.json`` for contract v1.

    Order:
    1. ``CLOUDLABS_EDGE_SCHEMAS`` env
    2. Walk up from this package looking for ``schemas/edge_contract/v1``
    3. Walk up from cwd
    """
    env = os.environ.get("CLOUDLABS_EDGE_SCHEMAS", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p
        raise FileNotFoundError(f"CLOUDLABS_EDGE_SCHEMAS is not a directory: {p}")

    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "schemas" / "edge_contract" / "v1"
        if (candidate / "capabilities.schema.json").is_file():
            return candidate

    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "schemas" / "edge_contract" / "v1"
        if (candidate / "capabilities.schema.json").is_file():
            return candidate

    raise FileNotFoundError(
        "Could not find schemas/edge_contract/v1. "
        "Set CLOUDLABS_EDGE_SCHEMAS or run from the cloud-labs repo."
    )


def load_schema(name: str) -> dict:
    import json

    path = contract_schemas_dir() / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))
