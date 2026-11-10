#!/usr/bin/env python3
"""Syntax-check inline <script> blocks in the site's HTML files.

The whole app lives in index.html (and biapia/index.html) with inline JS —
there is no bundler to catch a syntax error before deploy, and Netlify
publishes whatever is in the repo. Extract each inline block and run
`node --check` on it.

Usage: python3 scripts/check_inline_js.py [html ...]   (default: both sites)
Requires: node on PATH.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def main() -> int:
    files = [Path(p) for p in sys.argv[1:]] or [ROOT / "index.html", ROOT / "biapia" / "index.html"]
    failed = False
    for f in files:
        blocks = SCRIPT_RE.findall(f.read_text(encoding="utf-8"))
        for i, block in enumerate(blocks):
            if not block.strip():
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as t:
                t.write(block)
                tmp = t.name
            r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
            if r.returncode:
                failed = True
                print(f"FAIL {f} script block #{i}:\n{r.stderr.strip()}")
            else:
                print(f"ok   {f} script block #{i} ({len(block)} chars)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
