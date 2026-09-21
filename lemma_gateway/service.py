"""Cloud Labs coordinator client used by the Lemma gateway."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .command_language import ParsedLine, help_payload


BACKEND_ID = "sim.default"


class GatewayError(RuntimeError):
    """Base error returned to a gateway caller."""

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class UpstreamError(GatewayError):
    """Cloud Labs coordinator rejected a request or could not be reached."""


class JobCancelled(GatewayError):
    """The gateway stopped waiting for a submitted operation."""

    def __init__(self, message: str = "job cancellation requested") -> None:
        super().__init__(message, status_code=409)


def _response_detail(response: httpx.Response, body: Any) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)
    if response.text:
        return response.text[:1000]
    return f"HTTP {response.status_code}"


def _tag_sort_key(tag_id: str) -> tuple[int, str]:
    try:
        return (int(tag_id.removeprefix("tag_")), tag_id)
    except ValueError:
        return (10**9, tag_id)


@dataclass
class Lease:
    lease_id: str
    holder: str
    expires_at: str | None = None


class CoordinatorClient:
    """Simulation-only northbound client for the existing coordinator API."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8000",
        timeout_s: float = 15.0,
        operation_timeout_s: float = 900.0,
        poll_interval_s: float = 0.35,
        heartbeat_s: float = 45.0,
        session: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.operation_timeout_s = operation_timeout_s
        self.poll_interval_s = poll_interval_s
        self.heartbeat_s = heartbeat_s
        self.client_id = uuid.uuid4().hex[:12]
        self.holder = f"lemma-gateway:{self.client_id}"
        self.session = session or httpx.Client()
        self.session.headers.update(
            {
                "X-CloudLabs-Backend": BACKEND_ID,
                "X-CloudLabs-Client": self.client_id,
            }
        )
        self._lease: Lease | None = None
        self._lease_lock = threading.RLock()
        self._closed = threading.Event()
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name="lemma-gateway-lease-heartbeat",
            daemon=True,
        )
        self._heartbeat.start()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        include_lease: bool = False,
        timeout_s: float | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if include_lease:
            lease = self.acquire_lease()
            headers["X-CloudLabs-Lease"] = lease.lease_id
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                json=body,
                headers=headers or None,
                timeout=timeout_s or self.timeout_s,
            )
        except httpx.RequestError as exc:
            raise UpstreamError(
                f"Cloud Labs is unreachable at {self.base_url}: {exc}",
                status_code=503,
            ) from exc
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        if not response.is_success:
            status = response.status_code if response.status_code < 500 else 502
            raise UpstreamError(_response_detail(response, payload), status_code=status)
        return payload

    def acquire_lease(self) -> Lease:
        with self._lease_lock:
            if self._lease is not None:
                return self._lease
            payload = self._request(
                "POST",
                "/api/jobs/lease/acquire",
                body={
                    "backend_id": BACKEND_ID,
                    "holder": self.holder,
                    "mode": "imperative",
                },
            )
            if not isinstance(payload, dict) or not payload.get("lease_id"):
                raise UpstreamError("Cloud Labs returned an invalid lease", status_code=502)
            self._lease = Lease(
                lease_id=str(payload["lease_id"]),
                holder=str(payload.get("holder") or self.holder),
                expires_at=(
                    str(payload["expires_at"])
                    if payload.get("expires_at") is not None
                    else None
                ),
            )
            return self._lease

    def _heartbeat_loop(self) -> None:
        while not self._closed.wait(self.heartbeat_s):
            with self._lease_lock:
                lease = self._lease
            if lease is None:
                continue
            try:
                payload = self._request(
                    "POST",
                    "/api/jobs/lease/heartbeat",
                    body={"lease_id": lease.lease_id},
                )
                with self._lease_lock:
                    if self._lease and self._lease.lease_id == lease.lease_id:
                        expires_at = payload.get("expires_at") if isinstance(payload, dict) else None
                        self._lease.expires_at = str(expires_at) if expires_at else None
            except GatewayError:
                with self._lease_lock:
                    if self._lease and self._lease.lease_id == lease.lease_id:
                        self._lease = None

    def release_lease(self) -> dict[str, Any]:
        with self._lease_lock:
            lease = self._lease
            self._lease = None
        if lease is None:
            return {"released": False}
        try:
            self._request(
                "POST",
                "/api/jobs/lease/release",
                body={"lease_id": lease.lease_id},
            )
        except GatewayError as exc:
            return {"released": False, "error": str(exc)}
        return {"released": True}

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self.release_lease()
        self.session.close()

    def health(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/backends")
        rows = payload.get("backends", []) if isinstance(payload, dict) else []
        backend = next(
            (row for row in rows if isinstance(row, dict) and row.get("backend_id") == BACKEND_ID),
            None,
        )
        if backend is None:
            raise UpstreamError(f"{BACKEND_ID} is not registered", status_code=503)
        if str(backend.get("lab_mode") or "").upper() != "SIMULATION":
            raise UpstreamError("configured backend is not a simulation", status_code=503)
        return {
            "ok": backend.get("availability") == "ready",
            "gateway": "lemma-cloudlabs",
            "backend_id": BACKEND_ID,
            "backend_availability": backend.get("availability"),
            "system_status": backend.get("system_status"),
            "component_count": backend.get("component_count"),
        }

    def raw_state(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/lab-state")
        if not isinstance(payload, dict):
            raise UpstreamError("Cloud Labs returned an invalid lab state", status_code=502)
        return payload

    def _catalog(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/catalog")
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def _catalog_by_tag(self) -> dict[str, dict[str, Any]]:
        return {
            str(row["tag_id"]): row
            for row in self._catalog()
            if row.get("tag_id")
        }

    def resolve_component(self, component_ref: str) -> str:
        ref = str(component_ref or "").strip()
        if not ref:
            raise GatewayError("component tag or name is required", status_code=400)
        state = self.raw_state()
        components = state.get("components") if isinstance(state.get("components"), dict) else {}
        catalog = self._catalog_by_tag()
        if ref in components or ref in catalog:
            return ref
        numeric_alias = f"tag_{ref}"
        if not ref.startswith("tag_") and (
            numeric_alias in components or numeric_alias in catalog
        ):
            return numeric_alias
        needle = " ".join(ref.lower().split())
        hits = [
            tag_id
            for tag_id, row in catalog.items()
            if " ".join(str(row.get("name") or "").lower().split()) == needle
        ]
        if not hits:
            raise GatewayError(f'unknown component "{ref}"', status_code=404)
        if len(hits) > 1:
            raise GatewayError(
                f'ambiguous component name "{ref}"; use one of: {", ".join(sorted(hits))}',
                status_code=409,
            )
        return hits[0]

    def compact_state(self, *, tag_id: str | None = None) -> dict[str, Any]:
        state = self.raw_state()
        catalog = self._catalog_by_tag()
        raw_components = (
            state.get("components") if isinstance(state.get("components"), dict) else {}
        )
        components: list[dict[str, Any]] = []
        for current_tag in sorted(raw_components, key=_tag_sort_key):
            if tag_id is not None and current_tag != tag_id:
                continue
            component = raw_components[current_tag]
            if not isinstance(component, dict):
                continue
            tunables = (
                component.get("statecontrol", {}).get("tunables", {})
                if isinstance(component.get("statecontrol"), dict)
                else {}
            )
            if not isinstance(tunables, dict):
                tunables = {}
            catalog_row = catalog.get(current_tag, {})
            components.append(
                {
                    "tag_id": current_tag,
                    "name": catalog_row.get("name"),
                    "type": component.get("type") or catalog_row.get("type"),
                    "presence": tunables.get("presence"),
                    "pose": tunables.get("nominal_pose"),
                    "storage": tunables.get("storage"),
                }
            )
        if tag_id is not None and not components:
            raise GatewayError(f"component not found: {tag_id}", status_code=404)
        return {
            "backend_id": BACKEND_ID,
            "system_status": state.get("system_status"),
            "component_count": len(components),
            "components": components,
        }

    def list_presets(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/runtime-mode/simulation-presets")
        rows = payload.get("presets", []) if isinstance(payload, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def show_preset(self, selector: str) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/api/runtime-mode/simulation-presets/{quote(selector, safe='')}",
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("document"), dict):
            raise UpstreamError("Cloud Labs returned an invalid preset document", status_code=502)
        return payload

    def write_preset(
        self,
        name: str,
        document: dict[str, Any],
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        payload = self._request(
            "PUT",
            f"/api/runtime-mode/simulation-presets/{quote(name, safe='')}",
            body={"document": document, "overwrite": bool(overwrite)},
            include_lease=True,
            timeout_s=max(self.timeout_s, 60.0),
        )
        if not isinstance(payload, dict):
            raise UpstreamError("Cloud Labs returned an invalid preset write result", status_code=502)
        return payload

    def capabilities(self, *, detailed: bool = False) -> dict[str, Any]:
        state = self.compact_state()
        layout = self._request("GET", "/api/lab-layout")
        presets = self.list_presets()
        result: dict[str, Any] = {
            "language": "cloudlabs-console-v1",
            "backend_id": BACKEND_ID,
            "status": state["system_status"],
            "commands": help_payload()["commands"],
            "components": [
                {
                    "tag_id": row["tag_id"],
                    "name": row.get("name"),
                    "type": row.get("type"),
                }
                for row in state["components"]
            ],
            "presets": [
                row.get("name")
                for row in presets
                if row.get("name") and row.get("valid", True)
            ],
            "limits": (
                layout.get("lab_bounds_mm") if isinstance(layout, dict) else None
            ),
        }
        if detailed:
            result["notes"] = help_payload()["notes"]
            result["units"] = {"position": "mm", "rotation": "degrees"}
            result["job_model"] = "mutations return a job_id; poll /v1/jobs/{job_id}"
        return result

    def execute_read(self, parsed: ParsedLine) -> dict[str, Any]:
        if parsed.kind == "help":
            return help_payload()
        if parsed.kind == "capabilities":
            return self.capabilities()
        if parsed.kind == "state":
            if parsed.arguments.get("raw"):
                return self.raw_state()
            return self.compact_state()
        if parsed.kind == "preset_list":
            return {
                "presets": [
                    {
                        "name": row.get("name"),
                        "component_count": row.get("component_count"),
                        "valid": row.get("valid", True),
                    }
                    for row in self.list_presets()
                ]
            }
        if parsed.kind == "preset_show":
            payload = self.show_preset(str(parsed.arguments["selector"]))
            return payload["document"]
        if parsed.kind == "component_list":
            payload = self._request(
                "GET", "/api/runtime-mode/simulation-components"
            )
            if not isinstance(payload, dict):
                raise UpstreamError(
                    "Cloud Labs returned an invalid simulation library",
                    status_code=502,
                )
            return payload
        if parsed.kind == "component_next_tag":
            payload = self._request(
                "GET", "/api/runtime-mode/simulation-components/next-tag"
            )
            if not isinstance(payload, dict) or not payload.get("tag_id"):
                raise UpstreamError(
                    "Cloud Labs returned an invalid next component tag",
                    status_code=502,
                )
            return {"tag_id": str(payload["tag_id"])}
        if parsed.kind == "component_show":
            tag_id = str(parsed.arguments["tag_id"])
            payload = self._request(
                "GET",
                f"/api/runtime-mode/simulation-components/{quote(tag_id, safe='')}",
            )
            if not isinstance(payload, dict):
                raise UpstreamError(
                    "Cloud Labs returned an invalid component definition",
                    status_code=502,
                )
            return payload
        if parsed.kind in {"tunables", "measurables"}:
            tag_id = self.resolve_component(parsed.arguments["component_ref"])
            leaf = parsed.kind
            state = self.raw_state()
            component = (state.get("components") or {}).get(tag_id)
            if not isinstance(component, dict):
                raise GatewayError(f"component not found: {tag_id}", status_code=404)
            statecontrol = component.get("statecontrol")
            payload = statecontrol.get(leaf) if isinstance(statecontrol, dict) else None
            return {"tag_id": tag_id, leaf: payload or {}}
        raise GatewayError(f"{parsed.kind} is not a read command", status_code=400)

    def execute_mutation(
        self,
        parsed: ParsedLine,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        if cancel_event.is_set():
            raise JobCancelled()
        if parsed.kind == "command":
            if not parsed.command:
                raise GatewayError("parsed command is empty", status_code=400)
            command = copy.deepcopy(parsed.command)
            component_ref = command.pop("target_ref", None)
            if component_ref:
                command["target_id"] = self.resolve_component(str(component_ref))
            accepted = self._request(
                "POST",
                "/api/command",
                body=command,
                include_lease=True,
            )
            settled = self._wait_until_idle(cancel_event)
            return {"accepted": accepted, "settled_state": settled}

        if parsed.kind == "record":
            tag_id = self.resolve_component(parsed.arguments["component_ref"])
            payload = self._request(
                "POST",
                f"/api/components/{quote(tag_id, safe='')}/measurables/record",
                body={},
                include_lease=True,
            )
            return {"tag_id": tag_id, "result": payload}

        if parsed.kind == "preset_save":
            name = parsed.arguments["name"]
            payload = self._request(
                "POST",
                f"/api/runtime-mode/simulation-presets/{quote(name, safe='')}",
                body={"overwrite": bool(parsed.arguments.get("overwrite"))},
                include_lease=True,
                timeout_s=max(self.timeout_s, 60.0),
            )
            return {"preset": name, "result": payload}

        if parsed.kind == "preset_write":
            name = str(parsed.arguments["name"])
            payload = self.write_preset(
                name,
                parsed.arguments["document"],
                overwrite=bool(parsed.arguments.get("overwrite")),
            )
            return {"preset": name, "result": payload}

        if parsed.kind == "preset_load":
            selector = str(parsed.arguments["selector"])
            if selector.lower() == "current":
                path = "/api/runtime-mode/refresh-mujoco"
            else:
                path = (
                    "/api/runtime-mode/simulation-presets/"
                    f"{quote(selector, safe='')}/load"
                )
            payload = self._request(
                "POST",
                path,
                body={},
                include_lease=True,
                timeout_s=max(self.timeout_s, 120.0),
            )
            settled = self._wait_until_idle(cancel_event, allow_transient_errors=True)
            return {"preset": selector, "result": payload, "settled_state": settled}

        if parsed.kind in {
            "component_define",
            "component_configure",
            "component_reset",
            "component_insert",
            "component_remove",
            "component_delete",
            "component_clear",
        }:
            if cancel_event.is_set():
                raise JobCancelled()
            tag_id = str(parsed.arguments.get("tag_id") or "")
            if parsed.kind == "component_clear":
                method = "POST"
                path = "/api/runtime-mode/simulation-table/clear"
                body = {"scope": str(parsed.arguments.get("scope") or "table")}
            else:
                base = (
                    "/api/runtime-mode/simulation-components/"
                    f"{quote(tag_id, safe='')}"
                )
                method, suffix, body = {
                    "component_define": (
                        "PUT",
                        "",
                        parsed.arguments["definition"],
                    ),
                    "component_configure": (
                        "PATCH",
                        "",
                        parsed.arguments["definition"],
                    ),
                    "component_reset": ("POST", "/reset", {}),
                    "component_insert": (
                        "POST",
                        "/insert",
                        parsed.arguments["payload"],
                    ),
                    "component_remove": ("POST", "/remove", {}),
                    "component_delete": ("DELETE", "", None),
                }[parsed.kind]
                path = base + suffix
            payload = self._request(
                method,
                path,
                body=body,
                include_lease=True,
                timeout_s=max(self.timeout_s, 120.0),
            )
            result: dict[str, Any] = {
                "operation": parsed.kind,
                "result": payload,
            }
            if parsed.kind in {
                "component_configure",
                "component_reset",
                "component_insert",
                "component_remove",
                "component_clear",
            }:
                result["settled_state"] = self.compact_state()
            return result

        raise GatewayError(f"{parsed.kind} is not a mutation command", status_code=400)

    def _wait_until_idle(
        self,
        cancel_event: threading.Event,
        *,
        allow_transient_errors: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        saw_non_idle = False
        idle_observations = 0
        last_error: GatewayError | None = None
        while time.monotonic() - started < self.operation_timeout_s:
            if cancel_event.is_set():
                raise JobCancelled(
                    "cancellation requested; the gateway stopped waiting, but the simulation "
                    "may finish an action already accepted by MuJoCo"
                )
            try:
                state = self.raw_state()
                last_error = None
            except GatewayError as exc:
                last_error = exc
                if not allow_transient_errors:
                    raise
                time.sleep(self.poll_interval_s)
                continue
            status = str(state.get("system_status") or "").upper()
            elapsed = time.monotonic() - started
            if status and status != "IDLE":
                saw_non_idle = True
                idle_observations = 0
            elif status == "IDLE" and (saw_non_idle or elapsed >= 0.75):
                idle_observations += 1
                if idle_observations >= 2:
                    return {
                        "system_status": "IDLE",
                        "observed_busy": saw_non_idle,
                    }
            time.sleep(self.poll_interval_s)
        if last_error is not None:
            raise GatewayError(
                f"timed out waiting for simulation recovery: {last_error}",
                status_code=504,
            )
        raise GatewayError("timed out waiting for the simulation to become IDLE", status_code=504)


READ_KINDS = frozenset(
    {
        "help",
        "capabilities",
        "state",
        "preset_list",
        "preset_show",
        "component_list",
        "component_next_tag",
        "component_show",
        "tunables",
        "measurables",
    }
)
MUTATION_KINDS = frozenset(
    {
        "command",
        "record",
        "preset_save",
        "preset_write",
        "preset_load",
        "component_define",
        "component_configure",
        "component_reset",
        "component_insert",
        "component_remove",
        "component_delete",
        "component_clear",
    }
)


__all__ = [
    "BACKEND_ID",
    "CoordinatorClient",
    "GatewayError",
    "JobCancelled",
    "MUTATION_KINDS",
    "READ_KINDS",
    "UpstreamError",
]
