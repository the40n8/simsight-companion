#!/bin/zsh
# Build the macOS mod into dist/SimSight:
#   SimSight.ts4script     compiled from Scripts/ with Python 3.7
#   simsight-bridge        phone companion: web app + API (Go, universal)
#   simsight-say           speech companion (Swift, universal)
#   SimSight_UI.package    from the pinned TS4 Access release (checksum verified;
#                          the TS4 Access release names it TS4_Access_UI.package)
#   cloudflared            Cloudflare's tunnel client, pinned and signature-checked
# Env: PY37 = a Python 3.7 interpreter (default: micromamba env below).
# Needs: swiftc (Xcode Command Line Tools), go, pnpm.
set -euo pipefail

ROOT="${0:A:h:h}"
MAC="$ROOT/mac"
COMP="$ROOT/companion"
DIST="$ROOT/dist/SimSight"
CACHE="$MAC/.cache"
PY37="${PY37:-$HOME/.local/micromamba/envs/py37/bin/python}"

[[ -x "$PY37" ]] || { echo "Python 3.7 not found at $PY37 (set PY37=...). See .github/README.md"; exit 1; }
for tool in swiftc go pnpm; do
  command -v $tool >/dev/null || { echo "$tool not found. See .github/README.md"; exit 1; }
done
mkdir -p "$DIST" "$CACHE"

echo "== Python mod"
"$PY37" -I "$MAC/build.py" "$ROOT" "$DIST"

echo "== Speech companion"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos13" -o "$CACHE/simsight-say-$arch" "$COMP/speaker/SimSightSay.swift"
done
lipo -create -output "$DIST/simsight-say" "$CACHE/simsight-say-arm64" "$CACHE/simsight-say-x86_64"

echo "== Phone app"
(cd "$COMP/web" && pnpm install --frozen-lockfile --silent && pnpm build >/dev/null)
rm -rf "$COMP/bridge/internal/webui/dist"
cp -R "$COMP/web/build" "$COMP/bridge/internal/webui/dist"

echo "== Bridge"
for arch in arm64 amd64; do
  (cd "$COMP/bridge" && CGO_ENABLED=0 GOOS=darwin GOARCH=$arch \
    go build -trimpath -ldflags "-s -w -X main.version=${SIMSIGHT_VERSION:-dev}" -o "$CACHE/simsight-bridge-$arch" ./cmd/simsight-bridge)
done
lipo -create -output "$DIST/simsight-bridge" "$CACHE/simsight-bridge-arm64" "$CACHE/simsight-bridge-amd64"
chmod +x "$DIST/simsight-say" "$DIST/simsight-bridge"

echo "== Game interface (TS4 Access release)"
read -r REPO RELEASE ASSET SHA < <(python3 -I -c '
import json, sys; p = json.load(open(sys.argv[1]))
print(p["repo"], p["release"], p["asset"], p["sha256"])' "$MAC/ui-package.json")
ZIP="$CACHE/$RELEASE-$ASSET"
if [[ ! -f "$ZIP" ]]; then
  curl -fL --progress-bar -o "$ZIP.part" "https://github.com/$REPO/releases/download/$RELEASE/$ASSET"
  mv "$ZIP.part" "$ZIP"
fi
GOT=$(shasum -a 256 "$ZIP" | cut -d' ' -f1)
[[ "$GOT" == "$SHA" ]] || { rm -f "$ZIP"; echo "Checksum mismatch for $ASSET ($GOT). Not used."; exit 1; }
# The game interface (prebuilt Flash UI) comes from the TS4 Access release.
unzip -o -q -j "$ZIP" "Mods/TS4_Access/TS4_Access_UI.package" -d "$CACHE"
cp -f "$CACHE/TS4_Access_UI.package" "$DIST/SimSight_UI.package"
rm -f "$DIST/README.txt" "$DIST/CONTROLS.txt" "$DIST/CHEATS.txt" "$DIST/README-MAC.md"

echo "== cloudflared (Cloudflare quick tunnel)"
read -r CF_VER CF_TEAM CF_ARM CF_AMD < <(python3 -I -c '
import json, sys; p = json.load(open(sys.argv[1]))
print(p["version"], p["team_id"], p["sha256"]["arm64"], p["sha256"]["amd64"])' "$MAC/cloudflared.json")
for arch in arm64 amd64; do
  TGZ="$CACHE/cloudflared-$CF_VER-$arch.tgz"
  [[ -f "$TGZ" ]] || curl -fsSL -o "$TGZ" "https://github.com/cloudflare/cloudflared/releases/download/$CF_VER/cloudflared-darwin-$arch.tgz"
  rm -rf "$CACHE/cf-$arch" && mkdir -p "$CACHE/cf-$arch" && tar -xzf "$TGZ" -C "$CACHE/cf-$arch"
  WANT=$([[ $arch == arm64 ]] && echo $CF_ARM || echo $CF_AMD)
  GOT=$(shasum -a 256 "$CACHE/cf-$arch/cloudflared" | cut -d' ' -f1)
  [[ "$GOT" == "$WANT" ]] || { echo "cloudflared $arch checksum mismatch ($GOT)"; exit 1; }
done
lipo -create -output "$DIST/cloudflared" "$CACHE/cf-arm64/cloudflared" "$CACHE/cf-amd64/cloudflared"
codesign --verify --strict --all-architectures "$DIST/cloudflared"
CF_SIG=$(codesign -dv "$DIST/cloudflared" 2>&1)   # captured: grep -q on a pipe can fail it (pipefail)
[[ "$CF_SIG" == *"TeamIdentifier=$CF_TEAM"* ]] || { echo "cloudflared is not signed by Cloudflare"; exit 1; }
[[ -f "$CACHE/cloudflared-LICENSE-$CF_VER" ]] || curl -fsSL -o "$CACHE/cloudflared-LICENSE-$CF_VER" "https://raw.githubusercontent.com/cloudflare/cloudflared/$CF_VER/LICENSE"
cp "$CACHE/cloudflared-LICENSE-$CF_VER" "$DIST/cloudflared-LICENSE.txt"

cp "$ROOT/LICENSE" "$DIST/LICENSE.txt"
cp "$MAC/PLAYERS.md" "$DIST/README.md"
cp "$ROOT/docs/mac/KEYBOARD.md" "$ROOT/docs/mac/COMMANDS.md" "$DIST/"
echo "== Done: $DIST"
ls -l "$DIST"
