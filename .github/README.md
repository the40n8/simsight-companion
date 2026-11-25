# SimSight Companion

Play The Sims 4 with a screen reader, on the Mac or Windows, and from your phone. An
independent project based on **[TS4 Access](https://github.com/LeonianUniverse478/Sims4Access)**,
the screen-reader mod for The Sims 4 by tyjokr, LeonianUniverse and contributors
(MIT licence; their credits and licence are kept here).
It speaks menus, dialogs, tooltips, notifications and what your Sim is doing:
with the macOS system voices on the Mac, through NVDA or JAWS on Windows.

Made for playing together: a sighted player on the mouse, a blind player
hearing what happens, and a phone app for either of them. Keyboard-only play
(scanner, build cursor, Ctrl+Alt shortcuts) comes from TS4 Access on Windows;
the Mac has its own keys ([KEYBOARD.md](../docs/mac/KEYBOARD.md)).

**Windows: new, not yet tried in game.** The Windows parts of TS4 Access
(speech, keyboard) are unchanged; the phone companion and the installer are
new there. Testers welcome.

**Status (Mac):** works in game on an Apple Silicon Mac, game 1.129.39.1230
(hover narration, Sim actions). The phone companion works in game (status,
feed, speed, Play menus, House map); its newest parts (game questions,
notifications, Needs attention) are still being tried. Early: expect rough
edges.

**Players:** read the [phone guide](../docs/mac/PHONE-GUIDE.md) (also in the
app's Help tab). To ask ChatGPT or another assistant about the mod, point it
at [`llms.txt`](https://raw.githubusercontent.com/the40n8/simsight-companion/main/llms.txt).

## Install (players)

### Windows

Quit The Sims 4, open PowerShell (Windows+X, then Terminal), paste this line
and press Enter:

```powershell
irm https://github.com/the40n8/simsight-companion/releases/latest/download/install.ps1 | iex
```

It writes each step as a line your screen reader reads, finds your Sims 4
folder (OneDrive and other languages too), backs up your settings and saves,
installs the mod with its speech pack (Prism, for NVDA and JAWS) and allows
mods in the game. Run it again to update, or to remove SimSight (type r).
Without PowerShell: `SimSight-Companion-windows.zip` from the latest release,
then **Install SimSight Companion.cmd**. Guides:
[docs/windows](../docs/windows/README.txt).

### Mac

Quit The Sims 4, open Terminal (Command+Space, type Terminal, Return), paste
this line and press Return:

```sh
curl -fsSL https://github.com/the40n8/simsight-companion/releases/latest/download/install.sh | zsh
```

It says each step (VoiceOver reads them; with VoiceOver off, the Mac voice
says them), asks you to press Return, backs up your settings and saves,
installs the mod and allows mods in the game. Run the same line again to
update; add `-s uninstall` at the end to remove it. Then start the game and
load a household: Option+Command+A opens the accessibility menu.

Without Terminal: download `SimSight-Companion-mac.zip` from the
[latest release](https://github.com/the40n8/simsight-companion/releases/latest)
and double-click **Install SimSight Companion.command** (macOS may ask you to allow
it in System Settings > Privacy & Security > Open Anyway).

Nothing else to set up: the voice follows the game's language, and the phone
app works right away (QR code or 6-digit code each session). Optional later:
a [fixed address](../docs/mac/FIXED-ADDRESS.md) (your own domain) or a
[meeting point](../docs/mac/MEETING-POINT.md) (free Cloudflare account).

Updates: **companion setup** in the accessibility menu checks for a new
version and installs it (when you quit the game, or at once with the game
closed), with the same backups as the installer. The game also says when a
new version is out.

## On the Mac

| | |
|---|---|
| Speech | `simsight-say` companion (macOS voices, AVSpeechSynthesizer) |
| Keyboard | Mac keys while the game is in front (see [KEYBOARD.md](../docs/mac/KEYBOARD.md)) |
| Main menu / Create a Sim speech | Not yet |
| Game interface (patched Flash UI) | Prebuilt file from the TS4 Access release (`mac/ui-package.json`) |
| Install | One Terminal line (`mac/get.sh`), or `mac/install.sh` from source |

## Build from source

Requirements: Xcode Command Line Tools (`swiftc`), Go, pnpm and a
**Python 3.7** (the game only loads 3.7 bytecode). On Apple Silicon, one way
to get Python 3.7:

```sh
mkdir -p ~/.local/micromamba && cd ~/.local/micromamba
curl -Ls https://micro.mamba.pm/api/micromamba/osx-64/latest | tar -xj bin/micromamba
MAMBA_ROOT_PREFIX=~/.local/micromamba ./bin/micromamba create -y \
  -p ~/.local/micromamba/envs/py37 --platform osx-64 -c conda-forge python=3.7
```

Then, with the game closed:

```sh
mac/build.sh      # -> dist/SimSight (mod, bridge + phone app, speaker, UI package)
mac/test.sh       # offline checks (fake game for the Mac-only mod code, Go tests)
mac/install.sh    # backs up Options.ini and saves, installs, enables script mods
mac/release.sh    # player release in dist/release (zip, SHA256SUMS, install.sh);
                  # --publish creates the GitHub release
```

Start the game, accept the mod list, load a household. Player notes:
[`mac/PLAYERS.md`](../mac/PLAYERS.md). Remove with `mac/uninstall.sh`
(`--restore-options` also restores Options.ini). `mac/narration.sh on|off`
switches narration from outside the game.

## Phone companion

While the game runs, `simsight-bridge` serves a small web app: live
household status, a feed announced by VoiceOver, the game's own interaction
menus, a room-by-room house map, game questions answered from the phone, and
quick actions for what needs attention. See the
[phone guide](../docs/mac/PHONE-GUIDE.md). No server of ours is involved.

1. Start the game. The bridge opens a Cloudflare quick tunnel by itself
   (bundled `cloudflared`, no account) so the phone works from any network;
   the Mac voice says "Phone link ready" and how to open the QR code.
2. Show the QR code: in game, accessibility menu (`simsight.hub` in the cheat
   console) > phone companion, or `simsight.companion`; from a terminal,
   `mac/phone-link.sh`. Both open http://127.0.0.1:47800/pair (this Mac only).
3. Scan it with the phone, then in Safari: Share > Add to Home Screen. The
   tunnel address changes each game session, so scan again next time.
4. Settings: **SimSight setup**, a page on the Mac (accessibility menu >
   companion setup, `SimSight Setup.command`, or `simsight-bridge setup`).
   It chooses the phone address (changing, fixed, or home Wi-Fi only), the
   optional meeting point, and disconnects every phone with a new pairing
   key. It writes
   `~/Library/Application Support/SimSightCompanion/config.json`.
   `mac/wifi-test.sh` checks whether the phone reaches the Mac directly over
   Wi-Fi (works without the game). For a fixed address instead of a random
   one each session, see [docs/mac/FIXED-ADDRESS.md](../docs/mac/FIXED-ADDRESS.md).

The first time, macOS may ask whether `simsight-bridge` may accept incoming
connections: allow it, or the phone cannot connect.

## Layout

```
Scripts/              the mod's Python
  simsight_bridge.py  game side of the phone companion (Mac and Windows)
  ctypes/             CPython 3.7 ctypes, Windows build only
companion/
  proto/              API and pipe messages (buf, simsight.v1)
  bridge/             Go: pipe to the game, Connect API, pairing, event detection
  web/                SvelteKit phone app (fr/en/es), embedded in the bridge
  speaker/            SimSightSay.swift (macOS voices)
companion/meeting-point/  optional Cloudflare Worker (fixed app address)
mac/                  build, install, release, tests, player notes
  ui-package.json     pinned TS4 Access release for SimSight_UI.package
windows/              Windows build (on the Mac), installers, pinned downloads
                      (cloudflared, Prism speech pack)
docs/mac/             player guides (phone app, keyboard, commands, fixed address,
                      meeting point, AI helper)
docs/windows/         Windows guides (README, CONTROLS, CHEATS)
```

## Development

- Phone app without the game: `go run ./cmd/simsight-bridge --demo` in
  `companion/bridge` (fake household on port 47800, link printed), then
  `pnpm dev` in `companion/web` (proxies the API to it).
- While the game runs: `mac/dev.sh web` (phone app with live reload),
  `mac/dev.sh bridge`, `mac/dev.sh speaker` (rebuilt and swapped in; the mod
  restarts the bridge if it exits). Python changes need a game restart.
- After changing `companion/proto`: `buf generate` in `companion/bridge` and
  `pnpm generate` in `companion/web`.
- Checks: `go vet ./... && go test ./...` (bridge), `pnpm verify` (web).
- Game-side rules on macOS: the game's Python has no `ctypes`, and a
  background thread importing a not-yet-loaded module crashed the game. Run
  game work from the game's tick (see `simsight_bridge.py`), never import
  inside a thread.

## Licence and credits

MIT (see `LICENSE`). SimSight by the40n8, based on TS4 Access by **tyjokr**
(lead development **LeonianUniverse**; Plus add-on by **Michael Maslo**;
Build cursor and scanner additions by **DaRainbowGirlXX**).
Please report problems here, not to the TS4 Access project.
