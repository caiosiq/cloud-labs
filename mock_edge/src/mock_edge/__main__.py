"""``python -m mock_edge`` — serve Edge Contract on :8100."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloud Labs mock edge (Edge Contract v1)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args(argv)

    import uvicorn
    from mock_edge.server.app import build_default_app

    uvicorn.run(build_default_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
