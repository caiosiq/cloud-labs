"""``python -m mock_edge`` — serve ``mock_edge/cloudlabs_edge`` on :8100."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_edge_on_path() -> Path:
    """Put ``mock_edge/cloudlabs_edge`` first so ``main`` / ``adapters`` resolve."""
    mock_root = Path(__file__).resolve().parents[2]
    edge = mock_root / "cloudlabs_edge"
    edge_s = str(edge)
    if edge_s not in sys.path:
        sys.path.insert(0, edge_s)
    return edge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cloud Labs mock edge — Edge Contract via cloudlabs_edge/"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args(argv)

    edge = _ensure_edge_on_path()
    if not (edge / "main.py").is_file():
        print(f"error: missing {edge / 'main.py'}", file=sys.stderr)
        return 2

    import uvicorn
    from server.app import build_default_app

    uvicorn.run(build_default_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
