"""Assemble the published site: the landing page plus the standalone search demo.

Two inputs, both already committed deliverables:

* `site/index.html` -- the page a resume links to. It carries its own markup and script
  and is self-contained apart from the frozen comparison, which is injected here so the
  page and the export cannot drift apart;
* `frontend/dist-standalone/rulearena-demo.html` -- the single-file search-counterexample
  demo, built by `scripts/build_standalone_demo.py` (run the frontend build first).

Output is `_site/`, which is what gets uploaded to GitHub Pages. Nothing here reaches the
network: the site is static and stays that way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "site" / "index.html"
FROZEN = ROOT / "frontend" / "public" / "frozen" / "refund-gate-demo.json"
SUITE = ROOT / "frontend" / "public" / "frozen" / "refund-suite-results.json"
SEARCH_DEMO = ROOT / "frontend" / "dist-standalone" / "rulearena-demo.html"
GATE_DIAGRAM = ROOT / "site" / "gate-diagram.html"
OUTPUT = ROOT / "_site"

PAYLOAD_TOKEN = "__REFUND_DEMO_JSON__"
SUITE_TOKEN = "__SUITE_RESULTS_JSON__"

# The console is dark; the diagram viewer follows prefers-color-scheme, which would
# leave a light panel inside a dark shell on a light-mode machine. Pinning its theme
# in the built copy keeps the embedded artifact untouched and the page coherent.
DARK_THEME_SNIPPET = (
    "<script>document.documentElement.setAttribute('data-theme','dark');"
    "setTimeout(function(){document.documentElement.setAttribute('data-theme','dark')},0);"
    "</" + "script>"
)


def _payload(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    # `</script>` inside a `type="application/json"` block would close the tag early.
    # JSON never needs the raw sequence, so escaping it is safe and lossless.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def main() -> int:
    for required in (SEARCH_DEMO, GATE_DIAGRAM, FROZEN, SUITE):
        if not required.exists():
            raise SystemExit(
                f"{required} is missing; run the frontend build, "
                "`scripts/build_standalone_demo.py`, and the archify render first"
            )
    page = SOURCE.read_text(encoding="utf-8")
    for token in (PAYLOAD_TOKEN, SUITE_TOKEN):
        if token not in page:
            raise SystemExit(f"{SOURCE} no longer contains {token}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "index.html").write_text(
        page.replace(PAYLOAD_TOKEN, _payload(FROZEN)).replace(
            SUITE_TOKEN, _payload(SUITE)
        ),
        encoding="utf-8",
    )
    (OUTPUT / "demo.html").write_text(
        SEARCH_DEMO.read_text(encoding="utf-8"), encoding="utf-8"
    )
    diagram = GATE_DIAGRAM.read_text(encoding="utf-8")
    (OUTPUT / "diagram.html").write_text(
        diagram.replace("</body>", DARK_THEME_SNIPPET + "</body>", 1), encoding="utf-8"
    )
    for name in ("index.html", "demo.html", "diagram.html"):
        print(f"wrote {OUTPUT / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
