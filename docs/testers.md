# Testers wanted

SimSight is in **alpha**. It is played regularly on a Mac, by a sighted
player and a blind player on an iPhone with VoiceOver. We need more people
to try it, and above all **Windows players**.

## Windows testers

The Windows version has not been played inside the game yet. If you play
The Sims 4 on Windows with NVDA or JAWS (or would like to), you can help a
lot with one evening:

1. [Install it](install.md#windows-alpha) (one PowerShell line; it backs up
   your settings and saves first, and can remove SimSight again).
2. Start your screen reader, then the game, and load a household.
3. Tell us what happens, even if it is only "nothing speaks". The first
   minutes matter most:
    - Does the game start with the mod? Does anything speak?
    - Does **Control+Alt+H** open the accessibility menu?
    - Does **phone companion** open a page and say a code?
    - Does the phone app connect, and show your household?

## Mac and phone testers

The [test checklist](mac/TESTING.md) lists what to try, screen by screen.
Lines marked **new** have only been checked outside the game so far.

## How to tell us

- **On GitHub:** [open a test report](https://github.com/the40n8/simsight-companion/issues/new?template=test-report.yml).
  The form asks for the computer, the screen reader and what happened; every
  field is optional except the description.
- **Without a GitHub account:** in the Discord where you found SimSight.

For each problem, the most useful is: where you were, what you pressed, and
what was said (word for word if you can). If the game itself showed an
error, the file `SimSight.log` in `Mods/SimSight` helps too: it lists what
the mod said and did. It contains your Sims' names; nothing else personal.

## Safety

- Your saves are never changed. The installer copies them to
  **SimSight Backups** before installing anything.
- Removing SimSight moves it out of the Mods folder; the game works as before.
- The phone link is a key: anyone with it can play on your game. Send it
  only to your own phone. **Disconnect every phone** in companion setup
  makes a new key if you shared it by mistake.
