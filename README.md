# DuneShark · Tales of the Dark Hulud

Source code of **DuneShark**, a free live trainer and companion for **Dune: Awakening single-player (solo) worlds**.
Download the ready-to-use version on Nexus Mods. Player guide: [USER_README.md](USER_README.md).

## What it is

- A small Python app: a local web UI (`app/index.html`) served by `app/server.py` on `127.0.0.1:8765`,
  shown in a native window by `app/duneshark.pyw` (pywebview).
- `app/dune_bridge.py` talks to the running game: it finds the game's object tables by byte pattern and runs the
  game's own built-in functions (the same method as the Prometheu5 Studios Cheat Engine table).
- It does **not** modify game files, inject DLLs or bypass anti-cheat. It refuses to work when BattlEye is
  running or when you're on an online server: solo worlds only.

## Build it yourself (Windows 10/11, 64-bit)

1. Install **Python 3.14** (python.org), tick "Add python.exe to PATH".
2. Open a terminal in this folder and install the build tools:
   ```
   pip install -r requirements.txt
   ```
3. Build:
   ```
   python build_portable.py
   ```
   Result: `release/DuneShark/DuneShark.exe` and `release/DuneShark_<version>_portable.zip`
   (PyInstaller `--onedir --noconsole`, see `build_portable.py`).

**Item icons:** the game's icons are not in this repository (they belong to the game). `build_portable.py`
copies them from `icons_export/`, a folder you create yourself by exporting the game's
`DuneSandbox/Content` textures as PNG with FModel. Without it the build still works; items just show no icon.

## Run from source (no build)

```
pip install pywebview pillow
python app/duneshark.pyw
```

## Files

| File | What it does |
|---|---|
| `app/duneshark.pyw` | Window + starts the server |
| `app/server.py` | Local HTTP API used by the UI, status line, background re-apply loops |
| `app/dune_bridge.py` | Connects to the game, reads objects, runs game functions on the game thread |
| `app/dune_world.py` | Building: instant build, staking, extensions, limits, height |
| `app/dune_vehicles.py` | Vehicle speeds, thopter height / boost / thin air |
| `app/dune_story.py` | Finish current story / contract step |
| `app/dune_travel.py` | Fast travel to visited trading posts and own bases |
| `app/dune_abilities.py` | Instant ability cooldowns |
| `app/dune_hotkeys.py` | Global hotkeys |
| `app/dune_items.py`, `dune_details.py`, `dune_stats.py`, `dune_item_stats.py` | Tools that extracted the item data (`items.json`, `item_details.json`, ...) from the running game |

## Credits

Made in Ecuador, with Claude (Anthropic) as coding partner. Vehicle and cooldown recipes adapted from the
Dune: Awakening Cheat Engine table by **Prometheu5 Studios**.

DuneShark is a fan project, not affiliated with or endorsed by Funcom.
