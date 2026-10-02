# DuneShark Troubleshooter

Every problem DuneShark has hit so far: **symptom → cause → fix → how to check**.
New problems get added here as they happen.

## First checks (in this order)

1. **Is DuneShark running?** Its window may be hidden behind the fullscreen game: press **Ctrl+Alt+Home**.
2. **Are you in a world?** On the main menu, a loading screen or the **world map (overmap)** there is no character,
   so player features (run speed, infinite stats, cooldowns) wait until you land. That's normal.
3. **Read the error log:** `app\duneshark_errors.log` (every failure since 2026-09-29, with details).
   Other logs: `app\hotkeys_log.txt` (shortcuts), `C:\Game Mods\Talk To Claude\errors.log` (F13/F14 voice).
4. **Restart DuneShark** (desktop icon). Everything it applies is remembered and re-applied by itself.

---

## DuneShark app and window

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| DuneShark "doesn't open", Alt+Tab doesn't show it | It's open but under the fullscreen game; Windows blocks it from coming forward | **Ctrl+Alt+Home** shows it on top, press again to hide (fixed 09-28) | Press the hotkey |
| Ctrl+Alt+Home did nothing | "Stay on top" value was passed wrongly on 64-bit Windows, so it was ignored | Fixed 09-28 (proper 64-bit handle) | Hotkey toggles show/hide |
| Window opened off-screen | Minimizing saved Windows' hidden "parked" position (-16000) | Parked positions are ignored, minimum size enforced (fixed 09-28) | `app\settings.json` x/y are normal numbers |
| "Program is not responding" after the borderless window change | The window library scanned a public attribute holding the window itself and froze | Window internals made private (fixed 09-28) | Window responds |
| Ctrl+R / F5 didn't reload | App window ignores browser shortcuts | Own reload handler (fixed 09-28) | Ctrl+R reloads |
| Console windows flashing | Game check ran `tasklist` with a visible console | Runs hidden (fixed 09-27) | No flashes |
| Python icon in taskbar | No app identity/icon | Own icon + taskbar identity (09-29) | Shark fin icon |

## Settings not saved / mods not applying

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| **Mods not applying** (building at vanilla, errors) | DuneShark opened before the game; its game connection went stale and every background task failed **silently** | **Self-healing**: any failure drops the connection and reconnects; every failure is logged (fixed 09-29) | `duneshark_errors.log`; Building panel shows your values |
| Vehicle speeds (or other settings) lost | Several parts saved `settings.json` at the same moment, one wiped the other; a half-written file read as empty | One save at a time + complete file written before replacing (fixed 09-28) | Values survive a restart |
| Building settings gone after travelling to another map | Re-apply ran once per game launch only | Re-applies per world too (fixed 09-28) | Building panel after map travel |

## Movement

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| Run speed ×3 became ×9 ("Superman") | After a DuneShark restart, the boosted speed was adopted as the new normal | Normal speeds saved per game session (`app\move_base.json`), never re-adopted boosted (fixed 09-28) | Restart DuneShark 3×: speed stays the same |
| **Suspensor runaway**: accelerating forever, ended under the map | The belt's velocity multiplier is applied **every frame**, so anything above 1 compounds | **Never touch that value.** Hover speed now scales walk speed only while in the air (fixed 09-28) | Hover at ×1.5 stays steady |
| Stuck under the map | Result of the runaway | **Unstuck** button / Ctrl+Alt+U (moves you to the nearest safe spot, no death) | You're on the ground |

## Vehicles (ornithopters)

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| Height multiplier did nothing at first | The thopter's own config is only a display copy; physics reads a hidden copy | Both copies written (fixed 09-28) | Ceiling = 900 m × multiplier |
| Legs drop / "edit-mode" pose above ~1,200 m | Ground probe can't reach the ground, reads 0 m, animation lowers the gear | Gear stays up while the probe misses, normal when it hits (fixed 09-28) | Legs stay up high |
| Overheating + fuel drain + warning at 750 m even with height ×2 | A third copy: the **engine module** has its own altitude curve (×1 at 750 m → ×250 power at 900 m) | Curve follows the multiplier; "No thin-air burn & heat" flattens it (fixed 09-28) | Temperature calm when climbing |
| Fuel warning still showing at 750 m | Hardcoded in the HUD itself | HUD value patched to follow your settings (fixed 09-28) | No warning with the toggle on |
| Boost ×1.5 felt like nothing | Thopter top speed caps (200) cut the boost off | Caps lifted **only while boosting** (fixed 09-28) | Boost is faster than cruising |
| Boost: FOV "time bend", camera very far away | Camera widens and pulls back with speed; at ×3 speeds it goes far past what the game was tuned for (measured: FOV 161° peak, camera 2.4× further) | Boost max lowered to ×3. **"Calm camera" fix still to do** | — |

## Building

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| Height ×2 didn't affect the already-placed sub-fief | Placed claims keep their own size | Placed claim boxes stretched too (fixed 09-28) | You can build higher on old claims |

## Items and spawning

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| Stack size multiplier had no effect | The game reads a **combined** item table, not the base one | Both tables patched (09-28, awaiting test) | Stacks bigger in game |
| Items not appearing (early) | Early attempts went through UE4SS / wrong route | Own bridge; spawning confirmed (09-27, "I GOT COPPER INGOT") | — |
| Grade picker showed grades that don't exist | Recipes use 9999 placeholders for missing grades | Only real grades offered (fixed 09-28) | Augments start at their real grade |
| Icons washed out / lost texture | Flat color tint | Multiply tint + per-icon brightness (fixed 09-28) | Icons show detail |
| Refill health did nothing | The game's health command is empty in this build | Health written directly (09-28) | Health bar full |

## Fast travel

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| Nothing happened | "Find spot" teleport silently fails when terrain isn't loaded | Direct teleport (fixed 09-28) | You arrive |
| Fell under the ground (Griffin's Reach) | Ground not streamed in yet on arrival | Hold 5 s → Unstuck → safe landing, behind a real loading screen (fixed 09-28) | You land on ground |
| Rule | Could strand players | **Never** teleport to map markers or coordinates, only visited posts and your own bases | — |

## Hotkeys and voice (F13/F14)

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| Phantom Space key spamming (even with keyboards unplugged) | Voice script's hold-Space loop got stuck | 2-minute safety cap (fixed 09-27) | — |
| F13/F14 dead after reboot | Script isn't in startup | Relaunch `C:\Game Mods\Talk To Claude\TalkToClaude.ahk` | Tray icon present |
| F13 overlay shows but terminal not focused | Windows focus-stealing protection | 3 fallback methods (fixed 09-28) | `errors.log` shows which worked |
| Recording stops when the game takes focus | Space is only sent while the terminal is in front (safety) | Refocus attempt was reverted 09-28; **open** | — |
| "2222" typed in the terminal | Controller back paddle mapped to "2" (not a bug) | — | — |

## Story and quests (game bugs)

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| Stuck in the conversation with the Bene Gesserit in the first Bene Gesserit mission, can't exit (2026-10-01) | Game bug (not DuneShark). Tracked contract was "The Price of Rejection" step 4/7, which was NOT the stuck one | SkipCutscene did nothing; don't use ResetDialogueState (may wipe dialogue flags). Try Esc, then Alt+F4 + restart. **Waiting for the user to confirm the fix** | Conversation closes after reload |

## Game crashes / tools

| Symptom | Cause | Fix / status | Check |
|---|---|---|---|
| UE4SS crashes the game at start | Funcom's customized engine layout breaks UE4SS's ProcessEvent hook | UE4SS **disabled** (`Win64\dwmapi.dll.off`); DuneShark uses its own bridge | Game starts |
| FModel export wrote nothing | Export folders pointed to a protected folder | Export to `icons_export` (fixed 09-28) | Files appear |
| Game crashed right as DuneShark (re)opened (2026-10-01) | Fatal 'Streamline/DLSSG present failed, DXGI_ERROR_INVALID_CALL' after 'stale fullscreen-state recovery': the new window pulled the game out of exclusive fullscreen while DLSS Frame Generation was on | Don't restart DuneShark while the game is fullscreen; use Borderless in Dune's video settings. **Open** | Crash log in `%LOCALAPPDATA%\DuneSandbox\Saved\Crashes` |
| Windows Terminal crashed twice | Suspected voice script focus/Space spam | Watch; caps added | Event Viewer |

## Rules learned (don't break these)

- **Never write the suspensor belt's velocity multiplier.** It compounds every frame.
- **Items:** always patch the **combined** item table (CDT) as well as the base tables.
- **Thopters:** there are three copies of the flight limits (display, physics, engine module) plus the HUD, so change all of them.
- **Never force-close the game.** Never mass-spawn items unattended.
- **Destructive cheats** (Reset*, Unlearn*…) are blocked by DuneShark on purpose.
- Solo only: everything DuneShark changes lives in your own game's memory; restarting the game resets it and DuneShark re-applies it.
