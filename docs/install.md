# Install

Quit The Sims 4 first. Both installers back up your game settings and saves
before changing anything, allow mods in the game settings, and can be run
again to update or remove SimSight. Your saves are never changed.

New to The Sims 4? Start the game once and quit it, so it creates its
settings, then install.

## Mac

Open Terminal (Command+Space, type Terminal, Return), paste this line and
press Return:

```sh
curl -fsSL https://github.com/the40n8/simsight-companion/releases/latest/download/install.sh | zsh
```

It says each step (VoiceOver reads them; with VoiceOver off, the Mac voice
says them) and asks you to press Return before installing.

Without Terminal: download `SimSight-Companion-mac.zip` from the
[latest release](https://github.com/the40n8/simsight-companion/releases/latest)
and double-click **Install SimSight Companion.command**. macOS may say it
cannot verify the developer: open System Settings > Privacy & Security, find
the message near the bottom, choose **Open Anyway**, then double-click it again.

## Windows (alpha)

Open PowerShell (Windows+X, then Terminal), paste this line and press Enter:

```powershell
irm https://github.com/the40n8/simsight-companion/releases/latest/download/install.ps1 | iex
```

It writes each step as a line your screen reader reads, finds your Sims 4
folder (even when Windows keeps Documents in OneDrive, or when the game
names it in your language, like "Les Sims 4"), checks the download and installs
the mod with its speech pack for NVDA and JAWS.

Without PowerShell: download `SimSight-Companion-windows.zip` from the
[latest release](https://github.com/the40n8/simsight-companion/releases/latest),
extract it and open **Install SimSight Companion.cmd**. If Windows says it
protected your PC, choose More info, then Run anyway.

Then: [getting started on Windows](windows/index.md).

## Update

In the game, open the accessibility menu, choose **companion setup**, then
**Check for updates**. With the game running, the update is downloaded and
installed when you quit the game; with the game closed, it is installed at
once. The game also tells you when a new version is out.

Or quit the game and run the install line again.

**Testing versions.** New versions can be published for testing first.
Under **Versions offered** on the same page, choose **Testing** to be
offered them as well, before everyone else. They can have problems; your
saves are backed up before each install, as always. Choose **Stable** again
to go back to the versions everyone gets: a testing version stays installed
until the next stable release.

## Remove

- **Mac:** run the install line with `-s uninstall` at the end, or
  double-click **Uninstall SimSight Companion.command** from the zip.
- **Windows:** run the install line again and type **r**.

The mod folder goes to **SimSight Backups**, next to your saves; your saves
are not touched.

## What gets installed

Everything goes in `Mods/SimSight`, in your Sims 4 user folder:

- the mod itself (`SimSight.ts4script`) and the game interface it needs
  (`SimSight_UI.package`, from TS4 Access);
- the phone companion (`simsight-bridge`), which serves the phone app;
- Cloudflare's tunnel program (`cloudflared`), which lets the phone reach
  the computer from any network;
- on the Mac, the voice helper (`simsight-say`); on Windows, the speech pack
  for screen readers (Prism).

The installer's download is checked against its SHA-256 fingerprint before
anything is installed.
