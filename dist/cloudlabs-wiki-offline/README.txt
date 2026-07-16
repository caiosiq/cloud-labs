Cloud Labs Wiki — offline HTML pack
====================================

Open index.html from this folder (double-click is fine).
No local server and no localhost connection required.

Contents
--------
- index.html                 Learn + Backends hub UI (static)
- guides/*.md                Learn markdown sources
- guides/backends-review.md  Full mock.default text dump
- guides/figures/            Diagram SVGs
- backends-images/           Mock bench hero PNG (also inlined in index.html)
- data/mock-default.json     Baked catalog + kernels snapshot
- data/mock-component_library.json  Raw mock component library

Backends section
----------------
Matches the live Wiki Backends page layout:
  gallery card (hero image) → mock.default → Overview / Components / Kernels
All mock catalog detail is pre-rendered in the HTML. No API fetch.
The mock hero is both a file under backends-images/ and embedded in the HTML
so it shows even when browsing file:// with broken relative paths.

Regenerate
----------
  python scripts/ops/build_wiki_offline_html_zip.py

(Server optional — builder reads mock lab_view from disk.)
