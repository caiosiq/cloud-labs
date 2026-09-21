"""Server-side parser for the remote Cloud Labs Command Console language.

The browser Command Console parses the same concise verbs in JavaScript.  This
module deliberately produces the existing ``POST /api/command`` envelopes so
the gateway remains a client of Cloud Labs rather than a second control plane.
"""

from __future__ import annotations

import json
import math
import re
import shlex
from dataclasses import dataclass, field
from typing import Any


class CommandSyntaxError(ValueError):
    """Raised when a console line is unknown or malformed."""


@dataclass(frozen=True)
class ParsedLine:
    """Normalized representation of one console line."""

    kind: str
    command: dict[str, Any] | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


COMPACT_COMMANDS: tuple[str, ...] = (
    "help",
    "capabilities",
    "state [--json]",
    "tunables <tag|name>",
    "measurables <tag|name>",
    "record <tag|name>",
    "simreset list|current|default|<preset>",
    "simsave <preset> [--overwrite]",
    "simshow current|default|<preset>",
    "simwrite <preset> [--overwrite] <json>",
    "simcomponent list",
    "simcomponent nexttag",
    "simcomponent show <tag>",
    "simcomponent define <tag> <json>",
    "simcomponent configure <tag> <json>",
    "simcomponent reset <tag>",
    "simcomponent insert <tag> <x_mm> <y_mm> <rotation_deg>",
    "simcomponent insert <tag> storage <slot_i> <slot_j>",
    "simcomponent remove <tag>",
    "simcomponent delete <tag>",
    "simclear [table|all]",
    "move <tag|name> <x_mm> <y_mm> <rotation_deg>",
    "store <tag|name> [slot_i slot_j]",
    "place <tag|name> <x_mm> <y_mm> <rotation_deg>",
    "motor <tag|name> <motor_id> <distance>",
    "motorhome <tag|name> <motor_id>",
    "motorset0 <tag|name> <motor_id>",
    "pick <tag|name>",
    "hover <tag|name> <x_mm> <y_mm> <rotation_deg> <z_mm>",
    "placehover <tag|name> <x_mm> <y_mm> <rotation_deg>",
    "confirmhold <tag|name>",
)

_PRESET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_TAG_RE = re.compile(r"^tag_[1-9][0-9]*$")


def _number(value: str, label: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise CommandSyntaxError(f"{label} must be a number") from exc
    if not math.isfinite(result):
        raise CommandSyntaxError(f"{label} must be finite")
    return result


def _integer(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise CommandSyntaxError(f"{label} must be an integer") from exc


def _require_count(tokens: list[str], expected: set[int], usage: str) -> None:
    if len(tokens) not in expected:
        raise CommandSyntaxError(f"Usage: {usage}")


def _preset_name(value: str) -> str:
    if not _PRESET_RE.fullmatch(value):
        raise CommandSyntaxError(
            "preset names must contain only letters, numbers, '_' or '-'"
        )
    return value


def _tag_id(value: str) -> str:
    if not _TAG_RE.fullmatch(value):
        raise CommandSyntaxError(
            "tag_id must be tag_<positive integer>, for example tag_23"
        )
    return value


def _component_command(
    action: str,
    component_ref: str,
    parameters: dict[str, Any] | None = None,
) -> ParsedLine:
    return ParsedLine(
        "command",
        command={
            "action": action,
            "target_ref": component_ref,
            "parameters": parameters or {},
        },
    )


def parse_console_line(line: str) -> ParsedLine:
    """Parse one Command Console line without performing network access."""

    raw = str(line or "").strip()
    if not raw:
        raise CommandSyntaxError("command must not be empty")

    if re.match(r"^(?:simwrite|sim_write)\b", raw, flags=re.IGNORECASE):
        match = re.fullmatch(
            r"(?:simwrite|sim_write)\s+"
            r"([A-Za-z0-9][A-Za-z0-9_-]{0,63})"
            r"(?:\s+(--overwrite))?\s+([\s\S]+)",
            raw,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise CommandSyntaxError(
                "Usage: simwrite <preset> [--overwrite] <json_object>"
            )
        try:
            document = json.loads(match.group(3))
        except json.JSONDecodeError as exc:
            raise CommandSyntaxError(
                f"simwrite JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
            ) from exc
        if not isinstance(document, dict):
            raise CommandSyntaxError("simwrite JSON must be an object")
        return ParsedLine(
            "preset_write",
            arguments={
                "name": _preset_name(match.group(1)),
                "overwrite": match.group(2) is not None,
                "document": document,
            },
        )

    if re.match(r"^simcomponent\s+(?:define|configure)\b", raw, flags=re.IGNORECASE):
        match = re.fullmatch(
            r"simcomponent\s+(define|configure)\s+"
            r"(tag_[1-9][0-9]*)\s+([\s\S]+)",
            raw,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise CommandSyntaxError(
                "Usage: simcomponent define|configure <tag_id> <json_object>"
            )
        try:
            definition = json.loads(match.group(3))
        except json.JSONDecodeError as exc:
            raise CommandSyntaxError(
                f"simcomponent JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
            ) from exc
        if not isinstance(definition, dict):
            raise CommandSyntaxError("simcomponent JSON must be an object")
        return ParsedLine(
            f"component_{match.group(1).lower()}",
            arguments={"tag_id": _tag_id(match.group(2)), "definition": definition},
        )

    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise CommandSyntaxError(str(exc)) from exc
    if not tokens:
        raise CommandSyntaxError("command must not be empty")

    verb = tokens[0].lower()
    if verb in {"help", "?"}:
        _require_count(tokens, {1}, "help")
        return ParsedLine("help")
    if verb in {"capabilities", "commands"}:
        _require_count(tokens, {1}, "capabilities")
        return ParsedLine("capabilities")
    if verb in {"state", "labstate"}:
        _require_count(tokens, {1, 2}, "state [--json]")
        if len(tokens) == 2 and tokens[1] != "--json":
            raise CommandSyntaxError("Usage: state [--json]")
        return ParsedLine("state", arguments={"raw": len(tokens) == 2})
    if verb in {"tunables", "get_tunables"}:
        _require_count(tokens, {2}, "tunables <tag|name>")
        return ParsedLine("tunables", arguments={"component_ref": tokens[1]})
    if verb in {"measurables", "get_measurables"}:
        _require_count(tokens, {2}, "measurables <tag|name>")
        return ParsedLine("measurables", arguments={"component_ref": tokens[1]})
    if verb in {"record", "record_measurables"}:
        _require_count(tokens, {2}, "record <tag|name>")
        return ParsedLine("record", arguments={"component_ref": tokens[1]})

    if verb == "simreset":
        _require_count(tokens, {2}, "simreset list|current|default|<preset>")
        selector = tokens[1]
        if selector.lower() == "list":
            return ParsedLine("preset_list")
        if selector.lower() not in {"current", "default"}:
            selector = _preset_name(selector)
        return ParsedLine("preset_load", arguments={"selector": selector})
    if verb in {"simshow", "sim_show"}:
        _require_count(tokens, {2}, "simshow current|default|<preset>")
        selector = tokens[1]
        if selector.lower() not in {"current", "default"}:
            selector = _preset_name(selector)
        return ParsedLine("preset_show", arguments={"selector": selector})
    if verb == "simsave":
        _require_count(tokens, {2, 3}, "simsave <preset> [--overwrite]")
        if len(tokens) == 3 and tokens[2] != "--overwrite":
            raise CommandSyntaxError("simsave only supports --overwrite")
        return ParsedLine(
            "preset_save",
            arguments={
                "name": _preset_name(tokens[1]),
                "overwrite": len(tokens) == 3,
            },
        )

    if verb in {"simcomponent", "simcomp"}:
        if len(tokens) < 2:
            raise CommandSyntaxError(
                "Usage: simcomponent list|nexttag|show|define|configure|reset|insert|remove|delete ..."
            )
        action = tokens[1].lower()
        if action == "list":
            _require_count(tokens, {2}, "simcomponent list")
            return ParsedLine("component_list")
        if action == "nexttag":
            _require_count(tokens, {2}, "simcomponent nexttag")
            return ParsedLine("component_next_tag")
        if action == "show":
            _require_count(tokens, {3}, "simcomponent show <tag>")
            return ParsedLine("component_show", arguments={"tag_id": _tag_id(tokens[2])})
        if action == "reset":
            _require_count(tokens, {3}, "simcomponent reset <tag>")
            return ParsedLine("component_reset", arguments={"tag_id": _tag_id(tokens[2])})
        if action == "remove":
            _require_count(tokens, {3}, "simcomponent remove <tag>")
            return ParsedLine("component_remove", arguments={"tag_id": _tag_id(tokens[2])})
        if action == "delete":
            _require_count(tokens, {3}, "simcomponent delete <tag>")
            return ParsedLine("component_delete", arguments={"tag_id": _tag_id(tokens[2])})
        if action == "insert":
            _require_count(
                tokens,
                {6},
                "simcomponent insert <tag> <x> <y> <rotation> | "
                "simcomponent insert <tag> storage <i> <j>",
            )
            tag = _tag_id(tokens[2])
            if tokens[3].lower() == "storage":
                return ParsedLine(
                    "component_insert",
                    arguments={
                        "tag_id": tag,
                        "payload": {
                            "storage_slot": {
                                "i": _integer(tokens[4], "slot_i"),
                                "j": _integer(tokens[5], "slot_j"),
                            }
                        },
                    },
                )
            return ParsedLine(
                "component_insert",
                arguments={
                    "tag_id": tag,
                    "payload": {
                        "x": _number(tokens[3], "x"),
                        "y": _number(tokens[4], "y"),
                        "rotation": _number(tokens[5], "rotation"),
                    },
                },
            )
        raise CommandSyntaxError(
            "Usage: simcomponent list|nexttag|show|define|configure|reset|insert|remove|delete ..."
        )

    if verb == "simclear":
        _require_count(tokens, {1, 2}, "simclear [table|all]")
        scope = tokens[1].lower() if len(tokens) == 2 else "table"
        if scope not in {"table", "all"}:
            raise CommandSyntaxError("simclear scope must be table or all")
        return ParsedLine("component_clear", arguments={"scope": scope})

    if verb == "move":
        _require_count(tokens, {5}, "move <tag|name> <x> <y> <rotation>")
        return _component_command(
            "MOVE_COMPONENT",
            tokens[1],
            {
                "target_x": _number(tokens[2], "x"),
                "target_y": _number(tokens[3], "y"),
                "rotation": _number(tokens[4], "rotation"),
            },
        )
    if verb == "store":
        _require_count(tokens, {2, 4}, "store <tag|name> [slot_i slot_j]")
        parameters: dict[str, Any] = {}
        if len(tokens) == 4:
            parameters = {
                "slot_i": _integer(tokens[2], "slot_i"),
                "slot_j": _integer(tokens[3], "slot_j"),
            }
        return _component_command("STORE_COMPONENT", tokens[1], parameters)
    if verb == "place":
        _require_count(tokens, {5}, "place <tag|name> <x> <y> <rotation>")
        return _component_command(
            "PLACE_FROM_STORAGE",
            tokens[1],
            {
                "target_x": _number(tokens[2], "x"),
                "target_y": _number(tokens[3], "y"),
                "rotation": _number(tokens[4], "rotation"),
            },
        )
    if verb == "motor":
        _require_count(tokens, {4}, "motor <tag|name> <motor_id> <distance>")
        return _component_command(
            "MOVE_MOTOR",
            tokens[1],
            {
                "motor_id": _integer(tokens[2], "motor_id"),
                "distance": _number(tokens[3], "distance"),
            },
        )
    if verb == "motorhome":
        _require_count(tokens, {3}, "motorhome <tag|name> <motor_id>")
        return _component_command(
            "MOTOR_SEND_HOME",
            tokens[1],
            {"motor_id": _integer(tokens[2], "motor_id")},
        )
    if verb == "motorset0":
        _require_count(tokens, {3}, "motorset0 <tag|name> <motor_id>")
        return _component_command(
            "MOTOR_SET_ZERO",
            tokens[1],
            {"motor_id": _integer(tokens[2], "motor_id")},
        )
    if verb == "pick":
        _require_count(tokens, {2}, "pick <tag|name>")
        return _component_command("PICK_COMPONENT", tokens[1])
    if verb == "hover":
        _require_count(tokens, {6}, "hover <tag|name> <x> <y> <rotation> <z>")
        return _component_command(
            "HOVER",
            tokens[1],
            {
                "target_x": _number(tokens[2], "x"),
                "target_y": _number(tokens[3], "y"),
                "rotation": _number(tokens[4], "rotation"),
                "z": _number(tokens[5], "z"),
            },
        )
    if verb in {"placehover", "place-from-hover", "placefromhover"}:
        _require_count(tokens, {5}, "placehover <tag|name> <x> <y> <rotation>")
        return _component_command(
            "PLACE_FROM_HOVER",
            tokens[1],
            {
                "target_x": _number(tokens[2], "x"),
                "target_y": _number(tokens[3], "y"),
                "rotation": _number(tokens[4], "rotation"),
            },
        )
    if verb in {"confirmhold", "confirm-holding-tag", "confirm_holding"}:
        _require_count(tokens, {2}, "confirmhold <tag|name>")
        return _component_command("CONFIRM_HOLDING_TAG", tokens[1])

    if verb == "json":
        payload_text = raw[len(tokens[0]) :].strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise CommandSyntaxError(f"json: {exc.msg}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
            raise CommandSyntaxError('json requires an object with an "action" string')
        return ParsedLine(
            "command",
            command={
                "action": payload["action"],
                "target_ref": payload.get("target_id"),
                "parameters": payload.get("parameters") or {},
            },
        )

    raise CommandSyntaxError(f'Unknown command "{tokens[0]}". Use help.')


def help_payload() -> dict[str, Any]:
    """Return the intentionally compact command-language description."""

    return {
        "language": "cloudlabs-console-v1",
        "commands": list(COMPACT_COMMANDS),
        "notes": [
            "Distances and table positions are millimetres.",
            "Rotations are degrees.",
            "Use quoted component names when they contain spaces.",
        ],
    }


__all__ = [
    "COMPACT_COMMANDS",
    "CommandSyntaxError",
    "ParsedLine",
    "help_payload",
    "parse_console_line",
]
