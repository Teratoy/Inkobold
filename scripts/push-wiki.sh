#!/usr/bin/env bash
# Publish docs/wiki to the GitHub Wiki (requires wiki already initialized once).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/.github-wiki-build"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

python3 - <<PY
from pathlib import Path
import re, shutil
src = Path("$ROOT") / "docs/wiki"
out = Path("$BUILD")
if out.exists():
    shutil.rmtree(out)
out.mkdir()
FRONT = re.compile(r"^---\n.*?\n---\n+", re.S)
skip = {"README.md", "Inkobold.md"}
for p in src.rglob("*.md"):
    if p.name in skip:
        continue
    text = FRONT.sub("", p.read_text(encoding="utf-8")).strip() + "\n"
    dest = out / p.relative_to(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
print(f"Built {len(list(out.rglob('*.md')))} wiki pages")
PY

cd "$TMP"
git clone "https://github.com/Teratoy/Inkobold.wiki.git" wiki
cd wiki
# Replace all pages with build (keep .git)
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$BUILD"/. .
git add -A
if git diff --cached --quiet; then
  echo "No wiki changes."
  exit 0
fi
git commit -m "Sync wiki from docs/wiki"
git push origin HEAD
echo "Published: https://github.com/Teratoy/Inkobold/wiki"
