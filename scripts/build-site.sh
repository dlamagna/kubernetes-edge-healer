#!/usr/bin/env bash
# build-site.sh
# Assembles the project website into dist/site: the pages under site/ plus the paper and poster
# PDFs from the repository root (published at site/assets/, see site/common/js/site.js).
# Used by .github/workflows/pages.yml; locally, preview it with:
#   scripts/build-site.sh && python3 -m http.server -d dist/site 8080

set -euo pipefail
cd "$(dirname "$0")/.."

OUT=dist/site
rm -rf "$OUT"
mkdir -p "$OUT"
cp -R site/. "$OUT/"
mkdir -p "$OUT/assets"
cp lamagna_edge_healer_final.pdf lamagna_edge_healer_poster.pdf "$OUT/assets/"
touch "$OUT/.nojekyll"
echo "Built $OUT"
