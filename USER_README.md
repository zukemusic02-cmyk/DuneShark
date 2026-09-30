# DuneShark · Tales of the Dark Hulud

A free, single-player companion for **Dune: Awakening**: a live trainer with spawning, vehicle tuning,
base-building tools and quality-of-life options, all in one window that sits next to your game.

> **Solo worlds only.** DuneShark works in your own offline, single-player world. It does not work on official
> servers. Using any memory tool online can get your account banned.

**Game updates:** DuneShark checks the game's version every time. After an update it adapts by itself when
it safely can; if something changed that it can't adapt to, it tells you and changes nothing.

---

## Getting started (portable, no install)

0. **One time only:** in Steam, right-click **Dune: Awakening → Properties → Launch Options** and type `-nobattleye`.
   BattlEye (the online anti-cheat) isn't needed for solo play, and DuneShark can't connect while it runs.
   Remove it again before playing online.
1. **Unzip** the `DuneShark` folder somewhere normal, like your Desktop or Documents.
   Not inside *Program Files* (DuneShark saves your settings next to itself).
2. Double-click **DuneShark.exe**.
3. **"Windows protected your PC"?** That's Windows' warning for new apps that aren't widely known yet.
   Click **More info → Run anyway**. Some antivirus programs also flag game trainers; that's expected for tools
   that change game memory.
4. Start **Dune: Awakening** and load your **solo** world (DuneShark can be opened before or after the game).

To remove DuneShark, just delete the folder. Nothing is installed anywhere else.

### The status line (top of the window)

| Dot | Message | Meaning |
|---|---|---|
| 🔴 | Waiting for Dune: Awakening | The game isn't running yet |
| 🟡 | Waiting for your character | Main menu, loading screen or world map; features start when you land |
| 🟢 | Ready | Connected to your solo world, everything works |
| 🔴 | Game version not supported / online server / BattlEye running / Windows blocked access | See the message; nothing is changed |

The **Help** button opens this file.

---

## What it does

**Muad'Dib Imports (item spawner)**
Every item in the game with its real name, colors, icon, description, recipe and stats per grade.
Pick items and grades and they're delivered straight to your backpack. Save your picks as your own **kits**.

**Live · Player**
God mode · Refill health / water · Unstuck (moves you to the nearest safe spot) · Run speed and suspensor
(hover) speed · Infinite power, stamina and water · Instant ability cooldowns · Skill points,
specialization XP, XP, Solaris · Backpack size

**Live · Vehicles**
Separate speed multipliers for sandbike, buggy, sand crawler and light / assault / carrier ornithopters ·
Ornithopter flight height (the landing gear stays up up high) · Boost multiplier · Optional
"no thin-air fuel burn and overheating"

**Live · Building**
Instant build · Staking time · Up to 15 claim extensions · Higher piece and light limits · Building height
multiplier (placed claims too). Re-applied by itself every time you load your world.

**Live · Story**
Finish the current story or side-contract step, one step at a time, with the real quest names.

**Live · Fast travel**
Only to trading posts you've already visited and your own bases, with a proper loading screen.
It never sends you to map markers or random coordinates.

**Profiles**
Save your whole setup (speeds, vehicles, stats, building, god mode, cooldowns) and switch it live,
from a button or a hotkey.

**Time of day** · **Hotkeys** for everything (controller-friendly via reWASD)

---

## First run

1. Start **Dune: Awakening** and load your **solo** world.
2. Open **DuneShark** (desktop icon). Order doesn't matter: it waits for the game.
3. Once your character is in the world, the Live panels come alive.

**Can't see DuneShark?** It's probably behind the fullscreen game. Press **Ctrl+Alt+Home** to bring it to the
front, and again to hide it.

Your settings are remembered and re-applied automatically after restarts of DuneShark or the game.

---

## Hotkeys

| Keys | Action |
|---|---|
| Ctrl+Alt+Home | Show / hide DuneShark over the game |
| Ctrl+Alt+B | Instant build on/off |
| Ctrl+Alt+P | Infinite power on/off |
| Ctrl+Alt+C | Instant ability cooldowns on/off |
| Ctrl+Alt+G | God mode on/off |
| Ctrl+Alt+H / W | Refill health / water |
| Ctrl+Alt+U | Unstuck |
| Ctrl+Alt+D / N | Day (10:00) / night (22:00) |
| Ctrl+Alt+PageUp / PageDown | Suspensor speed +0.5 / −0.5 |
| Ctrl+Alt+S / X | Finish story step / side step (press twice) |
| Ctrl+Alt+1 / 2 | Build Ready Kit / Basic Survival Kit |

Change any of them in `hotkeys.json` (in the DuneShark folder). You can also bind a profile: `"Ctrl+Alt+F1": "profile:combat"`.
Controller users: see `REWASD_GUIDE.md`.

---

## If something doesn't work

1. **Is DuneShark hidden?** Ctrl+Alt+Home.
2. **Are you in your world?** On the menu, loading screens or the world map there's no character yet.
3. **Look at the log:** `duneshark_errors.log` (in the DuneShark folder).
4. **Restart DuneShark.** Everything you set comes back by itself.

The full list of known issues and fixes is in **TROUBLESHOOTING.md**.

---

## For testers

Thanks for testing! Try what you like, and if you have time, these especially:

- [ ] Opening DuneShark before and after the game, and the status line
- [ ] Muad'Dib Imports: spawn a few items and grades, save your own kit
- [ ] Player: run and suspensor speed, infinite power/stamina/water, instant cooldowns (Ctrl+Alt+C)
- [ ] Vehicles: each vehicle's speed, thopter height and boost, "no thin-air burn & heat"
- [ ] Building: instant build, staking time, limits, height
- [ ] Fast travel to a visited trading post and to your base
- [ ] Profiles: save one, switch between two
- [ ] Hotkeys, especially **Ctrl+Alt+Home** over the fullscreen game

**Reporting a problem:** tell me what you did, what you expected and what happened, and send the file
`duneshark_errors.log` from the DuneShark folder (plus a screenshot if you can).

---

## Your files

Everything DuneShark remembers lives in its folder: `settings.json` (your values), `profiles.json`,
`kits.json` (your kits), `hotkeys.json` (your keys), and the logs. Keep these files when you update to a new
DuneShark version.

---

## Safety

- DuneShark only changes your game **while it's running**. Close the game and everything is back to normal.
- Destructive commands (progress resets, unlearning) are blocked on purpose.
- Built-in guard rails: fast travel never lands you somewhere you could get stuck, and speed limits are
  capped.

---

## Credits

Made in **Ecuador** 🇪🇨, with Claude (Anthropic) as coding partner.
Vehicle and cooldown recipes adapted from the Dune: Awakening Cheat Engine table by **Prometheu5 Studios**.
Icons and names come from the game itself.

DuneShark is a fan project. It is not affiliated with or endorsed by Funcom. Dune: Awakening and its content
belong to their owners.
