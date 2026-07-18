"""Compare a stored configuration against live runtime + active catalog."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Set


def configuration_tag_ids(configuration: Mapping[str, Any]) -> List[str]:
    comps = configuration.get("components") or {}
    if not isinstance(comps, dict):
        return []
    return sorted(tid for tid in comps.keys() if isinstance(tid, str) and tid.strip())


def runtime_tag_ids(runtime: Mapping[str, Any]) -> List[str]:
    comps = runtime.get("components") or {}
    if not isinstance(comps, dict):
        return []
    return sorted(tid for tid in comps.keys() if isinstance(tid, str) and tid.strip())


def build_checkout_compatibility_report(
    *,
    configuration: Mapping[str, Any],
    runtime: Mapping[str, Any],
    commit_catalog_hash: Optional[str] = None,
    current_catalog_hash: Optional[str] = None,
    catalog_tag_ids: Optional[Iterable[str]] = None,
    library_tag_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Return tag/catalog drift between a commit and the live bench."""
    config_tags = set(configuration_tag_ids(configuration))
    runtime_tags = set(runtime_tag_ids(runtime))
    catalog_tags = {
        str(t).strip()
        for t in (catalog_tag_ids or ())
        if isinstance(t, str) and str(t).strip()
    }
    library_tags = {
        str(t).strip()
        for t in (library_tag_ids or ())
        if isinstance(t, str) and str(t).strip()
    }

    issues: List[Dict[str, Any]] = []

    for tag_id in sorted(config_tags - runtime_tags):
        in_library = tag_id in library_tags
        issues.append(
            {
                "kind": "missing_in_runtime",
                "tag_id": tag_id,
                "in_library": in_library,
                "in_catalog": tag_id in catalog_tags,
                "blocking": not in_library,
                "suggested_action": "add_from_inventory" if in_library else "unavailable",
            }
        )

    for tag_id in sorted(config_tags - catalog_tags):
        in_library = tag_id in library_tags
        issues.append(
            {
                "kind": "missing_in_catalog",
                "tag_id": tag_id,
                "in_library": in_library,
                "blocking": False,
                "suggested_action": "activate_catalog" if in_library else "unavailable",
            }
        )

    for tag_id in sorted(runtime_tags - config_tags):
        issues.append(
            {
                "kind": "extra_in_runtime",
                "tag_id": tag_id,
                "blocking": False,
                "suggested_action": "ignore",
            }
        )

    catalog_match = True
    if commit_catalog_hash and current_catalog_hash:
        catalog_match = commit_catalog_hash == current_catalog_hash
        if not catalog_match:
            issues.append(
                {
                    "kind": "catalog_hash_mismatch",
                    "tag_id": None,
                    "blocking": False,
                    "suggested_action": "acknowledge",
                    "commit_hash": commit_catalog_hash,
                    "current_hash": current_catalog_hash,
                }
            )

    blocking = [issue for issue in issues if issue.get("blocking")]
    missing_runtime = sorted(config_tags - runtime_tags)

    return {
        "ready": not missing_runtime and len(blocking) == 0,
        "blocking_count": len(blocking),
        "resolvable_count": len(
            [
                issue
                for issue in issues
                if issue.get("kind") == "missing_in_runtime"
                and issue.get("suggested_action") == "add_from_inventory"
            ]
        ),
        "missing_in_runtime": missing_runtime,
        "catalog_hash": {
            "commit": commit_catalog_hash,
            "current": current_catalog_hash,
            "match": catalog_match,
        },
        "tags": {
            "configuration": sorted(config_tags),
            "runtime": sorted(runtime_tags),
            "catalog": sorted(catalog_tags),
        },
        "issues": issues,
    }
