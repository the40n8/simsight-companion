# Optional: a meeting point for the phone app

Without anything set up, the phone link is a random Cloudflare address that
changes each game session (scan the QR code or type the pairing code each
time). A meeting point gives the phone app **one fixed address, for good**,
and the app then finds your Mac by itself every session:

- Open the app from the Home Screen, before or after starting the game: it
  says "Waiting for the game", then connects by itself.
- Several Macs with the same phone: Settings > **Computers** lists them; the
  app connects to whichever has the game running.
- You only need a **free Cloudflare account**, no domain.

The meeting point is a small Cloudflare Worker, like a URL shortener whose
target changes each session. You deploy your own, or use one someone shares.
It only keeps, for each computer: an ID, today's tunnel address, and while a
pairing code is shown, that code hashed (5 minutes). No names, no game data,
never the pairing key. After the phone has the address, it talks to the
computer through its tunnel; the meeting point is out of the way.

**Several meeting points, and when one stops:** the setup page takes a list
(one per line); the computer announces itself to each. The phone app asks
each meeting point in its own list (Settings > **Meeting points**, where you
can add another). If none answers, the app opens anyway (it keeps a copy)
and says so: open the link from the computer to play, or add another
meeting point. Nothing else breaks.

**Pairing codes have 9 digits** with meeting points (spoken in three
groups), so codes of many computers at a shared meeting point do not
clash and cannot be guessed. Guessing is also limited per network and for
everyone together.

## Set up (once)

You need Node.js on the Mac (https://nodejs.org) and the SimSight source
folder (this repository), built once with `mac/build.sh`.

1. Deploy the Worker (it opens the browser once to log in to Cloudflare):

   ```sh
   cd companion/meeting-point
   npx wrangler login
   npx wrangler deploy
   ```

   It prints the address, like `https://simsight.<your-account>.workers.dev`.
2. Open **SimSight setup** (accessibility menu > **companion setup**, or
   double-click `SimSight Setup.command` in the Mods/SimSight folder), type
   that address under **Meeting points**, then **Save**. If the game was
   running, quit and restart it.
3. Start the game and load a household. Option+Command+A, **phone
   companion**: the QR code now opens the app at the meeting point, and the
   Mac says a 9-digit code.
4. On the phone: open the meeting point address, **Share > Add to Home
   Screen**, open the app from its icon and type the code (or scan the QR
   code in Safari first, then add it).

From then on: just open the app.

**Another Mac:** type the same meeting point in that Mac's SimSight setup; on
the phone, Settings > Computers > **Pair another computer**, type the code
that Mac says.

## Good to know

- **Update the Worker when you update the mod:** the phone app lives there,
  so run `npx wrangler deploy` again after `mac/build.sh`. An old app with a
  newer mod may miss buttons.
- **Your own domain** (optional): in the Cloudflare dashboard, Workers >
  simsight > Settings > Domains & Routes > add a custom domain such as
  `sims-app.example.com`. Use a different name from a fixed tunnel address
  ([FIXED-ADDRESS.md](FIXED-ADDRESS.md)) if you have one; the two do not
  need each other.
- **Free plan, no bill:** a computer asks about 6 times an hour while the
  game runs, a phone once when it opens (more while it waits for the game).
  That is about 20 requests per player and evening; the free plan has
  100,000 a day, and the app's files do not count. On the free plan,
  Cloudflare never charges: past the limit, the meeting point stops
  answering until the next day and the apps say so (see above).
- **Sharing yours:** other players can list your address too. You then see
  their computers' IDs and tunnel addresses (not their names or games).
- **Turn it off:** empty the meeting points on the setup page. Phones then
  use the computer's own link again (pair them again once).
- **Forget a phone or Mac:** on the phone, Settings > Computers > Forget.
