#!/usr/bin/env bash
# Publish docs/wiki to the GitHub Wiki (flat pages — no page/dir name collisions).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/.github-wiki-build"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

ROOT="$ROOT" BUILD="$BUILD" python3 <<'PY'
from pathlib import Path
import re, shutil, os

src = Path(os.environ["ROOT"]) / "docs/wiki"
out = Path(os.environ["BUILD"])
if out.exists():
    shutil.rmtree(out)
out.mkdir()

FRONT = re.compile(r"^---\n.*?\n---\n+", re.S)
skip = {"README.md", "Inkobold.md"}
seen = {}
for p in sorted(src.rglob("*.md")):
    if p.name in skip:
        continue
    if p.name in seen:
        raise SystemExit(f"Duplicate wiki page basename: {p.name} ({seen[p.name]} vs {p})")
    seen[p.name] = p
    text = FRONT.sub("", p.read_text(encoding="utf-8")).strip() + "\n"
    # Always flat: GitHub Wiki breaks when Tools.md coexists with Tools/
    (out / p.name).write_text(text, encoding="utf-8")
print(f"Built {len(list(out.glob('*.md')))} flat wiki pages")
PY

cd "$TMP"
git clone "https://github.com/Teratoy/Inkobold.wiki.git" wiki
cd wiki
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$BUILD"/. .
git add -A
if git diff --cached --quiet; then
  echo "No wiki changes."
  exit 0
fi
git commit -m "Flatten wiki pages (fix Tools/Effects link breakage)"
git push origin HEAD
echo "Published: https://github.com/Teratoy/Inkobold/wiki"
