#!/bin/zsh
# SimSight Companion: install or update with one command (published with each
# release as install.sh):
#
#   curl -fsSL https://github.com/the40n8/simsight-companion/releases/latest/download/install.sh | zsh
#
# Remove it:   ... | zsh -s uninstall
#
# Downloads the latest release, checks it against its SHA-256 fingerprint,
# then runs the installer inside it (backups first, saves never modified).
# Files fetched this way are not blocked by macOS as "downloaded from the
# internet". Each step is printed one line at a time (VoiceOver reads them as
# they come); with VoiceOver off, the Mac voice also says them.

# Everything is in main, called on the last line: a half-downloaded script
# does nothing.
main() {
  set -euo pipefail
  local repo="${SIMSIGHT_REPO:-the40n8/simsight-companion}"
  local base="${SIMSIGHT_BASE:-https://github.com/$repo/releases/latest/download}"
  local userdir="$HOME/Documents/Electronic Arts/The Sims 4"
  local zip="SimSight-Companion-mac.zip"

  # VoiceOver already reads the Terminal: only speak when it is off.
  if [[ -z "${SIMSIGHT_SPEAK:-}" ]]; then
    if pgrep -xq VoiceOver; then export SIMSIGHT_SPEAK=0; else export SIMSIGHT_SPEAK=1; fi
  fi
  tell() {
    print -r -- "$1"
    [[ "$SIMSIGHT_SPEAK" == 1 ]] && say -- "$1" 2>/dev/null || true
  }
  # Answers come from the keyboard, not from the downloaded script.
  ask() {
    local answer=""
    tell "$1" >&2
    if [[ -r /dev/tty ]]; then { read -r answer < /dev/tty; } 2>/dev/null || true; fi
    print -r -- "$answer"
  }

  [[ "$(uname -s)" == Darwin ]] || { tell "SimSight Companion only runs on macOS for now."; exit 1; }

  if [[ "${1:-}" == uninstall ]]; then
    if [[ -x "$userdir/Mods/SimSight/uninstall.sh" ]]; then
      "$userdir/Mods/SimSight/uninstall.sh"
      tell "SimSight Companion is removed. Your saves were not touched; the mod folder was moved to SimSight Backups."
    else
      tell "SimSight Companion is not installed. Nothing was changed."
    fi
    return
  fi

  local what="install"
  [[ -d "$userdir/Mods/SimSight" ]] && what="update"
  tell "SimSight Companion, accessibility mod for The Sims 4. This will $what it."
  if pgrep -f "The Sims 4.app/Contents/MacOS" >/dev/null; then
    tell "The Sims 4 is running. Save, quit the game, then run this command again. Nothing was changed."; exit 1
  fi
  [[ -f "$userdir/Options.ini" ]] || {
    tell "The Sims 4 settings were not found. Start the game once, quit it, then run this command again."; exit 1; }
  local go
  go=$(ask "Press Return to $what, or type n then Return to stop.")
  [[ "$go" == [nN]* ]] && { tell "Stopped. Nothing was changed."; exit 0; }

  local tmp
  tmp=$(mktemp -d -t simsight)
  trap "rm -rf '$tmp'" EXIT
  tell "Downloading."
  curl -fsSL --retry 2 -o "$tmp/$zip" "$base/$zip" || { tell "Download failed. Check the internet connection and try again."; exit 1; }
  curl -fsSL --retry 2 -o "$tmp/SHA256SUMS" "$base/SHA256SUMS" || { tell "Download failed. Check the internet connection and try again."; exit 1; }
  local want have
  want=$(awk -v f="$zip" '$2 == f || $2 == "*"f { print $1 }' "$tmp/SHA256SUMS")
  have=$(shasum -a 256 "$tmp/$zip" | awk '{ print $1 }')
  if [[ -z "$want" || "$want" != "$have" ]]; then
    tell "The download does not match its fingerprint, so it was not installed. Try again later."; exit 1
  fi
  tell "Download checked."
  ditto -x -k "$tmp/$zip" "$tmp/x"
  local dir="$tmp/x/SimSight-Companion"
  [[ -x "$dir/install.sh" ]] || { tell "The download is incomplete. Nothing was changed."; exit 1; }
  [[ -f "$dir/VERSION" ]] && print -r -- "Version: $(sed -n 's/^release=//p' "$dir/VERSION")"
  "$dir/install.sh"
}

main "$@"
