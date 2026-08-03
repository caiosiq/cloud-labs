from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent


def run(args: list[str]) -> None:
    subprocess.check_call(args, cwd=root)


def show(spec: str) -> str:
    return subprocess.check_output(["git", "show", spec], cwd=root).decode("utf-8")


def write_add(path: str, text: str) -> None:
    (root / path).write_text(text, encoding="utf-8")
    run(["git", "add", "--", path])


# Runtime JSON: keep remote foundation
for p in (
    "mock_backend/lab_view/lab_state.json",
    "mock_backend/lab_view/session_last_lab_state.json",
):
    run(["git", "checkout", "--ours", "--", p])
    run(["git", "add", "--", p])

# README + mock agent: remote
for p in ("README.md", "scripts/ops/mock_backend_agent.py"):
    run(["git", "checkout", "--ours", "--", p])
    run(["git", "add", "--", p])

# ---- backend/main.py ----
# Start from ours (remote), then inject external_edge early-returns from WIP.
main = show(":2:backend/main.py")
wip = show("wip/local-cloudlabs-real-edge:backend/main.py")

# Insert external_edge skip into session checkpoint status builder.
# Locate the function containing "stale_warning_hours" and "offers".
if "external_edge" not in main:
    skip1 = '''    client = _edge_client_for()
    if client.transport != EdgeTransport.IN_PROCESS:
        # Session checkpoint reconciliation belongs to an in-process lab host.
        # An external edge owns its live state and is reconciled deliberately
        # through edge primitives such as RECORD_TUNABLES / LOCALIZE_COMPONENTS.
        return {
            "enabled": False,
            "skipped_reason": "external_edge",
            "checkpoint_path": None,
            "checkpoint_saved_at": None,
            "checkpoint_lab_mode": None,
            "age_hours": None,
            "stale_warning_hours": None,
            "stale_warning": False,
            "thresholds": None,
            "offers": [],
        }

'''
    # Find first function that returns checkpoint-like payload
    m = re.search(
        r"((?:async )?def [^\n]*checkpoint[^\n]*:\n(?:    \"\"\"[\s\S]*?\"\"\"\n)?)",
        main,
        flags=re.I,
    )
    if not m:
        # fallback: before first import of session_checkpoint inside a function
        m = re.search(
            r"(    from mock_backend\.shared\.session_checkpoint import \(\n)",
            main,
        )
        if m:
            main = main[: m.start()] + skip1 + main[m.start() :]
            print("inserted external_edge before session_checkpoint import")
        else:
            print("WARN: could not insert external_edge checkpoint skip")
    else:
        main = main[: m.end()] + skip1 + main[m.end() :]
        print("inserted external_edge after checkpoint def")

# Preview offers skip for HTTP edge
if 'skipped_reason": "external_edge"' not in main or main.count("external_edge") < 2:
    skip2 = '''    client = _edge_client_for()
    if client.transport != EdgeTransport.IN_PROCESS:
        # Preview offers are a mock/in-process dry run. A physical HTTP edge
        # cannot truthfully preview camera measurements without performing the
        # scan, so the UI should use its deliberate refresh confirmation.
        return {
            "supported": False,
            "skipped_reason": "external_edge",
            "offers": [],
        }

'''
    # Insert before reconciliation_thresholds_from_manifest import if present
    marker = "from mock_backend.shared.session_checkpoint import reconciliation_thresholds_from_manifest"
    idx = main.find(marker)
    if idx > 0 and main.count("external_edge") < 2:
        # only if this is inside offers function - check nearby
        window = main[max(0, idx - 400) : idx]
        if "offer" in window.lower() or "refresh" in window.lower() or "preview" in window.lower():
            # find line start
            line_start = main.rfind("\n", 0, idx) + 1
            main = main[:line_start] + skip2 + main[line_start:]
            print("inserted external_edge offers skip")
        else:
            # try second occurrence search in WIP context around refresh-pose offers
            m2 = re.search(
                r"((?:async )?def [^\n]*refresh[^\n]*offer[^\n]*:\n(?:    \"\"\"[\s\S]*?\"\"\"\n)?)",
                main,
                flags=re.I,
            )
            if m2 and main.count("external_edge") < 2:
                main = main[: m2.end()] + skip2 + main[m2.end() :]
                print("inserted external_edge offers skip via refresh offers def")
            else:
                print("WARN: offers skip not inserted", main.count("external_edge"))
    elif main.count("external_edge") >= 2:
        print("external_edge skips already sufficient")
    else:
        print("WARN: marker for offers skip missing")

if "<<<<<<" in main:
    raise SystemExit("main still has conflict markers - did not start from :2 cleanly?")

write_add("backend/main.py", main)
print("main external_edge count", main.count("external_edge"))

# ---- pose-refresh.js ----
pose = show(":2:frontend/js/ui/pose-refresh.js")
wip_pose = show("wip/local-cloudlabs-real-edge:frontend/js/ui/pose-refresh.js")

# Ensure imports include backendHeaders + getSelectedBackendId for init helper
if "getSelectedBackendId" not in pose:
    pose = pose.replace(
        "import { withBackendQuery } from '../state/backend-selection.js';",
        "import {\n"
        "    backendHeaders,\n"
        "    getSelectedBackendId,\n"
        "    withBackendQuery,\n"
        "} from '../state/backend-selection.js';",
    )
if "backendHeaders" not in pose:
    pose = pose.replace(
        "import {\n    getSelectedBackendId,\n    withBackendQuery,\n} from '../state/backend-selection.js';",
        "import {\n"
        "    backendHeaders,\n"
        "    getSelectedBackendId,\n"
        "    withBackendQuery,\n"
        "} from '../state/backend-selection.js';",
    )

# Add headers to offers/lab-state fetches when missing
pose = pose.replace(
    "const res = await fetch(withBackendQuery(`/api/lab-state/refresh-pose/offers${qs}`));",
    "const res = await fetch(\n"
    "        withBackendQuery(`/api/lab-state/refresh-pose/offers${qs}`),\n"
    "        { headers: backendHeaders() },\n"
    "    );",
)
pose = pose.replace(
    "const stateRes = await fetch(withBackendQuery('/api/lab-state'));",
    "const stateRes = await fetch(withBackendQuery('/api/lab-state'), {\n"
    "            headers: backendHeaders(),\n"
    "        });",
)

m = re.search(
    r"export async function initializeUnlocalizedRealInventoryPoses\(\) \{[\s\S]*?\n\}",
    wip_pose,
)
if not m:
    raise SystemExit("missing initializeUnlocalizedRealInventoryPoses in WIP")
init_fn = m.group(0)
# Prefer RECORD_TUNABLES in the helper if it still posts refresh-pose
if "initializeUnlocalizedRealInventoryPoses" not in pose:
    if "let _initialLocalizationPromise" not in pose:
        init_block = "let _initialLocalizationPromise = null;\n\n" + init_fn + "\n\n"
    else:
        init_block = init_fn + "\n\n"
    # Insert before export async function runLabPoseRefresh or at end before last export
    if "export async function runLabPoseRefresh" in pose:
        pose = pose.replace(
            "export async function runLabPoseRefresh",
            init_block + "export async function runLabPoseRefresh",
            1,
        )
    else:
        pose = pose.rstrip() + "\n\n" + init_block

write_add("frontend/js/ui/pose-refresh.js", pose)
print("pose-refresh ok", "initializeUnlocalizedRealInventoryPoses" in pose, "<<<<<<" not in pose)

# ---- app-main.js ----
app = show(":2:frontend/js/app-main.js")
if "initializeUnlocalizedRealInventoryPoses" not in app:
    if "initPoseRefresh, runLabPoseRefresh" in app:
        app = app.replace(
            "import { initPoseRefresh, runLabPoseRefresh } from './ui/pose-refresh.js';",
            "import {\n"
            "    initPoseRefresh,\n"
            "    initializeUnlocalizedRealInventoryPoses,\n"
            "    runLabPoseRefresh,\n"
            "} from './ui/pose-refresh.js';",
        )
    elif "initPoseRefresh" in app and "from './ui/pose-refresh.js'" in app:
        app = re.sub(
            r"import \{([^}]+)\} from '\./ui/pose-refresh\.js';",
            lambda m: (
                "import {\n"
                "    initPoseRefresh,\n"
                "    initializeUnlocalizedRealInventoryPoses,\n"
                "    runLabPoseRefresh,\n"
                "} from './ui/pose-refresh.js';"
                if "initializeUnlocalizedRealInventoryPoses" not in m.group(1)
                else m.group(0)
            ),
            app,
            count=1,
        )
    # Call site: after initPoseRefresh()
    if "initializeUnlocalizedRealInventoryPoses()" not in app:
        if "initPoseRefresh();" in app:
            app = app.replace(
                "initPoseRefresh();",
                "initPoseRefresh();\n    void initializeUnlocalizedRealInventoryPoses();",
                1,
            )
        else:
            # after fetchLabState chain commonly used in WIP
            app = re.sub(
                r"(fetchLabState\(\)[^\n]*\n(?:.*\n){0,15}?)",
                lambda m: m.group(0)
                if "initializeUnlocalizedRealInventoryPoses" in m.group(0)
                else m.group(0).rstrip("\n")
                + "\n        .then(() => initializeUnlocalizedRealInventoryPoses())\n",
                app,
                count=1,
            )

write_add("frontend/js/app-main.js", app)
print("app-main ok", "initializeUnlocalizedRealInventoryPoses" in app, "<<<<<<" not in app)

# Move experiment-test into mock_backend
src = root / "mock_edge" / "lab_view" / "control" / "experiment-test"
dst = root / "mock_backend" / "lab_view" / "control" / "experiment-test"
if src.exists():
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    run(["git", "rm", "-r", "--cached", "--ignore-unmatch", "mock_edge"])
    # remove leftover mock_edge tree from index/worktree if empty-ish
    if (root / "mock_edge").exists():
        shutil.rmtree(root / "mock_edge", ignore_errors=True)
    run(["git", "add", "mock_backend/lab_view/control/experiment-test"])
    print("moved experiment-test")

# Stage already-merged non-conflict files
run(["git", "add", "-u"])
run(["git", "add", "backend/tests/test_http_edge_refresh_routing.py"])
print("remaining conflicts:")
print(subprocess.check_output(["git", "diff", "--name-only", "--diff-filter=U"], cwd=root).decode())
