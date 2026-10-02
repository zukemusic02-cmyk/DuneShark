"""Mentat Console v0.1 alpha - local item granter for Dune: Awakening solo saves.

Runs a small web server on http://127.0.0.1:8765 and edits the solo save (game.db) directly:
game.db = 4-byte version + 4-byte size + zlib-compressed SQLite database.
Every write makes a backup first and is refused while the game is running.
"""
import glob
import json
import os
import app_paths
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import time
import webbrowser
import zlib
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = app_paths.APP_DIR
PORT = 8765
SAVE_ROOT = os.path.expandvars(r"%LOCALAPPDATA%\DuneSandbox\Saved\Cloud\PlayerClientStorage")
GAME_EXE = "DuneSandbox-Win64-Shipping.exe"
STACKABLE_GROUPS = {"Resources", "Consumables"}
MAX_STACK_PER_ROW = 100
ITEM_STATS = '{"FItemStackAndDurabilityStats":[[],{}],"FCustomizationStats":[[],{}]}'
INVENTORIES = {"bank": 30, "backpack": 0}  # inventory_type values in the save


def find_save():
    saves = glob.glob(os.path.join(SAVE_ROOT, "FLS_retail", "*", "game.db"))
    return max(saves, key=os.path.getmtime) if saves else None


def game_running():
    # CSV output keeps the full image name (the table view truncates it to 25 characters)
    # CREATE_NO_WINDOW: no console flash when running from the windowed app
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {GAME_EXE}"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    return f'"{GAME_EXE.lower()}"' in out.lower()


def open_save(path):
    raw = open(path, "rb").read()
    version = struct.unpack("<I", raw[:4])[0]
    tmp = os.path.join(tempfile.gettempdir(), "mentat_console_edit.sqlite")
    with open(tmp, "wb") as f:
        f.write(zlib.decompress(raw[8:]))
    return version, tmp


def write_save(path, version, tmp):
    data = open(tmp, "rb").read()
    with open(path, "wb") as f:
        f.write(struct.pack("<II", version, len(data)) + zlib.compress(data, 6))


def player_inventory(con, kind):
    pawn = con.execute("select player_pawn_id from player_state order by id limit 1").fetchone()[0]
    row = con.execute("select id, max_item_count from inventories where actor_id=? and inventory_type=?",
                      (pawn, INVENTORIES[kind])).fetchone()
    if not row:
        raise RuntimeError(f"No {kind} inventory found for this character.")
    return row


def status():
    path = find_save()
    info = {"game_running": game_running(), "save": path, "character": None, "slots": {}}
    if not path:
        return info
    info["save_time"] = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(path)))
    _, tmp = open_save(path)
    con = sqlite3.connect(tmp)
    try:
        info["character"] = con.execute("select character_name from player_state order by id limit 1").fetchone()[0]
        for kind in INVENTORIES:
            inv, cap = player_inventory(con, kind)
            used = con.execute("select count(*) from items where inventory_id=?", (inv,)).fetchone()[0]
            info["slots"][kind] = {"used": used, "max": cap}
    finally:
        con.close()
    return info


def grant(requests, kind):
    if game_running():
        raise RuntimeError("Dune is running. Close the game first, then grant again.")
    path = find_save()
    if not path:
        raise RuntimeError("No solo save found.")
    catalogue = {i["id"]: i for i in json.load(open(os.path.join(HERE, "items.json"), encoding="utf-8"))}

    rows = []  # (template_id, stack_size)
    for r in requests:
        iid, qty = r["id"], max(1, int(r["qty"]))
        if iid not in catalogue:
            raise RuntimeError(f"Unknown item: {iid}")
        if catalogue[iid]["group"] in STACKABLE_GROUPS:
            while qty > 0:
                rows.append((iid, min(qty, MAX_STACK_PER_ROW)))
                qty -= MAX_STACK_PER_ROW
        else:
            rows += [(iid, 1)] * qty

    backup = os.path.join(SAVE_ROOT, f"_BACKUP_mentat_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(path, backup)

    version, tmp = open_save(path)
    con = sqlite3.connect(tmp)
    try:
        inv, cap = player_inventory(con, kind)
        used = {p for (p,) in con.execute("select position_index from items where inventory_id=?", (inv,))}
        free = [i for i in range(cap) if i not in used]
        if len(free) < len(rows):
            raise RuntimeError(f"Not enough space in the {kind}: need {len(rows)} slots, {len(free)} free.")
        next_id = max(con.execute("select next_id from items_id_sequencer").fetchone()[0],
                      con.execute("select seq from sqlite_sequence where name='items'").fetchone()[0] + 1)
        now = int(time.time())
        for n, ((iid, stack), pos) in enumerate(zip(rows, free)):
            con.execute("insert into items(id, inventory_id, stack_size, position_index, template_id, is_new,"
                        " acquisition_time, stats, quality_level) values (?,?,?,?,?,1,?,?,0)",
                        (next_id + n, inv, stack, pos, iid, now, ITEM_STATS))
        last = next_id + len(rows) - 1
        con.execute("update items_id_sequencer set next_id=?", (last + 1,))
        con.execute("update sqlite_sequence set seq=? where name='items'", (last,))
        con.commit()
        if con.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database check failed - nothing was written.")
    finally:
        con.close()
    write_save(path, version, tmp)
    return {"granted": len(rows), "kind": kind, "backup": backup}


# ---------------------------------------------------------------- LIVE (running game, solo)
_live_lock = threading.Lock()
_live_game = None

# ---------------------------------------------------------------- self-healing + error log
# Every failure is written to duneshark_errors.log (same error at most once a minute) and drops the cached game
# connection (at most every 15 s), so a stale connection - game started after DuneShark, game restarted,
# world reloaded - heals itself on the next try instead of failing silently forever.
_ERR_LOG = os.path.join(HERE, "duneshark_errors.log")


# entries the game lists as items but players can never own (built-in abilities)
NOT_ITEMS = {"Dash"}


_state_cache = {"state": "starting", "text": "Starting…"}


def game_state():
    """One plain-words line for the header: is DuneShark ready, and if not, why not."""
    if not game_running():
        return {"state": "nogame", "text": "Waiting for Dune: Awakening. Start the game and load your solo world."}
    if not _live_lock.acquire(timeout=1):
        return _state_cache
    try:
        g = _game()
        c = g.ctx()
        if not c.get("char") or not g.ptr(c["char"], "CharacterMovement"):
            st = {"state": "waiting", "text": "Game found. Waiting for your character (main menu, loading or world map)."}
        else:
            try:
                g.require_solo(c)
                st = {"state": "ready", "text": "Ready. Connected to your solo world."}
            except Exception as e:
                st = {"state": "blocked", "text": str(e)}
    except Exception as e:
        msg = str(e)
        if "version" in msg or "Windows blocked" in msg or "BattlEye" in msg:
            st = {"state": "blocked", "text": msg}
        else:
            st = {"state": "waiting", "text": "Game found. Waiting for your world to finish loading."}
    finally:
        _live_lock.release()
    _state_cache.update(st)
    return st


def open_doc(name):
    if name not in ("README.md", "TROUBLESHOOTING.md", "REWASD_GUIDE.md"):
        raise ValueError("unknown document")
    for p in (os.path.join(HERE, name), os.path.join(HERE, "..", name), os.path.join(HERE, "..", "release", name)):
        if os.path.isfile(p):
            os.startfile(os.path.abspath(p))
            return {"ok": True}
    raise ValueError(name + " not found")


def icon_root():
    """Item icons: the app's own 'icons' folder (portable build) or the FModel export next to the project."""
    for r in (os.path.join(HERE, "icons", "DuneSandbox", "Content"),
              os.path.join(HERE, "..", "icons_export", "DuneSandbox", "Content")):
        if os.path.isdir(r):
            return r
    return os.path.join(HERE, "icons", "DuneSandbox", "Content")
_err_seen = {}
_last_reset = [0.0]
_TRANSIENT = ("not running", "Not loaded yet", "not ready", "Load your own", "loading", "not supported",
              "Windows blocked", "online server", "BattlEye")


def log_error(where, e, reset=True):
    msg = str(e) or e.__class__.__name__
    if any(t in msg for t in _TRANSIENT):
        return                                          # normal states (game closed, loading), not errors
    now = time.time()
    key = where + "|" + msg[:160]
    if now - _err_seen.get(key, 0) > 60:
        _err_seen[key] = now
        tb = traceback.format_exc() if isinstance(e, BaseException) else ""
        try:
            with open(_ERR_LOG, "a", encoding="utf-8") as f:
                f.write("%s  [%s] %s\n%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), where, msg,
                                            tb if tb and "NoneType: None" not in tb else ""))
        except OSError:
            pass
    if reset and not any(t in msg for t in _TRANSIENT) and now - _last_reset[0] > 15:
        _last_reset[0] = now
        _reset_game()


def _reset_game():
    global _live_game
    _live_game = None
# commands that live on a cheat-manager extension class instead of DuneCheatManager itself
LIVE_EXT = {"JourneyCompleteTrackedNode": "JourneyCheatManager",   # finish the quest step you're tracking
            "SkipCutscene": "DuneStoryCheatManager"}
LIVE_BLOCKED = ("Reset", "Unlearn", "RemoveCharacterUnlockedCustomizationAll", "FactionResetFactionAlignment")


def live(cmd, args):
    """Run a DuneCheatManager command in the running solo game via dune_bridge."""
    import dune_bridge
    global _live_game
    if not cmd.replace("_", "").isalnum() or cmd.startswith(LIVE_BLOCKED):
        raise ValueError("Command not allowed: " + cmd)
    with _live_lock:
        if _live_game is None or dune_bridge.find_pid() != _live_game.pid:
            _live_game = dune_bridge.Game()
            _live_game.attach()
        if cmd in ("SetCrateLoot", "SetEnemyLoot"):   # loot roll multipliers (dune_loot.py, runtime only)
            import dune_loot
            out = dune_loot.set_multiplier(_live_game, "crate" if cmd == "SetCrateLoot" else "enemy", args[0])
            return {"ok": True, "cmd": cmd, **out}
        if cmd == "SetIntelMultiplier":         # world setting IntelPointsGainMultiplier (0..5, whole steps)
            import dune_world
            v = float(args[0])
            if v not in (0, 1, 2, 3, 4, 5):
                raise ValueError("Intel gain multiplier: 0 to 5 in steps of 1.")
            now = dune_world.set_world(_live_game, _live_game.ctx(), 26, v)
            return {"ok": True, "cmd": cmd, "value": now}
        if cmd == "FinishStep":                 # story helper: finish exactly this quest step (checked again first)
            import dune_story
            dune_story.finish(_live_game, _live_game.ctx(), str(args[0]))
            return {"ok": True, "cmd": cmd, "node": args[0]}
        refill = {"RefillHealth": ("DuneCharacterAttributeSet", "MaxHealth", "SetPlayerHealth"),
                  "RefillWater": ("DuneHydrationAttributeSet", "MaxHydration", "SetHydration")}
        if cmd in refill:                       # read the current maximum, then set to it
            aset, attr, setter = refill[cmd]
            c = _live_game.ctx()
            top = dune_bridge.player_attribute(_live_game, c, aset, attr)
            if cmd == "RefillHealth":           # SetPlayerHealth has no effect in this build -> write CurrentHealth
                dune_bridge.set_player_attribute(_live_game, c, aset, "CurrentHealth", top)
            else:
                dune_bridge.run_command(_live_game, setter, [top])
            return {"ok": True, "cmd": cmd, "value": top}
        dune_bridge.run_command(_live_game, cmd, [str(a) for a in args], ext=LIVE_EXT.get(cmd))
    return {"ok": True, "cmd": cmd, "args": args}



SETTINGS = os.path.join(HERE, "settings.json")   # UI size + window position/size, kept across restarts


# settings.json is changed by several threads (window position, speeds, building, hotkeys...). Every
# read-modify-write holds this lock, and writes go to a temp file first, so one save can never wipe another.
_settings_lock = threading.RLock()


def load_settings():
    for _ in range(3):
        try:
            return json.load(open(SETTINGS, encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            time.sleep(0.05)
    return {}


def write_settings(s):
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, SETTINGS)


def _settings_txn(fn):
    def wrapped(*a, **k):
        with _settings_lock:
            return fn(*a, **k)
    wrapped.__name__ = fn.__name__
    return wrapped


@_settings_txn
def save_settings(changes):
    s = load_settings()
    s.update({k: v for k, v in changes.items() if k in ("zoom", "x", "y", "w", "h", "maxed")})
    write_settings(s)
    return s


# ---------------------------------------------------------------- base building (dune_world.py)
BUILD_CATS = ("BuildingPiece", "Light", "Decoration", "Storage", "Production", "Doors")


def _game():
    import dune_bridge
    global _live_game
    if _live_game is None or dune_bridge.find_pid() != _live_game.pid:
        g = dune_bridge.Game()
        g.attach()
        _live_game = g
    return _live_game


def building_status():
    import dune_world
    with _live_lock:
        g = _game()
        bset = dune_world.get_building(g)
        lim = dune_world.category_limits(g)
        inst = dune_world._instant_fields(g)
        import struct as _s
        instant = all(_s.unpack("<f", g.read(a, 4))[0] <= 0.01 for _, a, _v in inst)
    vr = dune_world.vertical_ranges(g).get("Totem_Placeable") or [(0, 0)]
    return {"instant": instant, "staking_time": round(bset["staking_time"], 2), "horizontal": bset["horizontal"],
            "depth_m": -vr[-1][0], "up_m": vr[-1][1],
            "limits": {k: lim.get(k, [0])[-1] for k in BUILD_CATS}, "saved": load_settings().get("building", {})}


def building_apply(changes, remember=True):
    """changes: {instant: bool, staking_time: s, horizontal: n, mult: {cat: x}}; remembered for auto re-apply."""
    import dune_world
    with _live_lock:
        g = _game()
        g.require_solo(g.ctx())
        if "instant" in changes:
            dune_world.set_instant_build(g, bool(changes["instant"]))
        if "staking_time" in changes:
            dune_world.set_staking_time(g, changes["staking_time"])
        if "horizontal" in changes:
            dune_world.set_horizontal(g, changes["horizontal"])
        for cat, m in (changes.get("mult") or {}).items():
            if cat in BUILD_CATS:
                dune_world.set_category_multiplier(g, cat, m)
        if "height" in changes:
            dune_world.set_height_multiplier(g, changes["height"])
            dune_world.stretch_placed_claims(g, g.ctx(), changes["height"])   # already-placed sub-fiefs too
    if remember:
        with _settings_lock:
            s = load_settings()
            bld = s.get("building", {})
            for k in ("instant", "staking_time", "horizontal", "auto", "height"):
                if k in changes:
                    bld[k] = changes[k]
            if changes.get("mult"):
                bld.setdefault("mult", {}).update(changes["mult"])
            s["building"] = bld
            write_settings(s)
    return building_status()


def _building_watchdog():
    """Re-apply the remembered building settings once per game session AND per world (map travel, e.g. the Deep
    Desert, loads a new world whose placed claims / settings may be fresh)."""
    import dune_bridge
    applied_pid = None
    while True:
        time.sleep(15)
        try:
            bld = load_settings().get("building", {})
            if not bld or not bld.get("auto", True):
                continue
            pid = dune_bridge.find_pid()
            if not pid:
                continue
            with _live_lock:
                world = _game().ctx()["world"]
            if (pid, world) == applied_pid:
                continue
            building_apply({k: v for k, v in bld.items() if k != "auto"}, remember=False)
            applied_pid = (pid, world)                  # only counts once the world was loaded and it worked
        except Exception as e:
            log_error("building watchdog", e)           # menu / loading screen: try again later


threading.Thread(target=_building_watchdog, daemon=True).start()


# ---------------------------------------------------------------- keep full (infinite power / stamina / water)
# Like the CT: every 0.1 s set Current = Max. Power also feeds the shield and the suspensor belt.
KEEP = {"power": ("DuneCharacterAttributeSet", "CurrentPower", "MaxPower"),
        "stamina": ("DuneCharacterAttributeSet", "CurrentStamina", "MaxStamina"),
        "water": ("DuneHydrationAttributeSet", "CurrentHydration", "MaxHydration")}


def keep_status():
    return {k: bool(load_settings().get("keep", {}).get(k)) for k in KEEP}


@_settings_txn
def keep_set(changes):
    s = load_settings()
    keep = s.get("keep", {})
    for k, v in changes.items():
        if k in KEEP:
            keep[k] = bool(v)
    s["keep"] = keep
    write_settings(s)
    return keep_status()


def _keep_full_loop():
    import dune_bridge
    addrs, pid, backoff = {}, None, 0
    while True:
        time.sleep(0.1 if not backoff else backoff)
        try:
            want = [k for k, on in load_settings().get("keep", {}).items() if on and k in KEEP]
            if not want:
                backoff = 1.0
                continue
            with _live_lock:
                g = _game()
                if g.pid != pid:
                    addrs, pid = {}, g.pid
                c = g.ctx()
                for k in want:
                    aset, cur, mx = KEEP[k]
                    if k in addrs:                           # still the same live attribute set of THIS character?
                        obj = addrs[k][0]
                        if g.u64(obj + 0x20) != c["char"] or g.class_name(obj) != aset:
                            del addrs[k]
                    if k not in addrs:
                        a_cur = dune_bridge.attribute_address(g, c, aset, cur)
                        a_max = dune_bridge.attribute_address(g, c, aset, mx)
                        addrs[k] = (g._attrsets[aset], a_cur, a_max)
                    _, a_cur, a_max = addrs[k]
                    top = g.read(a_max, 4)
                    if top and g.read(a_cur, 4) != top:
                        g.write(a_cur, top)
            backoff = 0
        except Exception as e:
            log_error("infinite stats", e)
            addrs, backoff = {}, 2.0                         # loading screen / character changed: re-find later


threading.Thread(target=_keep_full_loop, daemon=True).start()


# ---------------------------------------------------------------- movement speed (run + suspensor belt)
# Run: CharacterMovement.MaxWalkSpeed x mult (sprint derives from it); like the CT, when the game changes the value
# itself (gear, crouch, new character) that becomes the new base. Suspensor: the equipped belt's copy on the
# movement component, m_SuspensorBeltData.VelocityMultiplier (X/Y doubles), base re-adopted the same way.
MOVE_LIMITS = {"run": (0.5, 3.0), "suspensor": (0.2, 10.0)}
# plausible NORMAL values, so an already-boosted value is never adopted as the base (that stacked x3 on x3 = x9)
MOVE_BASE_OK = {"run": (100.0, 1500.0), "suspensor": (0.1, 2.0)}
MOVE_BASE_FILE = os.path.join(HERE, "move_base.json")


def move_status():
    m = load_settings().get("move", {})
    return {k: float(m.get(k, 1.0)) for k in MOVE_LIMITS}


@_settings_txn
def move_set(changes):
    s = load_settings()
    m = s.get("move", {})
    for k, v in changes.items():
        if k in MOVE_LIMITS:
            lo, hi = MOVE_LIMITS[k]
            m[k] = round(max(lo, min(hi, float(v))), 2)
    s["move"] = m
    write_settings(s)
    return move_status()


def _movement_loop():
    import struct as _s
    state = {}                                           # key -> {"addr", "base", "set"}
    pid = None
    try:                                                 # bases survive a DuneShark restart (same game session)
        saved_bases = json.load(open(MOVE_BASE_FILE, encoding="utf-8"))
    except Exception:
        saved_bases = {}
    while True:
        time.sleep(0.1)
        try:
            want = move_status()
            if all(abs(v - 1.0) < 1e-6 for v in want.values()) and not state:
                time.sleep(0.9)
                continue
            with _live_lock:
                g = _game()
                if g.pid != pid:
                    state, pid = {}, g.pid
                    if saved_bases.get("pid") != g.pid:
                        saved_bases = {"pid": g.pid}
                c = g.ctx()
                cmc = g.ptr(c["char"], "CharacterMovement")
                if not cmc:
                    continue
                cache = g.__dict__.setdefault("_moveoff", {})
                cls = g.cls(cmc)
                if cls not in cache:
                    sb = g.find_prop_in(cls, "m_SuspensorBeltData")
                    cache[cls] = {"run": (g.find_prop_in(cls, "MaxWalkSpeed")["off"], "<f", 4, 1),
                                  "mode": g.find_prop_in(cls, "MovementMode")["off"],
                                  "velmul": sb["off"] + 0x10 if sb else None}   # belt VelocityMultiplier: kept at 1.0
                # (the belt's own VelocityMultiplier is managed by the game itself - never written by DuneShark)
                airborne = (g.read(cmc + cache[cls]["mode"], 1) or b"\0")[0] == 3      # MOVE_Falling = hovering / in the air
                speed = {"run": want["suspensor"] if airborne and abs(want["suspensor"] - 1.0) > 1e-6 else want["run"]}
                for key, mult in speed.items():
                    spec = cache[cls].get(key)
                    if not spec:
                        continue
                    off, fmt, size, count = spec
                    addr = cmc + off
                    cur = [_s.unpack(fmt, g.read(addr + i * size, size))[0] for i in range(count)]
                    st = state.get(key)
                    if not st and saved_bases.get(key):              # DuneShark (re)started: trust the saved base,
                        st = state[key] = {"addr": addr, "base": saved_bases[key], "set": cur}   # never the boosted value
                    elif not st or st["addr"] != addr or any(abs(a - b_) > 1e-3 for a, b_ in zip(cur, st["set"])):
                        lo, hi = MOVE_BASE_OK[key]
                        if all(lo <= v <= hi for v in cur):          # the game itself changed it (re-equip, gear...)
                            base = cur
                            saved_bases[key] = cur
                            json.dump(saved_bases, open(MOVE_BASE_FILE, "w", encoding="utf-8"))
                        else:                                         # boosted leftover: fall back to the known base
                            base = saved_bases.get(key) or ([1.0] * count if key == "suspensor" else None)
                            if not base:
                                continue
                        st = state[key] = {"addr": addr, "base": base, "set": cur}
                    target = [v * mult for v in st["base"]]
                    if any(abs(a - b_) > 1e-3 for a, b_ in zip(cur, target)):
                        g.write(addr, b"".join(_s.pack(fmt, v) for v in target))
                    st["set"] = target
        except Exception as e:
            log_error("movement", e)
            time.sleep(2)                                # keep the known bases (clearing them caused the stacking)


threading.Thread(target=_movement_loop, daemon=True).start()


# ---------------------------------------------------------------- ornithopters (dune_vehicles.py)
# height: flight ceiling x mult (physics copy + mirror); boost: boost-module speed x mult only. Every loaded
# ornithopter, re-applied every few seconds so newly summoned / streamed-in ones get it too. Runtime only.
VEH_LIMITS = {"height": (0.5, 3.0), "boost": (0.5, 3.0)}
SPEED_TYPES = ("bike", "buggy", "crawler", "light", "medium", "transport")
SPEED_LIMITS = (0.5, 3.0)
_veh_report = {"vehicles": []}


def veh_status():
    v = load_settings().get("vehicles", {})
    out = {k: float(v.get(k, 1.0)) for k in VEH_LIMITS}
    out["no_burn"] = bool(v.get("no_burn", False))
    sp = v.get("speed", {})
    out["speed"] = {k: float(sp.get(k, 1.0)) for k in SPEED_TYPES}
    out["report"] = _veh_report.get("vehicles", [])
    return out


@_settings_txn
def veh_set(changes):
    s = load_settings()
    v = s.get("vehicles", {})
    for k, val in changes.items():
        if k in VEH_LIMITS:
            lo, hi = VEH_LIMITS[k]
            v[k] = round(max(lo, min(hi, float(val))), 2)
        elif k == "no_burn":
            v[k] = bool(val)
        elif k == "speed" and isinstance(val, dict):
            sp = v.setdefault("speed", {})
            for t, x in val.items():
                if t in SPEED_TYPES:
                    sp[t] = round(max(SPEED_LIMITS[0], min(SPEED_LIMITS[1], float(x))), 2)
    s["vehicles"] = v
    write_settings(s)
    _veh_wake.set()
    return veh_status()


_veh_wake = threading.Event()


def _vehicle_loop():
    import dune_vehicles
    touched = False
    while True:
        _veh_wake.wait(8)
        _veh_wake.clear()
        try:
            want = veh_status()
            idle = (all(abs(want[k] - 1.0) < 1e-6 for k in VEH_LIMITS) and not want["no_burn"]
                    and all(abs(x - 1.0) < 1e-6 for x in want["speed"].values()))
            if idle and not touched:
                continue
            with _live_lock:
                g = _game()
                c = g.ctx()
            _veh_report["vehicles"] = dune_vehicles.apply(g, c, height=want["height"], boost=want["boost"],
                                                          no_burn=want["no_burn"], speeds=want["speed"])
            vehs = dune_vehicles.ornithopters(g)
            _gear["g"], _gear["t"] = g, (dune_vehicles.gear_targets(g, vehs) if want["height"] > 1 else [])
            _gear["b"], _gear["m"] = dune_vehicles.boost_targets(g, c, vehs, want["speed"]), want["boost"]
            touched = not idle
        except Exception as e:
            log_error("vehicles", e)
            time.sleep(5)


threading.Thread(target=_vehicle_loop, daemon=True).start()


# ---------------------------------------------------------------- instant ability cooldowns (dune_abilities.py)
def cooldowns_status():
    return {"on": bool(load_settings().get("cooldowns", False))}


@_settings_txn
def cooldowns_set(on):
    s = load_settings()
    s["cooldowns"] = bool(on)
    write_settings(s)
    _cd_wake.set()
    return cooldowns_status()


_cd_wake = threading.Event()


def _cooldown_loop():
    """Every 2 s while on (new gear / abilities get it too); restores the originals once when turned off."""
    import dune_abilities
    active = False
    while True:
        _cd_wake.wait(2)
        _cd_wake.clear()
        try:
            on = cooldowns_status()["on"]
            if not on and not active:
                continue
            with _live_lock:
                g = _game()
                c = g.ctx()
                if on:
                    dune_abilities.apply(g, c)
                    active = True
                else:
                    dune_abilities.restore(g)
                    active = False
        except Exception as e:
            log_error("cooldowns", e)
            time.sleep(3)


threading.Thread(target=_cooldown_loop, daemon=True).start()
_gear = {"g": None, "t": [], "b": [], "m": 1.0}


def _gear_loop():
    """4x a second: keep the thopter's landing gear up while its ground probe is out of range (high flight)."""
    import dune_vehicles
    while True:
        time.sleep(0.1)
        try:
            if _gear["t"]:
                dune_vehicles.gear_tick(_gear["g"], _gear["t"])
            if _gear["b"]:
                dune_vehicles.boost_tick(_gear["g"], _gear["b"], _gear["m"])
        except Exception as e:
            log_error("gear/boost", e, reset=False)
            _gear["t"], _gear["b"] = [], []              # rebuilt by the vehicle loop with a fresh connection
            time.sleep(2)


threading.Thread(target=_gear_loop, daemon=True).start()


# ---------------------------------------------------------------- item kits (kits.json)
def kits_list():
    k = json.load(open(os.path.join(HERE, "kits.json"), encoding="utf-8")).get("kits", {})
    return [{"key": key, "name": v.get("name", key), "count": len(v.get("items", [])), "custom": bool(v.get("custom"))}
            for key, v in k.items()]


def kit_save(name, items):
    """Save the Selected list as a custom kit (marked custom so it can be deleted from the UI)."""
    import re as _re
    name = str(name).strip()[:40]
    if not name or not items:
        raise ValueError("Give the kit a name and select some items first.")
    p = os.path.join(HERE, "kits.json")
    data = json.load(open(p, encoding="utf-8"))
    key = "my_" + (_re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "kit")
    data.setdefault("kits", {})[key] = {"name": name, "custom": True,
                                        "items": [{"id": str(i["id"]), "qty": max(1, min(int(i.get("qty", 1)), 10000)),
                                                   "grade": max(0, min(int(i.get("grade", 0)), 5))} for i in items]}
    json.dump(data, open(p, "w", encoding="utf-8"), indent=2)
    return kits_list()


def kit_delete(key):
    p = os.path.join(HERE, "kits.json")
    data = json.load(open(p, encoding="utf-8"))
    if not data.get("kits", {}).get(key, {}).get("custom"):
        raise ValueError("Only your own kits can be deleted here.")
    del data["kits"][key]
    json.dump(data, open(p, "w", encoding="utf-8"), indent=2)
    return kits_list()


def kit_spawn(key):
    kits = json.load(open(os.path.join(HERE, "kits.json"), encoding="utf-8")).get("kits", {})
    kit = kits.get(key)
    if not kit:
        raise ValueError("No kit named " + key)
    want = int(kit.get("backpack_min", 0))
    if want:                                            # only ever raises the backpack size
        slots = (status().get("slots") or {}).get("backpack") or {}
        if slots.get("max", 0) < want:
            live("SetBackpackSize", [want])
    res = live_grant(kit.get("items", []))
    res["kit"] = kit.get("name", key)
    return res


# ---------------------------------------------------------------- profiles (presets of the whole setup)
PROFILES = os.path.join(HERE, "profiles.json")
PROFILE_SECTIONS = ("move", "keep", "vehicles", "building", "god", "cooldowns")


def _profiles():
    try:
        return json.load(open(PROFILES, encoding="utf-8")).get("profiles", {})
    except Exception:
        return {}


def profiles_list():
    return [{"key": k, "name": v.get("name", k)} for k, v in _profiles().items()]


def profile_save(name):
    import re as _re
    name = str(name).strip()[:40]
    if not name:
        raise ValueError("Give the profile a name first.")
    key = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "profile"
    cur = load_settings()
    data = {"profiles": _profiles()}
    data["profiles"][key] = {"name": name, "settings": {k: cur[k] for k in PROFILE_SECTIONS if k in cur}}
    json.dump(data, open(PROFILES, "w", encoding="utf-8"), indent=2)
    return profiles_list()


def profile_delete(key):
    data = {"profiles": _profiles()}
    data["profiles"].pop(key, None)
    json.dump(data, open(PROFILES, "w", encoding="utf-8"), indent=2)
    return profiles_list()


def profile_load(key):
    """Switch the whole setup live: speeds, vehicles and infinite stats are picked up by their loops within
    seconds; building and god mode are pushed to the game right away (when it is running)."""
    prof = _profiles().get(key)
    if not prof:
        raise ValueError("No profile named " + key)
    with _settings_lock:
        s = load_settings()
        for k, v in prof.get("settings", {}).items():
            if k in PROFILE_SECTIONS:
                s[k] = v
        write_settings(s)
    _veh_wake.set()
    notes = []
    bld = prof["settings"].get("building")
    if bld and game_running():
        try:
            building_apply({k: v for k, v in bld.items() if k != "auto"}, remember=False)
        except Exception as e:
            notes.append("building: %s" % e)
    if "god" in prof["settings"] and game_running():
        try:
            live("SetGodMode", [1 if prof["settings"]["god"] else 0])
        except Exception as e:
            notes.append("god mode: %s" % e)
    return {"profile": prof.get("name", key), "notes": notes}


def story_status():
    """Current story / side quest step the game would accept right now (see dune_story.py)."""
    import dune_bridge, dune_story
    global _live_game
    with _live_lock:
        if _live_game is None or dune_bridge.find_pid() != _live_game.pid:
            _live_game = dune_bridge.Game()
            _live_game.attach()
        c = _live_game.ctx()
        return {k: dune_story.current_step(_live_game, c, k) for k in ("story", "side")}


def live_grant(items):
    """Spawn items straight into the running game's backpack (AddItemToInventory, like the CT)."""
    done, failed = 0, []
    for it in items:
        qty = max(1, min(int(it.get("qty", 1)), 10000))
        grade = max(0, min(int(it.get("grade", 0)), 5))       # InQuality = item grade 0-5
        try:
            live("AddItemToInventory", [it["id"], qty, 1, grade])
            done += qty
        except Exception as e:
            failed.append("%s: %s" % (it["id"], e))
    return {"granted": done, "kind": "backpack (live)", "failed": failed, "live": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        if code >= 400 and code != 404 and isinstance(body, dict) and body.get("error"):
            log_error("http " + self.path, body["error"])
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        elif self.path == "/favicon.ico":
            self.send(200, open(os.path.join(HERE, "duneshark.ico"), "rb").read(), "image/x-icon")
        elif self.path == "/items.json":
            # mark which items have an exported icon file (re-checked on every load, so new FModel exports just appear)
            items = [i for i in json.load(open(os.path.join(HERE, "items.json"), encoding="utf-8"))
                     if i.get("id") not in NOT_ITEMS]
            root = icon_root()
            try:
                lum = json.load(open(os.path.join(HERE, "icon_lum.json")))     # from icon_lum.py
            except Exception:
                lum = {}
            for i in items:
                ic = i.get("icon", "")
                i["iconOk"] = ic.startswith("/Game/") and os.path.isfile(os.path.join(root, ic[6:] + ".png"))
                # brightness boost after tinting: grey icons need more, white (augment) icons none
                i["iconB"] = round(min(3.0, max(1.0, 0.95 / lum[ic])), 2) if lum.get(ic) else 1.6
            self.send(200, items)
        elif self.path == "/api/settings":
            self.send(200, load_settings())
        elif self.path == "/api/keep":
            self.send(200, keep_status())
        elif self.path == "/api/move":
            self.send(200, move_status())
        elif self.path == "/api/vehicles":
            self.send(200, veh_status())
        elif self.path == "/api/profiles":
            self.send(200, profiles_list())
        elif self.path == "/api/state":
            self.send(200, game_state())
        elif self.path.startswith("/api/doc/"):
            try:
                self.send(200, open_doc(self.path[len("/api/doc/"):]))
            except Exception as e:
                self.send(404, {"missing": str(e)})
        elif self.path == "/api/cooldowns":
            self.send(200, cooldowns_status())
        elif self.path == "/api/kits":
            try:
                self.send(200, kits_list())
            except Exception as e:
                self.send(400, {"error": str(e)})
        elif self.path == "/api/travel":
            try:
                import dune_travel
                self.send(200, dune_travel.destinations())
            except Exception as e:
                self.send(400, {"error": str(e)})
        elif self.path == "/api/building":
            try:
                self.send(200, building_status())
            except Exception as e:
                self.send(400, {"error": str(e)})
        elif self.path == "/api/story":
            try:
                self.send(200, story_status())
            except Exception as e:
                self.send(400, {"error": str(e)})
        elif self.path in ("/item_details.json", "/augment_stats.json", "/item_stats.json"):
            self.send(200, open(os.path.join(HERE, self.path.lstrip("/")), "rb").read())
        elif self.path == "/game_colors.json":
            self.send(200, open(os.path.join(HERE, "game_colors.json"), "rb").read())
        elif self.path.startswith("/icon/"):
            # /icon/Game/Dune/GUI/.../T_icon_x  ->  icons_export/DuneSandbox/Content/Dune/GUI/.../T_icon_x.png (FModel export)
            rel = self.path[len("/icon/Game/"):].split("?")[0]
            root = os.path.realpath(icon_root())
            f = os.path.realpath(os.path.join(root, rel + ".png"))
            if f.startswith(root + os.sep) and os.path.isfile(f):
                self.send(200, open(f, "rb").read(), "image/png")
            else:
                self.send(404, {"error": "no icon"})
        elif self.path == "/api/status":
            try:
                self.send(200, status())
            except Exception as e:
                self.send(500, {"error": str(e)})
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/cooldowns":
            try:
                return self.send(200, cooldowns_set(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["on"]))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/profiles":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body.get("save"):
                    return self.send(200, profile_save(body["save"]))
                if body.get("delete"):
                    return self.send(200, profile_delete(body["delete"]))
                return self.send(200, profile_load(body["key"]))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/kits":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body.get("save"):
                    return self.send(200, kit_save(body["save"], body.get("items", [])))
                if body.get("delete"):
                    return self.send(200, kit_delete(body["delete"]))
                return self.send(200, kit_spawn(body["key"]))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/travel":
            try:
                import dune_travel
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with _live_lock:
                    d = dune_travel.travel(_game(), body["id"])
                return self.send(200, d)
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/vehicles":
            try:
                return self.send(200, veh_set(json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/move":
            try:
                return self.send(200, move_set(json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/keep":
            try:
                return self.send(200, keep_set(json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/building":
            try:
                return self.send(200, building_apply(json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/settings":
            try:
                return self.send(200, save_settings(json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path == "/api/live":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                return self.send(200, live(body["cmd"], body.get("args", [])))
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if self.path != "/api/grant":
            return self.send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if game_running():
                self.send(200, live_grant(body["items"]))
            else:
                self.send(200, grant(body["items"], body.get("to", "bank")))
        except Exception as e:
            self.send(400, {"error": str(e)})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Mentat Console running at http://127.0.0.1:{PORT}  (close this window to stop)")
    webbrowser.open(f"http://127.0.0.1:{PORT}")
    server.serve_forever()
