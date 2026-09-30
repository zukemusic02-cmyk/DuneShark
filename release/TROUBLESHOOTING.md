# DuneShark · Troubleshooting

## First checks (in this order)

1. **Can't see DuneShark?** It's probably behind the fullscreen game. Press **Ctrl+Alt+Home**
   (again to hide it). Alt+Tab often doesn't work over fullscreen games.
2. **Look at the status line** at the top of DuneShark. It tells you what it's waiting for.
3. **Are you in your world?** On the main menu, loading screens or the world map there's no character yet,
   so player features (speeds, infinite stats, cooldowns) start when you land.
4. **Restart DuneShark.** Everything you set is remembered and applied again by itself.
5. Still stuck? Send `duneshark_errors.log` (in the DuneShark folder) with a short description.

---

| Problem | What to do |
|---|---|
| DuneShark doesn't show up / "doesn't open" | It's open behind the game: **Ctrl+Alt+Home**. |
| "Windows protected your PC" when starting it | **More info → Run anyway.** Windows shows this for new apps. |
| Antivirus deletes or blocks DuneShark.exe | Game trainers change game memory, which some antivirus programs flag. Allow the DuneShark folder in your antivirus, or don't use it if you're not comfortable. |
| Status: **Waiting for Dune: Awakening** | Start the game. |
| Status: **Waiting for your character** | Load your solo world and land (not the world map). |
| Status: **Game version not supported** | A game update changed something DuneShark can't adapt to by itself. Nothing is changed; wait for a DuneShark update. |
| Status: **You're on an online server** | DuneShark only works in your own solo world. |
| Status: **BattlEye is running** | Close the game, add `-nobattleye` to its Steam launch options (right-click Dune: Awakening → Properties → Launch Options) and start it again. Remove it to play online. |
| Status: **Windows blocked access to the game** | Run DuneShark and Dune the same way: both normally, or both as administrator. |
| A hotkey does nothing | Another program (Discord, NVIDIA/AMD overlay…) may use the same keys. `hotkeys_log.txt` lists keys that couldn't be registered; change them in `hotkeys.json`. |
| Settings not saved | Don't run DuneShark from inside a zip or from *Program Files*: unzip it to your Desktop or Documents. |
| Stuck under the map or inside a rock | Press **Unstuck** in DuneShark (or Ctrl+Alt+U): nearest safe spot, no death. |
| Items don't arrive | Make room in your backpack first: items are delivered straight into it. |
| Thopter legs drop when flying very high | Use the **Thopter height** setting in DuneShark (it keeps the gear up). |
| Boost looks too extreme (wide view, camera far away) | Known: at very high speed and boost the game's camera stretches. Use a lower speed or boost multiplier for now. A "calm camera" option is planned. |
| Something else strange | Restart DuneShark; if it happens again, send `duneshark_errors.log` and what you did. |

## Good to know

- DuneShark only changes your game **while it's running**. Close the game and everything is back to normal.
- Your settings, profiles, kits and hotkeys are the `.json` files in the DuneShark folder. Keep them when
  updating to a new version.
