#!/bin/zsh
# Build the Windows mod into dist/SimSight-windows/SimSight, on the Mac:
#   SimSight.ts4script     compiled from Scripts/ with Python 3.7, with the
#                          ctypes package and the Prism binding
#   simsight-bridge.exe    phone companion: web app + API (Go, windows/amd64)
#   cloudflared.exe        Cloudflare's tunnel client, pinned by checksum
#   SimSight_SpeechPack/   Prism (screen reader speech), official release,
#                          pinned by checksum, with its licences
#   SimSight_UI.package    the game interface, from the Mac build
# The phone app and the game interface come from mac/build.sh, run first.
# Nothing here runs a Windows program.
set -euo pipefail

ROOT="${0:A:h:h}"
WIN="$ROOT/windows"
CACHE="$ROOT/mac/.cache"
OUT="$ROOT/dist/SimSight-windows/SimSight"
PY37="${PY37:-$HOME/.local/micromamba/envs/py37/bin/python}"

[[ "${SKIP_MAC_BUILD:-0}" == 1 ]] || "$ROOT/mac/build.sh" >/dev/null
[[ -f "$ROOT/dist/SimSight/SimSight_UI.package" ]] || { echo "Run mac/build.sh first."; exit 1; }
rm -rf "$OUT" && mkdir -p "$OUT" "$CACHE"

pin() { python3 -I -c 'import json, sys; p = json.load(open(sys.argv[1])); print(p[sys.argv[2]][sys.argv[3]])' "$WIN/pins.json" "$1" "$2"; }

echo "== Python mod (Windows)"
"$PY37" -I "$ROOT/mac/build.py" "$ROOT" "$OUT" windows

echo "== Bridge (windows/amd64)"
(cd "$ROOT/companion/bridge" && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 \
  go build -trimpath -ldflags "-s -w -X main.version=${SIMSIGHT_VERSION:-dev}" -o "$OUT/simsight-bridge.exe" ./cmd/simsight-bridge)

echo "== cloudflared (Windows)"
CF_VER=$(pin cloudflared version); CF_ASSET=$(pin cloudflared asset); CF_SHA=$(pin cloudflared sha256)
CF="$CACHE/cloudflared-$CF_VER-$CF_ASSET"
[[ -f "$CF" ]] || curl -fsSL -o "$CF" "https://github.com/cloudflare/cloudflared/releases/download/$CF_VER/$CF_ASSET"
GOT=$(shasum -a 256 "$CF" | cut -d' ' -f1)
[[ "$GOT" == "$CF_SHA" ]] || { rm -f "$CF"; echo "cloudflared Windows checksum mismatch ($GOT)"; exit 1; }
cp "$CF" "$OUT/cloudflared.exe"
[[ -f "$CACHE/cloudflared-LICENSE-$CF_VER" ]] || curl -fsSL -o "$CACHE/cloudflared-LICENSE-$CF_VER" "https://raw.githubusercontent.com/cloudflare/cloudflared/$CF_VER/LICENSE"
cp "$CACHE/cloudflared-LICENSE-$CF_VER" "$OUT/cloudflared-LICENSE.txt"

echo "== Prism speech pack"
P_REPO=$(pin prism repo); P_VER=$(pin prism version); P_ASSET=$(pin prism asset)
P_SHA=$(pin prism sha256); P_DLL=$(pin prism dll); P_DLL_SHA=$(pin prism dll_sha256)
PZ="$CACHE/prism-$P_VER/$P_ASSET"
mkdir -p "${PZ:h}"
[[ -f "$PZ" ]] || curl -fsSL -o "$PZ" "https://github.com/$P_REPO/releases/download/$P_VER/$P_ASSET"
GOT=$(shasum -a 256 "$PZ" | cut -d' ' -f1)
[[ "$GOT" == "$P_SHA" ]] || { rm -f "$PZ"; echo "Prism checksum mismatch ($GOT)"; exit 1; }
PACK="$OUT/SimSight_SpeechPack"
mkdir -p "$PACK"
unzip -q -o "$PZ" "$P_DLL" 'LICENSES/*' NOTICE -d "$CACHE/prism-$P_VER/x"
cp "$CACHE/prism-$P_VER/x/$P_DLL" "$PACK/prism.dll"
GOT=$(shasum -a 256 "$PACK/prism.dll" | cut -d' ' -f1)
[[ "$GOT" == "$P_DLL_SHA" ]] || { echo "prism.dll checksum mismatch ($GOT)"; exit 1; }
cp -R "$CACHE/prism-$P_VER/x/LICENSES" "$PACK/LICENSES"
cp "$CACHE/prism-$P_VER/x/NOTICE" "$PACK/NOTICE.txt"
printf 'Prism %s, unmodified, for screen reader speech (NVDA, JAWS and others).\r\nSource: https://github.com/%s/tree/%s\r\nLicence: MPL-2.0 (LICENSES\\prism). Third-party licences: LICENSES.\r\n' \
  "${P_VER#v}" "$P_REPO" "$P_VER" > "$PACK/SOURCE.txt"

echo "== Interface, guides, licence"
cp "$ROOT/dist/SimSight/SimSight_UI.package" "$OUT/"
cp "$ROOT/LICENSE" "$OUT/LICENSE.txt"
cp "$ROOT/docs/windows/README.txt" "$ROOT/docs/windows/CONTROLS.txt" "$ROOT/docs/windows/CHEATS.txt" "$OUT/"
cp "$WIN/SimSight Setup.cmd" "$OUT/"
echo "== Done: $OUT"
ls -l "$OUT" "$PACK"
