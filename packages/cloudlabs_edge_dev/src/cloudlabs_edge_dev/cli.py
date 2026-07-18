"""CLI entry: ``cloudlabs-edge``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cloudlabs-edge",
        description=(
            "Scaffold, doctor, check, and certify Cloud Labs Edge Contract v1 agents"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create cloudlabs_edge/ skeleton")
    p_init.add_argument(
        "dest",
        type=Path,
        nargs="?",
        default=Path("cloudlabs_edge"),
        help="Destination directory (default: ./cloudlabs_edge)",
    )
    p_init.add_argument(
        "--backend-id",
        default="stub.default",
        help="backend_id written into capabilities/bench",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite scaffold files even if dest is non-empty",
    )

    p_serve = sub.add_parser("serve-stub", help="Run reference Edge Contract stub")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8100)
    p_serve.add_argument("--backend-id", default="stub.default")

    p_doctor = sub.add_parser(
        "doctor",
        help="Local kit + edge-tree readiness (no live edge required)",
    )
    p_doctor.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Path to cloudlabs_edge/ (default: ./cloudlabs_edge or cwd)",
    )
    p_doctor.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON report",
    )

    p_check = sub.add_parser("check", help="Run conformance against a live edge URL")
    p_check.add_argument("url", help="Base URL, e.g. http://127.0.0.1:8100")
    p_check.add_argument(
        "--profile",
        choices=("stub", "skeleton", "hardware"),
        default="stub",
        help="Conformance depth (default: stub)",
    )
    p_check.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON report",
    )

    p_certify = sub.add_parser(
        "certify",
        help="Pre-register gate: doctor + live conformance (JSON report)",
    )
    p_certify.add_argument("url", help="Base URL of the running edge")
    p_certify.add_argument(
        "--profile",
        choices=("stub", "skeleton", "hardware"),
        default="stub",
        help="Conformance depth (default: stub)",
    )
    p_certify.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Local cloudlabs_edge/ tree for doctor (optional)",
    )
    p_certify.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write certify JSON report to this path",
    )
    p_certify.add_argument(
        "--json",
        action="store_true",
        help="Also print the full JSON report to stdout",
    )
    p_certify.add_argument(
        "--skip-doctor",
        action="store_true",
        help="Only run live conformance (skip local tree / kit checks)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "init":
        from cloudlabs_edge_dev.scaffold import init_edge

        try:
            path = init_edge(args.dest, backend_id=args.backend_id, force=args.force)
        except FileExistsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"Initialized Edge Contract skeleton at {path}")
        print("Next: uvicorn main:app --host 0.0.0.0 --port 8100")
        print("Then: cloudlabs-edge doctor --path", path)
        print("      cloudlabs-edge certify http://127.0.0.1:8100 --path", path)
        return 0

    if args.cmd == "serve-stub":
        from cloudlabs_edge_dev.stub_server import run

        run(host=args.host, port=args.port, backend_id=args.backend_id)
        return 0

    if args.cmd == "doctor":
        from cloudlabs_edge_dev.doctor import main_doctor

        return main_doctor(args.path, as_json=args.json)

    if args.cmd == "check":
        from cloudlabs_edge_dev.conformance import main_check

        return main_check(args.url, profile=args.profile, as_json=args.json)

    if args.cmd == "certify":
        from cloudlabs_edge_dev.certify import main_certify

        return main_certify(
            args.url,
            profile=args.profile,
            edge_path=args.path,
            out=args.out,
            skip_doctor=args.skip_doctor,
            as_json=args.json,
        )

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
