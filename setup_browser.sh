#!/usr/bin/env bash
# Build STAC Browser (https://github.com/radiantearth/stac-browser) into site/browser/
#
# Needs git and Node.js/npm.
#
# Local use (browse at http://127.0.0.1:8000/browser/):
#     ./setup_browser.sh            # port 8000
#     PORT=8080 ./setup_browser.sh
#
# Published use (the GitHub Actions workflow sets these):
#     SITE_URL=https://user.github.io/repo ./setup_browser.sh
#   SITE_URL is the URL the site/ folder is served at. The browser is then
#   built for SITE_URL/browser/ and opens SITE_URL/stac/collection.json.
set -euo pipefail

VERSION="${STAC_BROWSER_VERSION:-v5.1.0}"
PORT="${PORT:-8000}"
SITE_URL="${SITE_URL:-http://127.0.0.1:${PORT}}"
SITE_URL="${SITE_URL%/}"
# Path part of SITE_URL ("" locally, "/repo" on GitHub Pages project sites)
SITE_PATH="$(printf '%s' "$SITE_URL" | sed -E 's#^[a-z]+://[^/]+##')"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/.stac-browser-src"

if [ ! -d "$SRC/.git" ]; then
  rm -rf "$SRC"
  git clone --depth 1 --branch "$VERSION" https://github.com/radiantearth/stac-browser.git "$SRC"
fi
cd "$SRC"
npm ci --no-audit --no-fund

# STAC Browser v5 reads its options from SB_* environment variables.
# historyMode=hash because neither serve.py nor GitHub Pages does URL rewriting.
SB_catalogUrl="${SITE_URL}/stac/collection.json" \
SB_catalogTitle="ECMWF open data (IFS 0.1° oper): metdx demo" \
SB_historyMode="hash" \
SB_pathPrefix="${SITE_PATH}/browser/" \
SB_allowExternalAccess="false" \
  npm run build

rm -rf "$HERE/site/browser"
cp -R "$SRC/dist" "$HERE/site/browser"
echo "STAC Browser $VERSION built into site/browser/ for ${SITE_URL}/browser/"
