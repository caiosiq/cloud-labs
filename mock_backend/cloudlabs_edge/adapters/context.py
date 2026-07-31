"""Process-local binding of the mock teaching host for filled adapters."""

from __future__ import annotations

from typing import Any, Optional, Set

_lab: Any = None
live_active: Set[str] = set()
teleop_active: Set[str] = set()


def bind_lab(lab: Any) -> None:
    """Attach the MockLabCommunicator (or RuntimeLabProxy) for this process."""
    global _lab
    _lab = lab


def get_lab() -> Any:
    """Return the bound lab host, bootstrapping if needed."""
    global _lab
    if _lab is None:
        from mock_backend.bootstrap import bootstrap_host

        host, _rm = bootstrap_host()
        _lab = host
    return _lab


def tag_from_args(args: dict[str, Any], default: str = "") -> str:
    return str(args.get("tag_id") or args.get("target_id") or default)


async def run_uc(action: str, args: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run one UC primitive on the bound host via coordinator dispatch."""
    from lab_model.language.primitives.dispatch import (
        execute_validated_command,
        parse_command_payload,
    )

    args = dict(args or {})
    command: dict[str, Any] = {
        "action": action,
        "target_id": args.get("tag_id") or args.get("target_id") or "",
        "parameters": {
            k: v for k, v in args.items() if k not in ("tag_id", "target_id", "channel")
        },
    }
    if args.get("channel") is not None:
        command["channel"] = args["channel"]
    if not command["target_id"]:
        command.pop("target_id", None)
    cmd = parse_command_payload(command)
    result = await execute_validated_command(get_lab(), cmd)
    return result if isinstance(result, dict) else {"status": "ok"}
