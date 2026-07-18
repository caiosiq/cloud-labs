"""Remote approved configuration pins and publish-request workflow (Phase G)."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CatalogPinsStore:
    """File-backed store for CatalogPin rows and publish requests."""

    def __init__(self, store_dir: str) -> None:
        self._dir = store_dir
        self._pins_path = os.path.join(store_dir, "pins.json")
        self._requests_path = os.path.join(store_dir, "publish_requests.json")
        self._lock = threading.RLock()
        os.makedirs(store_dir, exist_ok=True)
        self._ensure_files()

    def _ensure_files(self) -> None:
        for path, default in (
            (self._pins_path, {"pins": []}),
            (self._requests_path, {"requests": []}),
        ):
            if not os.path.isfile(path):
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(default, fh, indent=2)

    def _read_json(self, path: str) -> Dict[str, Any]:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}

    def _write_json(self, path: str, data: Dict[str, Any]) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)

    def list_pins(self) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._read_json(self._pins_path)
            pins = data.get("pins")
            return list(pins) if isinstance(pins, list) else []

    def get_pin(self, pin_id: str) -> Optional[Dict[str, Any]]:
        needle = (pin_id or "").strip()
        if not needle:
            return None
        for pin in self.list_pins():
            if str(pin.get("pin_id")) == needle:
                return dict(pin)
        return None

    def list_publish_requests(
        self,
        *,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._read_json(self._requests_path)
            rows = data.get("requests")
            if not isinstance(rows, list):
                return []
            if status is None:
                return [dict(r) for r in rows]
            want = status.strip().lower()
            return [dict(r) for r in rows if str(r.get("status", "")).lower() == want]

    def submit_publish_request(
        self,
        *,
        repo_id: str,
        configuration_id: str,
        branch: str,
        message: str,
        requested_by: str,
        pin_id: Optional[str] = None,
        backend_id: Optional[str] = None,
        auto_approve: bool = False,
        approved_by: str = "system:auto",
    ) -> Dict[str, Any]:
        repo = (repo_id or "").strip()
        commit = (configuration_id or "").strip()
        branch_name = (branch or "main").strip() or "main"
        if not repo or not commit:
            raise ValueError("repo_id and configuration_id are required")

        with self._lock:
            req_id = f"pub_{uuid.uuid4().hex[:12]}"
            record: Dict[str, Any] = {
                "request_id": req_id,
                "status": "pending",
                "repo_id": repo,
                "configuration_id": commit,
                "branch": branch_name,
                "message": (message or "").strip(),
                "requested_by": (requested_by or "").strip() or "unknown",
                "requested_at": _utcnow_iso(),
                "backend_id": backend_id,
                "proposed_pin_id": (pin_id or "").strip() or None,
            }

            if auto_approve:
                pin = self._approve_locked(record, approved_by=approved_by)
                record["status"] = "approved"
                record["approved_at"] = _utcnow_iso()
                record["approved_by"] = approved_by
                record["catalog_pin_id"] = pin["pin_id"]
            else:
                data = self._read_json(self._requests_path)
                rows = data.get("requests")
                if not isinstance(rows, list):
                    rows = []
                rows.append(record)
                data["requests"] = rows
                self._write_json(self._requests_path, data)

            return dict(record)

    def approve_publish_request(
        self,
        request_id: str,
        *,
        approved_by: str,
        pin_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        needle = (request_id or "").strip()
        if not needle:
            raise ValueError("request_id is required")

        with self._lock:
            data = self._read_json(self._requests_path)
            rows = data.get("requests")
            if not isinstance(rows, list):
                raise KeyError(request_id)

            target = None
            for row in rows:
                if str(row.get("request_id")) == needle:
                    target = row
                    break
            if target is None:
                raise KeyError(request_id)
            if str(target.get("status")).lower() != "pending":
                raise ValueError(f"request {needle!r} is not pending")

            if pin_id:
                target["proposed_pin_id"] = pin_id.strip()
            pin = self._approve_locked(target, approved_by=approved_by)
            target["status"] = "approved"
            target["approved_at"] = _utcnow_iso()
            target["approved_by"] = approved_by
            target["catalog_pin_id"] = pin["pin_id"]
            self._write_json(self._requests_path, data)
            return dict(target)

    def reject_publish_request(
        self,
        request_id: str,
        *,
        rejected_by: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        needle = (request_id or "").strip()
        if not needle:
            raise ValueError("request_id is required")

        with self._lock:
            data = self._read_json(self._requests_path)
            rows = data.get("requests")
            if not isinstance(rows, list):
                raise KeyError(request_id)

            target = None
            for row in rows:
                if str(row.get("request_id")) == needle:
                    target = row
                    break
            if target is None:
                raise KeyError(request_id)
            if str(target.get("status")).lower() != "pending":
                raise ValueError(f"request {needle!r} is not pending")

            target["status"] = "rejected"
            target["rejected_at"] = _utcnow_iso()
            target["rejected_by"] = (rejected_by or "").strip() or "owner"
            target["reject_reason"] = (reason or "").strip()
            self._write_json(self._requests_path, data)
            return dict(target)

    def _approve_locked(
        self,
        request: Dict[str, Any],
        *,
        approved_by: str,
    ) -> Dict[str, Any]:
        pin_id = (
            str(request.get("proposed_pin_id") or "").strip()
            or f"{request['repo_id']}-{request['configuration_id'][:8]}"
        )
        pin: Dict[str, Any] = {
            "pin_id": pin_id,
            "display_name": request.get("message") or pin_id,
            "repo_id": request["repo_id"],
            "configuration_id": request["configuration_id"],
            "branch": request.get("branch") or "main",
            "backend_id": request.get("backend_id"),
            "approved_at": _utcnow_iso(),
            "approved_by": approved_by,
            "requested_by": request.get("requested_by"),
        }

        pins_data = self._read_json(self._pins_path)
        pins = pins_data.get("pins")
        if not isinstance(pins, list):
            pins = []
        replaced = False
        for index, existing in enumerate(pins):
            if str(existing.get("pin_id")) == pin_id:
                pins[index] = pin
                replaced = True
                break
        if not replaced:
            pins.append(pin)
        pins_data["pins"] = pins
        self._write_json(self._pins_path, pins_data)

        if str(request.get("status", "")).lower() == "pending":
            req_data = self._read_json(self._requests_path)
            req_rows = req_data.get("requests")
            if isinstance(req_rows, list):
                for row in req_rows:
                    if str(row.get("request_id")) == str(request.get("request_id")):
                        row.update(
                            {
                                "status": "approved",
                                "approved_at": pin["approved_at"],
                                "approved_by": approved_by,
                                "catalog_pin_id": pin_id,
                            }
                        )
                self._write_json(self._requests_path, req_data)

        return pin
