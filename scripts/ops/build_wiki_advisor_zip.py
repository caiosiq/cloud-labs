"""Build advisor Wiki zip: static assets + API fixtures + offline fetch shim."""
from __future__ import annotations

import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(r"c:\Users\User\OneDrive - The University of Chicago\cloud-labs")
OUT = Path.home() / "Desktop" / "cloudlabs-wiki-advisor"
ZIP_PATH = Path.home() / "Desktop" / "cloudlabs-wiki-advisor.zip"
# Also keep a copy under repo dist when possible
REPO_ZIP = ROOT / "dist" / "cloudlabs-wiki-advisor.zip"
BASE = "http://127.0.0.1:8000"
front = ROOT / "frontend"


def get_json(path: str):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    import time

    global OUT, ZIP_PATH
    # Avoid Windows/OneDrive locks on a previous folder name.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    OUT = Path.home() / "Desktop" / f"cloudlabs-wiki-advisor-{stamp}"
    ZIP_PATH = Path.home() / "Desktop" / "cloudlabs-wiki-advisor.zip"
    OUT.mkdir(parents=True)

    fixtures = OUT / "fixtures"
    fixtures.mkdir()

    backends = get_json("/api/backends")
    (fixtures / "backends.json").write_text(json.dumps(backends, indent=2), encoding="utf-8")
    backend_ids = [b["backend_id"] for b in backends.get("backends", [])]
    print("backends", backend_ids)

    for bid in backend_ids:
        safe = bid.replace(".", "_")
        try:
            lib = get_json(f"/api/catalog/library-rows?backend_id={bid}")
        except Exception as exc:  # noqa: BLE001
            print("library-rows fail", bid, type(exc).__name__)
            lib = {"rows": [], "status": "unavailable", "backend_id": bid}
        try:
            tags = get_json(f"/api/catalog/active-tags?backend_id={bid}")
        except Exception as exc:  # noqa: BLE001
            print("active-tags fail", bid, type(exc).__name__)
            tags = {"tags": [], "status": "unavailable", "backend_id": bid}
        (fixtures / f"library-rows__{safe}.json").write_text(
            json.dumps(lib, indent=2), encoding="utf-8"
        )
        (fixtures / f"active-tags__{safe}.json").write_text(
            json.dumps(tags, indent=2), encoding="utf-8"
        )

    (fixtures / "kernels.json").write_text(
        json.dumps(get_json("/api/kernels"), indent=2), encoding="utf-8"
    )

    for path, name in [
        ("/api/platform/registries", "platform-registries.json"),
        ("/api/catalog/pins", "catalog-pins.json"),
        ("/api/control/repos", "control-repos.json"),
    ]:
        try:
            data = get_json(path)
            (fixtures / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("ok", path)
        except Exception as exc:  # noqa: BLE001
            print("skip", path, type(exc).__name__, exc)

    static = OUT / "static"
    # Full JS tree — Wiki pulls a few Twin modules transitively; keep imports resolvable.
    shutil.copytree(front / "js", static / "js")
    shutil.copytree(front / "wiki", static / "wiki")

    shim = r'''/* Offline Wiki shim — baked fixtures instead of live coordinator. */
(function () {
  const FIX = new URL("./fixtures/", import.meta.url);

  async function loadFixture(name) {
    const res = await fetch(new URL(name, FIX));
    if (!res.ok) throw new Error("fixture missing: " + name);
    return res.json();
  }

  function backendSafe(id) {
    return String(id || "mock.default").replace(/\./g, "_");
  }

  function parseUrl(input) {
    try {
      return new URL(input, location.href);
    } catch {
      return null;
    }
  }

  const origFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const url =
      typeof input === "string"
        ? parseUrl(input)
        : input && input.url
          ? parseUrl(input.url)
          : null;
    if (!url) return origFetch(input, init);
    const path = url.pathname;
    const bid = url.searchParams.get("backend_id") || "mock.default";
    const safe = backendSafe(bid);

    if (path.startsWith("/static/")) {
      return origFetch("./static/" + path.slice("/static/".length) + url.search, init);
    }

    if (path === "/api/backends") {
      const data = await loadFixture("backends.json");
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (path === "/api/kernels" || path.startsWith("/api/kernels")) {
      const data = await loadFixture("kernels.json");
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (path.includes("/api/catalog/library-rows")) {
      const data = await loadFixture(`library-rows__${safe}.json`);
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (path.includes("/api/catalog/active-tags")) {
      const data = await loadFixture(`active-tags__${safe}.json`);
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (path.includes("/api/platform/registries")) {
      try {
        const data = await loadFixture("platform-registries.json");
        return new Response(JSON.stringify(data), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      } catch {
        return new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    if (path.includes("/api/catalog/pins")) {
      try {
        const data = await loadFixture("catalog-pins.json");
        return new Response(JSON.stringify(data), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      } catch {
        return new Response(JSON.stringify({ pins: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    if (path.includes("/api/control/")) {
      try {
        const data = await loadFixture("control-repos.json");
        return new Response(JSON.stringify(data), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      } catch {
        return new Response(JSON.stringify({ repos: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    if (path.startsWith("/api/")) {
      console.warn("[offline-wiki] unmocked", path);
      return new Response(JSON.stringify({ status: "ok", offline: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return origFetch(input, init);
  };
})();
'''
    (OUT / "offline-shim.js").write_text(shim.strip() + "\n", encoding="utf-8")

    html = (front / "wiki.html").read_text(encoding="utf-8")
    html = html.replace(
        '<script type="module" src="/static/js/wiki/dashboard.js"></script>',
        '<script type="module" src="./offline-shim.js"></script>\n'
        '    <script type="module" src="./static/js/wiki/dashboard.js"></script>',
    )
    banner = (
        '    <div style="position:relative;z-index:5;padding:8px 24px;'
        "background:rgba(14,165,233,0.15);border-bottom:1px solid rgba(56,189,248,0.35);"
        'font:13px Inter,system-ui,sans-serif;color:#e2e8f0;">'
        "      <strong>Cloud Labs Wiki — offline demo pack</strong>"
        "      · Learn + Backends snapshot · open via local server (see README.txt)"
        "    </div>\n"
    )
    html = html.replace('<div class="wiki-atmosphere"', banner + '    <div class="wiki-atmosphere"', 1)
    (OUT / "index.html").write_text(html, encoding="utf-8")

    dash = (static / "js/wiki/dashboard.js").read_text(encoding="utf-8")
    dash = dash.replace(
        "const GUIDES_BASE = '/static/wiki/guides';",
        "const GUIDES_BASE = './static/wiki/guides';",
    )
    (static / "js/wiki/dashboard.js").write_text(dash, encoding="utf-8")

    hub = (static / "js/wiki/backends-hub.js").read_text(encoding="utf-8")
    hub = hub.replace("/static/wiki/backends/", "./static/wiki/backends/")
    (static / "js/wiki/backends-hub.js").write_text(hub, encoding="utf-8")

    for f in fixtures.glob("*.json"):
        text = f.read_text(encoding="utf-8")
        if "/static/" in text:
            f.write_text(text.replace('"/static/', '"./static/'), encoding="utf-8")

    (OUT / "README.txt").write_text(
        """Cloud Labs Wiki — advisor demo pack
=====================================

What this is
------------
Self-contained snapshot of the Cloud Labs Wiki (Learn + Backends hub)
with backend/catalog/kernel data baked from the author's coordinator.

How to open (required)
----------------------
Do not double-click index.html (browsers block ES modules on file://).

From this folder:

  python -m http.server 8765

Then open:

  http://127.0.0.1:8765/index.html
  http://127.0.0.1:8765/index.html#learn/why
  http://127.0.0.1:8765/index.html#backends

What works offline
------------------
- Learn chapters + figures
- Backends gallery (hero images)
- Component / kernel reference for snapshotted labs

What does not run
-----------------
- Twin / live TeleOp / OPTIMIZE / hardware mutations
""",
        encoding="utf-8",
    )
    (OUT / "serve.ps1").write_text("python -m http.server 8765\n", encoding="utf-8")
    (OUT / "serve.sh").write_text("#!/bin/sh\npython3 -m http.server 8765\n", encoding="utf-8")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in OUT.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(OUT.parent).as_posix())

    print("OUT", OUT)
    print("ZIP", ZIP_PATH, "MB", round(ZIP_PATH.stat().st_size / 1e6, 2))
    print("files", sum(1 for p in OUT.rglob("*") if p.is_file()))
    try:
        REPO_ZIP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ZIP_PATH, REPO_ZIP)
        print("REPO_ZIP", REPO_ZIP)
    except Exception as exc:  # noqa: BLE001
        print("repo zip copy skipped", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
