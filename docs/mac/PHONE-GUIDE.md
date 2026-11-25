# SimSight Companion: player guide (phone companion)

This guide explains, for players and for AI assistants helping them, how to
play The Sims 4 on a Mac with SimSight Companion (the accessibility mod,
based on TS4 Access) and its phone companion. It is written for blind and low-vision players using VoiceOver on
an iPhone, often playing together with a sighted player on the Mac.

## What it is

- **SimSight** is an accessibility mod for The Sims 4, based on TS4 Access
  by tyjokr and LeonianUniverse. On the Mac, it reads what the mouse points
  at aloud and announces what the Sims do.
- It also has a **phone companion**: a web app on the phone that shows the
  game and controls it.
  It is fully usable with VoiceOver.
- Nothing is sent to a server of ours. The Mac runs a small local server; the
  phone reaches it through a temporary Cloudflare address (random, changes
  each time the game starts), or directly on the same Wi-Fi.

## Connecting the phone

1. Start the game on the Mac. When the Mac says "Phone link ready. Press
   Option Command A for the accessibility menu, then choose phone companion",
   it is ready.
2. Press Option+Command+A: the SimSight menu opens with **phone companion**
   first; choose it (or click the accessibility icon in the macOS menu bar,
   then **Phone companion…**). A page with a QR code opens, and the Mac
   voice says a **pairing code** of 6 digits, 9 with a meeting point (said twice; it is also on the page).
3. Scan the QR code with the phone camera and open the link in Safari.
   **Or, without the camera:** open the app (or the address) on the phone;
   on the **Pair this phone** screen, type the code in **Pairing code
   (6 digits)**, then **Pair with the code**. A code works once, for 5
   minutes, and after 5 wrong tries it stops: choose phone companion again
   for a new one.
4. Optional: in Safari, **Share > Add to Home Screen**: the app opens full
   screen from its icon, already paired.
5. The address changes each game session: scan again next time. The phone
   stays paired (it remembers its key). For an address that never changes
   (a Home Screen app that always works), see
   [FIXED-ADDRESS.md](FIXED-ADDRESS.md), or
   [MEETING-POINT.md](MEETING-POINT.md) (no domain needed, several Macs).
6. Settings on the Mac side are on the **SimSight setup** page:
   accessibility menu > **companion setup** (or double-click
   `SimSight Setup.command` in the Mods/SimSight folder, game closed or
   not). It chooses the phone address (changing, fixed, or home Wi-Fi
   only), the meeting point, and **Disconnect every phone** if a phone is
   lost. Changes apply when the game starts. Under **SimSight version** it
   checks for updates: with the game running, the update is downloaded and
   installed when you quit the game; with the game closed, it is installed
   at once. At game start the Mac also says when a new version is out.
7. With the game closed, the app still opens from the Home Screen and says
   the computer cannot be reached; it connects by itself once the game runs.

## Using the app with VoiceOver

- Swipe right from the top of the screen: first the **Pause / Resume** button
  (with the game speed, day and time), then the **tabs**, then the screen.
- Use the VoiceOver rotor set to **Headings** to jump between sections.
- Tabs: Live, Play, House, Sims, Settings, Help (two rows of three).
  History opens from Live with "Full history" (and has "Back to Live").
- When the game asks a question, a card **"The game asks: …"** appears under
  the screen title on every tab, and VoiceOver announces it.

## The screens

### Top bar
- One button pauses the game, or resumes at the previous speed.
- The four speeds (Pause, Play, Fast, Ultra fast) are in Play.

### Live
- **Needs attention**: what needs doing now, most urgent first:
  - low needs (hunger, bladder, energy, hygiene, fun, social);
  - fire, broken objects, puddles, spoiled food;
  - food ready to eat (cooked meals, deliveries): **Eat**, or **Put away**
    (the Sim takes it to the fridge);
  - visitors on the lot: **Greet**, **Welcome**;
  - bills due: **Pay the bills** (at the mailbox).
- Each line has buttons that run the game's own actions:
  - Hunger: **Eat now** (quick meal from the fridge), **Cook…** (opens the
    recipe question), **Other ways** (fridge, stove, microwave, grill, and the
    phone to order food).
  - Bladder: Use the toilet. Energy: Sleep, Nap. Hygiene: Shower, Bath.
    Fun: Have fun (TV, computer...). Social: Call someone.
  - Broken object: Repair, or Hire a repair person (phone).
    Puddle: Mop it up, or Hire a maid. Spoiled food: Throw it away.
    Fire: Put out the fire, or Call the firefighters.
  - For a Sim that is not the controlled one, the button is "Control <name>"
    first: actions always run for the controlled Sim.
- Then: the **Mac voice** switch, the household (funds, mood), and the latest
  events. "Full history" opens History.

### Play
From top to bottom:
- **Controlled Sim**: one button per Sim on the lot; the pressed one is the
  Sim you play. Actions always run for this Sim.
- **<Name>'s actions**: the action running now and the ones waiting in the
  queue, each with a **Cancel** button (as in the game, only for the
  controlled Sim). The Sims tab shows every Sim's queue too.
- **<Name>'s inventory** (in You): what the Sim carries ("Food: Mac and
  Cheese ×2"), each with "Put in Fridge, 3 m" buttons, one per place on the
  lot that accepts it (several fridges: each one). One tap moves the whole
  stack, like dragging it in the game.
- **You**: the Sim itself (self-care, its own interactions) and the Sim's
  **phone** (calls, invitations, going out, services, travel).
- **Interact**:
  - **Search** by type or name, in English, French or Spanish ("fridge",
    "frigo", "nevera", "shower", "douche", "ducha"...). Several words narrow
    the search. The keyboard's Search key closes the keyboard and goes to the
    results; the number found is announced.
  - **Sims nearby**, closest first.
  - **Objects by category** (Appliances, Plumbing, Comfort, Surfaces,
    Electronics, Activities, Kids, Storage... Decorations, Lighting and Build
    last). Categories are folded: each is a button "Appliances, 3" that
    opens it. A search opens every category with results.
  - Choose a Sim or object: its menu is the game's own pie menu, with
    categories. Unavailable rows say why ("Lobster, unavailable: requires
    Cooking level 9"). Choosing a row adds the action to the Sim's queue.
- **Camera**: moves the game camera on the Mac to the controlled Sim:
  **Follow <name>**, **Close-up on <name>**, and **Free camera** to let go.
  Useful to hear the Sims talk.
- **Game speed**: Pause, Play, Fast, Ultra fast.

### House
- The home floor by floor (ground floor, floor 1, basement).
- Each room is named from its furniture (Kitchen, Bathroom, Bedroom, Living
  room, Dining room, Study, Nursery, Room), with its part of the floor
  (north-west part, middle...), size in metres, and distance and direction
  from the controlled Sim. North is the game's north.
- A room shows: doors and openings to other rooms and outside (each opens that
  room), who is inside, and what is in it with where (north side, middle...).
  Decorations and lights are grouped separately. Objects open their menu.
- The garden ("Outside") and pools are listed like rooms.
- Each floor says where its stairs are ("Stairs up to Floor 1: east part, in
  the Living room"); rooms with stairs list "Stairs up to …" / "Stairs down
  to …" in their ways in and out.
- An open-plan space is named by both parts ("Kitchen and Living room") and
  says where each part is ("Kitchen area: north side").
- Live and Sims say which room each Sim is in.

### Sims
- Each household Sim: job and work performance (towards the next promotion),
  mood, needs (lowest first), moodlets with time left, the action queue
  (actions can be cancelled), skills (level of 10), relationships (friendship
  and romance from -100 to 100, with labels like "Good Friends", "Spouse").
- Sims at work or school say where they are and until when.
- **Goals**: the aspiration and its current milestone with each step (done
  or to do), wants and fears, today's work tasks, and what the next
  promotion needs.

### History
- Every event, newest first.

### Settings
- App language (French, English, Spanish). Names from the game stay in the
  game's language.
- What VoiceOver announces automatically: game messages and questions, mood
  changes, new moodlets, low needs, actions starting, speed and money.
- **Pause while I choose an action** (off by default): the game pauses when a
  menu opens and resumes once the action is queued or the menu closes.
- Mac voice: hover reading on/off, Sim action announcements on/off.
- **Conversations**: off, only new relationship levels, what they do and how
  it goes (default), or everything.
- **The Mac voice announces**: the same choices, for the Mac's own voice
  (kept on the Mac, for playing without the phone). By default moods, low
  needs and new relationship levels; the mod already reads game windows and
  notifications itself.
- Unpair this phone.

### Help
- This guide, in the app's language.

## On the Mac

- **Option+Command+S**: speech on/off (also while the game has focus).
- **Option+Command+A**: the SimSight menu.
- Accessibility icon in the macOS menu bar: the same, plus Phone companion….
- Cheat console (Cmd+Shift+C): `simsight.hub` (menu), `simsight.companion` (QR code).
- The original mod's keyboard works too, with Mac keys (Control+Shift for its
  Ctrl+Alt keys, Option+Shift for its Alt keys): see [KEYBOARD.md](KEYBOARD.md).

## Troubleshooting

- "Bella can't do “Take Shower”: no way to get there, check doors and
  stairs": the Sim could not reach the place (the game shows a "blocked"
  bubble). Add a door or stairs, or move what is in the way. Said on the
  Mac and on the phone.
- "Waiting for the game": the game is loading or closed.
- The page does not open: the game was restarted, so the address changed.
  Scan the new QR code.
- An action is missing from a menu: the game does not offer it right now
  (wrong place, Sim busy, needs a skill). Try Refresh in Play.
- Quick action says "Nothing nearby can do it": open Other ways.
- Direct Wi-Fi test (no internet needed): on the Mac, run
  `mac/wifi-test.sh`, scan the QR code; it says when the phone connects.

## For AI assistants

- Answer in the player's language. The player may be blind: give
  step-by-step instructions in terms of VoiceOver swipes, headings and button
  names as written above, not colours or positions on screen.
- Game object and action names come from the game (usually English).
- Source code: https://github.com/the40n8/simsight-companion (phone app in
  `companion/web`, game side in `Scripts/simsight_bridge.py`).
