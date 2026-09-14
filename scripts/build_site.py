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
SEARCH_DEMO = ROOT / "frontend" / "dist-standalone" / "rulearena-demo.html"
GATE_DIAGRAM = ROOT / "site" / "gate-diagram.html"
OUTPUT = ROOT / "_site"

PAYLOAD_TOKEN = "__REFUND_DEMO_JSON__"


def _payload() -> str:
    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    # `</script>` inside a `type="application/json"` block would close the tag early.
    # JSON never needs the raw sequence, so escaping it is safe and lossless.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def main() -> int:
    for required in (SEARCH_DEMO, GATE_DIAGRAM):
        if not required.exists():
            raise SystemExit(
                f"{required} is missing; run the frontend build, "
                "`scripts/build_standalone_demo.py`, and the archify render first"
            )
    page = SOURCE.read_text(encoding="utf-8")
    if PAYLOAD_TOKEN not in page:
        raise SystemExit(f"{SOURCE} no longer contains {PAYLOAD_TOKEN}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "index.html").write_text(
        page.replace(PAYLOAD_TOKEN, _payload()), encoding="utf-8"
    )
    (OUTPUT / "demo.html").write_text(
        SEARCH_DEMO.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (OUTPUT / "diagram.html").write_text(
        GATE_DIAGRAM.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for name in ("index.html", "demo.html", "diagram.html"):
        print(f"wrote {OUTPUT / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
