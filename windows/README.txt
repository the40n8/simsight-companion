SIMSIGHT

START HERE

This mod reads menus, dialogs, tooltips and notifications aloud. Its
scanner finds Sims and objects and opens their interactions without
moving the mouse. Windows only.

Three guides:
README.txt: Installation, getting started, settings and the phone companion.
CONTROLS.txt: Keyboard shortcuts, grouped by activity.
CHEATS.txt: Optional settings and help commands.

GETTING STARTED

1. Install the mod as described below (one command, the game closed).
2. Start your screen reader, then the game through Steam or the EA app,
   and load a household.
3. Press Ctrl+Alt+H, choose settings, and turn Scanner mode on.
   You can also toggle it with Ctrl+Alt+Space.
4. Wait for "Scanner", then use ordinary arrows to browse and Enter
   to open interactions. You do not hold Ctrl+Alt while browsing.

Up and Down browse the current list. Right enters a category.
Left or Backspace goes back. Space repeats speech.
For example: choose Plumbing, press Right, choose Showers, press Right,
then choose a shower and press Enter.

The scanner hides objects with no interactions for the active Sim and
updates when you switch Sims. Objects with disabled options stay listed
as unavailable; Enter opens their menu so you can hear the requirements.
Checks run in the background, so newly found objects may appear briefly
before filtering finishes. If a check fails, the object stays listed.
Unchanged objects are rechecked less often, up to once a minute. Press
Ctrl+Alt+S to request fresh checks. Switching Sims starts fresh immediately.
Checks pause while menus or text fields own keyboard input.

Menus and dialogs take over navigation while open. Escape closes them.
The scanner resumes and announces your place after they close.
For spoken help, press Ctrl+Alt+H and choose Keyboard shortcuts.
In Live mode, Scanner mode prevents arrow-key camera movement and follows
the active Sim. It does not change the Build mode camera.
With menus closed in Live mode, Shift+Tab toggles first-person view.
The scanner still works in first-person; its camera follow resumes when
you leave first-person view.

INSTALLATION

Close The Sims 4. Open PowerShell: press Windows+X, then choose Terminal
(or Windows PowerShell). Type or paste this line and press Enter:

  irm https://github.com/the40n8/simsight-companion/releases/latest/download/install.ps1 | iex

It says each step as a line of text your screen reader reads. It finds
your Sims 4 user folder (also in OneDrive Documents, and in other
languages), backs up an older SimSight, checks the download, installs
the mod with its speech pack in Mods\SimSight, and turns on both Custom
Content and Mods and Script Mods Allowed. Run the same line again to
update. To remove SimSight, run the same line: when SimSight is already
installed, it asks whether to update or remove it (type r for remove).

Without the command: download SimSight-Companion-windows.zip from the
releases page, extract it, and open "Install SimSight Companion.cmd".

First-time players must open the game once and exit so it can create its
settings (Options.ini), then install.

UI Cheats Extension is not tested with SimSight; if menus stop speaking,
try without it.

UPDATING

In the accessibility menu, choose companion setup, then Check for
updates. With the game running, the update is downloaded and installed
when you quit the game; with the game closed (SimSight Setup.cmd in
Mods\SimSight), it is installed at once. Or close the game and run the
install line again. Your settings in SimSight.cfg are kept. At game
start, your screen reader is told when a new version is out.

After every Sims 4 patch, check for a compatible SimSight release. Game
patches can turn mods off again: run the install line again with the game
closed to turn them back on.

SPEECH

With the speech pack (Prism, installed with the mod), the mod can speak through NVDA, JAWS and
other supported screen readers. Your screen reader controls its voice
and speed. Microsoft Narrator is not supported.

Without a supported screen reader, the mod uses a
Windows voice. Only that fallback voice uses the rate, volume and name
settings under [voice]. The rate defaults to 3; its range is -10 to 10.

SETTINGS

Open settings from the accessibility menu, or enter simsight.menu window in
the cheat console.

You can also edit SimSight.cfg next to the mod files. It is a
plain-text INI file despite the .cfg extension. Save changes, then restart
the game or enter simsight.reload in the cheat console.

Most options use on or off. The main choices are:
Scanner mode: Ordinary arrow-key browsing and Live camera follow.
Keyboard: Navigation inside menus and panels.
Simplified menus: Move through menu options one at a time. On by default.
CAS and Stories keep their existing controls. Turn this setting off to
restore the original menu navigation.
Travel list: Choose travel destinations from lists instead of a map.
Numpad navigation: Optional keys for menus and panels.
Build controls: Place catalog objects from the lot grid in Build mode.
Sim actions: Announce what the active Sim starts doing. On by default.
Existing settings are preserved; set "sim actions = on" to enable it if
your settings file already has it off.
Object names: Read world-object tooltips.
Captions: Show spoken text on screen.

Build controls start on. Scanner mode starts off.
Speech mode can be hover, manual, beeps or off. Hover reads automatically;
manual waits for the read shortcut; beeps uses tones; off is silent.
Tooltip delay controls the wait before longer descriptions.
Braille can be concise, full, only or off.
Verbosity can be full or brief. Full says what a thing is before its name,
such as Category, Subcategory, Dialog or Menu open. Brief says only the names.

Shortcut blocking is automatic. There is no setting to manage it.

SIMPLIFIED MENUS

Simplified menus is on by default. Up and Down or Tab move through one
option at a time, including choices arranged in several columns.
Enter chooses an available single choice. For multiple choices, Space
selects or clears a row; use Done to confirm. Escape goes back or closes.
The game still checks availability, prices and selection limits.

Load Game treats each save as one choice. Enter loads the selected save.
CAS and Stories retain their existing controls.

TRAVEL

With Travel list and Keyboard on, press M. Choose Go alone or Choose
who comes when Simplified menus is on. With it off, the game's companion
picker opens directly. Then choose a world and destination. Some buildings
offer a unit list. If nobody is eligible to come, the game skips the
companion picker. If the game is paused, resume it so the Sim can travel.
Travel from the phone goes straight to the destination lists. To choose
companions, use M and Choose who comes.
Use Back to worlds or Back to places to return. Escape cancels.
Open the map instead continues the trip using the game's map.
The same setting covers vacation travel. Rentals show the stay length
and price before confirmation. Some destinations require the native map.

PHONE ACTIONS

With Simplified menus on, open Phone from the accessibility menu.
Ctrl+Alt+P also opens it when no call or text is waiting. Choose a category,
then an action. The list uses the game's available phone actions, including
jobs, planning events and moving household. The game's requirements and
confirmation screens still apply. Escape closes the phone list.
Choose Back to the phone to return from an action list to its categories.

HOUSEHOLDS

To play another household, open Manage Worlds from the game's menu.
Tab starts map navigation. Select an occupied lot with Enter, then
continue to Play and press Enter. The household actions follow the lots.
Moving is available through Phone, Travel, Move Household. Read the
furnishing choice and remaining funds in the final move-in dialog before
confirming. Cancel Moving returns without completing the move.

BUILD MODE

With a catalog object selected, leave the catalog controls and press
Ctrl+Alt+H, then choose Guided placement. Pick a floor and a room or
lot area. The game searches that area for a valid fit and charges if
it places the object. Undo last accessible placement refunds it.
The same Guided placement option is in the Alt+B Build Helper.

With Build controls on, press F2 to enter Build mode. Choose an object in
the Build catalog, then press Ctrl+Alt+B. The plain arrow keys move one
tile at a time and read what is there, walls and doors, and whether the
object fits. R turns it. Enter places it exactly on the tile; if it does
not fit, you hear why and the nearest spot where it does, and a second
Enter places it there. Number keys 1 to 6 read details. See CONTROLS.txt.
Ctrl+Alt+Z undoes the last placement with a refund. Ctrl+Alt+X sells the
object at the cursor after a second press to confirm. It sells what is
on top first, and refuses anything that still holds or carries something.
CONTROLS.txt lists every key.

CURRENT LIMITATIONS

Many Create a Sim controls still need the mouse.
Build mode places catalog objects only. Walls, floors, roofs, rooms,
stairs, pools, terrain and paint still need the mouse.
Objects that hang on a wall or ceiling, attach to a fence, or need water
or bare ground cannot be placed from the grid yet.
The game's own Build undo does not cover objects placed from the grid;
use Ctrl+Alt+Z.
On the map, choosing a lot does not automatically move keyboard focus to
its action panel. Continue through the options to Play or Confirm.

CREDITS

The world scanner, tile cursor, object placement and several keyboard
ideas come from LeonianUniverse's a11yscan mod, included with his permission.

PHONE COMPANION

SimSight comes with a phone companion: a web app on the phone (iPhone with
VoiceOver, or Android with TalkBack) that shows needs, mood and actions,
the game's questions, a room-by-room house map and quick actions, and lets
a second player follow or play along. In the game, press Ctrl+Alt+H and
choose phone companion: a page with a QR code opens in the browser and
your screen reader says a 6-digit pairing code. Scan the code with the
phone, or open the app on the phone and type the 6 digits.

Settings for the companion (a fixed address, a meeting point, disconnect
every phone): Ctrl+Alt+H, then companion setup. With the game closed,
open "SimSight Setup.cmd" in Mods\SimSight. Phone guide:
https://github.com/the40n8/simsight-companion/blob/main/docs/mac/PHONE-GUIDE.md
