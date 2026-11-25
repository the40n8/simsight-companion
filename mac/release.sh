#!/bin/zsh
# Build a player release: dist/release/SimSight-Companion-mac.zip and
# SimSight-Companion-windows.zip, the one-command installers install.sh
# (mac/get.sh) and install.ps1 (windows/get.ps1), and SHA256SUMS. Nothing is published
# unless --publish is given (then: a GitHub release on the40n8/simsight-companion).
#
#   mac/release.sh             build and check the files only
#   mac/release.sh --publish   also create the GitHub release (public)
set -euo pipefail
ROOT="${0:A:h:h}"
cd "$ROOT"
PUBLISH=0
[[ "${1:-}" == --publish ]] && PUBLISH=1

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Uncommitted changes: commit them first, a release is built from a commit."; exit 1
fi
TAG="v$(date +%Y.%m.%d)"
n=2
while git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || gh release view "$TAG" >/dev/null 2>&1; do
  TAG="v$(date +%Y.%m.%d)-$n"; n=$((n + 1))
done
GAME=$(python3 -c 'import json;print(json.load(open("mac/ui-package.json"))["game_version"])')
UPSTREAM=$(python3 -c 'import json;print(json.load(open("mac/ui-package.json"))["release"])')

# The bridge knows its version: the setup page offers newer releases.
export SIMSIGHT_VERSION="$TAG"
./mac/build.sh
SKIP_MAC_BUILD=1 ./windows/build.sh
./mac/test.sh

OUT="$ROOT/dist/release"
PKG="$OUT/SimSight-Companion"
rm -rf "$OUT" && mkdir -p "$PKG"
cp -Rp dist/SimSight "$PKG/SimSight"
cp -p mac/install.sh mac/uninstall.sh "$PKG/"
cat > "$PKG/Install SimSight Companion.command" <<'CMD'
#!/bin/zsh
# Double-click to install or update SimSight (quit the game first).
cd "${0:A:h}" && ./install.sh
echo; echo "You can close this window."
CMD
cat > "$PKG/Uninstall SimSight Companion.command" <<'CMD'
#!/bin/zsh
# Double-click to remove SimSight (saves are not touched).
cd "${0:A:h}" && ./uninstall.sh
echo; echo "SimSight removed. You can close this window."
CMD
cp -p "mac/SimSight Setup.command" "$PKG/"
chmod +x "$PKG"/*.command "$PKG"/*.sh
# The optional meeting point, with the phone app it serves.
mkdir -p "$PKG/meeting-point"
cp -Rp companion/meeting-point/src "$PKG/meeting-point/src"
cp -Rp companion/web/build "$PKG/meeting-point/app"
sed 's#"directory": "../web/build"#"directory": "./app"#' companion/meeting-point/wrangler.jsonc > "$PKG/meeting-point/wrangler.jsonc"
grep -q '"./app"' "$PKG/meeting-point/wrangler.jsonc"
printf 'release=%s\ncommit=%s\ngame=%s\nui=%s\n' "$TAG" "$(git rev-parse --short HEAD)" "$GAME" "$UPSTREAM" > "$PKG/VERSION"
cat > "$PKG/READ ME FIRST.txt" <<TXT
SimSight Companion ($TAG), accessibility mod for The Sims 4 on Mac (game $GAME),
with a phone companion app. Based on TS4 Access by tyjokr, LeonianUniverse
and contributors (MIT licence).

Install (quit the game first). Easiest, in Terminal:
  curl -fsSL https://github.com/the40n8/simsight-companion/releases/latest/download/install.sh | zsh

Or double-click "Install SimSight Companion.command". macOS may say it cannot
verify the developer: then open System Settings > Privacy & Security, find
the message about it near the bottom and choose Open Anyway, then
double-click it again.

Then start The Sims 4 and load a household. Option+Command+A opens the
accessibility menu; "phone companion" there connects a phone.

Optional settings (fixed phone address, meeting point, disconnect phones):
double-click "SimSight Setup.command", or choose "companion setup" in the
accessibility menu.

Guides: https://github.com/the40n8/simsight-companion/tree/main/docs/mac
Remove: double-click "Uninstall SimSight Companion.command" (saves are not touched).
TXT
( cd "$OUT" && ditto -c -k --sequesterRsrc --keepParent SimSight-Companion SimSight-Companion-mac.zip )
cp -p mac/get.sh "$OUT/install.sh"

# Windows: same layout, a plain zip (no Mac metadata), text files with
# Windows line endings.
WPKG="$OUT/windows/SimSight-Companion"
mkdir -p "$WPKG"
cp -Rp dist/SimSight-windows/SimSight "$WPKG/SimSight"
cp -p windows/install.ps1 "windows/Install SimSight Companion.cmd" "$WPKG/"
printf 'release=%s\r\ncommit=%s\r\ngame=%s\r\nui=%s\r\n' "$TAG" "$(git rev-parse --short HEAD)" "$GAME" "$UPSTREAM" > "$WPKG/VERSION"
sed 's/$/\r/' > "$WPKG/READ ME FIRST.txt" <<TXT
SimSight Companion ($TAG), accessibility mod for The Sims 4 on Windows (game
$GAME), with a phone companion app. Based on TS4 Access by tyjokr,
LeonianUniverse and contributors (MIT licence).

Install (quit the game first). Easiest, in PowerShell (Windows+X, Terminal):
  irm https://github.com/the40n8/simsight-companion/releases/latest/download/install.ps1 | iex

Or open "Install SimSight Companion.cmd" in this folder. If Windows says it
protected your PC, choose More info, then Run anyway.

Then start your screen reader (NVDA or JAWS), start The Sims 4 and load a
household. Control+Alt+H opens the accessibility menu; "phone companion"
there connects a phone, "companion setup" has its settings.

Guides: SimSight\README.txt, CONTROLS.txt and CHEATS.txt, also at
https://github.com/the40n8/simsight-companion/tree/main/docs/windows
Remove: open "Install SimSight Companion.cmd" again and type r (saves are not touched).
TXT
find "$OUT/windows" -name .DS_Store -delete
( cd "$OUT/windows" && zip -q -r -X ../SimSight-Companion-windows.zip SimSight-Companion )
cp -p windows/get.ps1 "$OUT/install.ps1"
( cd "$OUT" && shasum -a 256 SimSight-Companion-mac.zip install.sh SimSight-Companion-windows.zip install.ps1 > SHA256SUMS )
echo "Release $TAG built:"; ls -la "$OUT"; cat "$OUT/SHA256SUMS"

if (( PUBLISH )); then
  NOTES="$OUT/notes.md"
  cat > "$NOTES" <<MD
SimSight Companion for The Sims 4 $GAME on Mac and Windows (game interface from TS4 Access $UPSTREAM).

**Mac: install or update** (quit the game first), in Terminal:

\`\`\`sh
curl -fsSL https://github.com/the40n8/simsight-companion/releases/latest/download/install.sh | zsh
\`\`\`

Or download SimSight-Companion-mac.zip and double-click "Install SimSight Companion.command"
(see READ ME FIRST.txt inside). Guides: [docs/mac](https://github.com/the40n8/simsight-companion/tree/main/docs/mac).

**Windows: install, update or remove** (quit the game first), in PowerShell:

\`\`\`powershell
irm https://github.com/the40n8/simsight-companion/releases/latest/download/install.ps1 | iex
\`\`\`

Or download SimSight-Companion-windows.zip, extract it and open "Install SimSight Companion.cmd".
Windows is **beta**: the installer, phone companion and NVDA speech are tested on Windows 11,
the mod inside the game on Windows not yet. Please report what works and what does not.

**Updating later:** accessibility menu > companion setup > Check for updates.
Guides: [docs/windows](https://github.com/the40n8/simsight-companion/tree/main/docs/windows).
MD
  git tag "$TAG" && git push -q origin "$TAG"
  gh release create "$TAG" "$OUT/SimSight-Companion-mac.zip" "$OUT/install.sh" \
    "$OUT/SimSight-Companion-windows.zip" "$OUT/install.ps1" "$OUT/SHA256SUMS" \
    --repo the40n8/simsight-companion --title "SimSight Companion $TAG" --notes-file "$NOTES" --latest
fi
