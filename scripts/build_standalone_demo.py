"""Bundle the built frontend and the frozen run into one self-contained HTML file.

The frozen view needs no backend, so the demo can be a single file that opens from
disk or any static host -- which is what makes it something you can hand to someone.

Reads frontend/dist (run `pnpm --dir frontend run build` first) and writes
frontend/dist-standalone/rulearena-demo.html with the CSS, the JS bundle and the frozen
payload inlined. The payload is injected as window.__FROZEN_DEMO__ so no fetch happens.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
FROZEN = ROOT / "frontend" / "public" / "frozen" / "golden-run.json"
OUTPUT = ROOT / "frontend" / "dist-standalone" / "rulearena-demo.html"
# Same page without the document wrapper, for hosts that supply their own <body>.
FRAGMENT = ROOT / "frontend" / "dist-standalone" / "rulearena-demo.fragment.html"


def _read_asset(pattern: str) -> str:
    matches = sorted((DIST / "assets").glob(pattern))
    if not matches:
        raise SystemExit(f"no built asset matching {pattern}; run the frontend build first")
    return matches[-1].read_text(encoding="utf-8")


def main() -> int:
    index = (DIST / "index.html").read_text(encoding="utf-8")
    css = _read_asset("*.css")
    js = _read_asset("*.js")
    payload = json.loads(FROZEN.read_text(encoding="utf-8"))

    # </script> inside the payload would close the tag early; JSON never needs the raw
    # sequence, so escaping it is safe.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    favicon = re.search(r'<link rel="icon"[^>]*>', index)
    favicon_tag = favicon.group(0) if favicon else ""

    html = f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    {favicon_tag}
    <title>RuleArena 演示 · 模型找到的反例</title>
    <meta
      name="description"
      content="AI 搜索电商规则的异常操作组合，真实 API 重放，确定性 Oracle 裁决。"
    />
    <style>{css}</style>
  </head>
  <body>
    <div id="root"></div>
    <script>window.__FROZEN_DEMO__ = {data};</script>
    <script type="module">{js}</script>
  </body>
</html>
"""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")

    # The title is the artifact's name wherever it is listed, so it stays the product
    # name; the description below carries the explanation.
    fragment = f"""<title>RuleArena</title>
<meta
  name="description"
  content="AI 搜索电商规则的异常操作组合，真实 API 重放，确定性 Oracle 裁决：模型找到的反例与证据链。"
/>
<style>{css}</style>
<div id="root"></div>
<script>window.__FROZEN_DEMO__ = {data};</script>
<script type="module">{js}</script>
"""
    FRAGMENT.write_text(fragment, encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")
    print(f"wrote {FRAGMENT} ({FRAGMENT.stat().st_size / 1024:.0f} KB)")
    print("open it directly in a browser, or upload it to any static host")
    return 0


if __name__ == "__main__":
    sys.exit(main())
