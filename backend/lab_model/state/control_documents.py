"""Stored document kinds for ControlManager (configuration VC)."""

from __future__ import annotations

from typing import Any, Dict, Final

KIND_CONFIGURATION: Final[str] = "cloud_labs_configuration"
KIND_OBSERVATIONS: Final[str] = "cloud_labs_observations"
KIND_SETUP: Final[str] = "cloud_labs_setup"

SCHEMA_VERSION: Final[int] = 1
DEFAULT_REPO_ID: Final[str] = "default"


def configuration_document(
    *,
    commit_id: str,
    repo_id: str,
    branch: str,
    parent_id: str | None,
    message: str,
    configuration: Dict[str, Any],
    catalog_hash: str | None = None,
    created_at: str,
    author: str | None = None,
) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_CONFIGURATION,
        "id": commit_id,
        "repo_id": repo_id,
        "branch": branch,
        "parent_id": parent_id,
        "message": message,
        "created_at": created_at,
        "configuration": configuration,
    }
    if catalog_hash is not None:
        doc["catalog_hash"] = catalog_hash
    if author is not None:
        doc["author"] = author
    return doc


def observations_document(
    *,
    pin_id: str,
    repo_id: str,
    configuration_id: str,
    observations: Dict[str, Any],
    created_at: str,
    message: str | None = None,
) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_OBSERVATIONS,
        "id": pin_id,
        "repo_id": repo_id,
        "configuration_id": configuration_id,
        "created_at": created_at,
        "observations": observations,
    }
    if message is not None:
        doc["message"] = message
    return doc


def setup_document(
    *,
    setup_id: str,
    repo_id: str,
    name: str,
    configuration_id: str,
    observations_id: str | None,
    message: str | None = None,
    created_at: str,
) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_SETUP,
        "id": setup_id,
        "repo_id": repo_id,
        "name": name,
        "configuration_id": configuration_id,
        "observations_id": observations_id,
        "created_at": created_at,
    }
    if message is not None:
        doc["message"] = message
    return doc
