"""Fast travel that doesn't feel like cheating (user design, 2026-09-28).

Destinations are ONLY places the game itself treats as safe spawn points, and only ones you have unlocked:
  * trading posts you visited  -> save table player_respawn_locations, group CheckpointSafe (game's own unlock list),
    travelled to with RespawnCheatManager.TeleportToSpawnLocation(<locator name>) = the exact spot the game respawns you
  * your bases                 -> your sub-fief totems and respawn beacons (save table actors), travelled to with
    DuneCheatManager.TeleportToFindSpot, which lets the game pick a free, safe spot next to the base
Never map markers or free coordinates (those can leave people stuck inside the terrain).
"""
import sqlite3
import dune_bridge as b
import dune_story

BASE_CLASSES = {"BP_TotemSmall_C": "Sub-Fief Console", "BP_Totem_C": "Advanced Sub-Fief", "BP_RespawnBeacon_C": "Respawn Beacon"}
LOCATION_NAMES = {
    "HaggaBasin_Tradepost_GriffinsReach": "Griffin's Reach", "HaggaBasin_Tradepost_Anvil": "The Anvil",
    "HaggaBasin_Tradepost_Pinnacle": "Pinnacle Station", "HaggaBasin_Tradepost_Crossroads": "The Crossroads",
    "PolarCap_Tradepost_BreakersYard": "Breaker's Yard",
}


def _save():
    dune_story.save_nodes()                                   # refreshes the decompressed copy of the solo save
    import os, tempfile
    return sqlite3.connect(os.path.join(tempfile.gettempdir(), "duneshark_journey.sqlite"))


def destinations():
    con = _save()
    out = []
    for name, grp in con.execute("select locator_name, \"group\" from player_respawn_locations where locator_name is not null"):
        if grp == "CheckpointSafe" or name in LOCATION_NAMES:
            out.append({"id": "spot:" + name, "kind": "Trading post", "name": LOCATION_NAMES.get(name, dune_story.pretty(name))})
    for aid, cls, x, y, z, mp in con.execute("select id, class, location_x, location_y, location_z, map from actors"):
        short = (cls or "").rsplit(".", 1)[-1]
        if short in BASE_CLASSES and x is not None:
            out.append({"id": "base:%d" % aid, "kind": "Base", "name": "%s (%.0f, %.0f)" % (BASE_CLASSES[short], x / 100, y / 100),
                        "map": mp})
    con.close()
    seen, uniq = set(), []
    for d in out:
        if d["id"] not in seen:
            seen.add(d["id"])
            uniq.append(d)
    return uniq


def travel(g, dest_id):
    c = g.ctx()
    g.require_solo(c)
    allowed = {d["id"]: d for d in destinations()}
    if dest_id not in allowed:
        raise b.BridgeError("That destination isn't unlocked.")
    import time
    cam = g.ptr(c["pc"], "PlayerCameraManager")
    cmc = g.ptr(c["char"], "CharacterMovement")
    # 1) cover the screen: the game's own loading screen with tips (fallback: fade to black)
    screen = _show_loading_screen(g, c)
    if not screen:
        _fade(g, c, cam, 0.0, 1.0, 0.4, hold=True)
    time.sleep(0.5)
    try:
        _jump(g, dest_id)
        # 2) hold position: movement off, so nothing can fall while the area streams in
        if cmc:
            b.call_on(g, c, cmc, "DisableMovement", [])
        time.sleep(5.0)
        # 3) release, then let the game itself put us on the nearest safe, LOADED ground (Unstuck always
        #    worked when a spot was still streaming in) - all while the loading screen still hides it
        if cmc:
            b.call_on(g, c, cmc, "SetMovementMode", [3, 0])
        b.run_command(g, "Unstuck", ["true", 0])
        time.sleep(1.5)
        _safe_landing(g, watch=2.0)
    finally:
        if cmc:
            try:
                b.call_on(g, c, cmc, "SetMovementMode", [3, 0])   # never leave the player frozen
            except b.BridgeError:
                pass
        if screen:
            _hide_loading_screen(g, c, screen)
        else:
            _fade(g, c, cam, 1.0, 0.0, 1.0, hold=False)
    return allowed[dest_id]


def _show_loading_screen(g, c):
    """Create the game's W_LoadingScreenMain (tips screen) and put it on top of everything. None if it can't."""
    try:
        o = g.find_objects({("Default__WidgetBlueprintLibrary", "WidgetBlueprintLibrary"): None,
                            ("W_LoadingScreenMain_C", "WidgetBlueprintGeneratedClass"): None})
        lib = o[("Default__WidgetBlueprintLibrary", "WidgetBlueprintLibrary")]
        wcls = o[("W_LoadingScreenMain_C", "WidgetBlueprintGeneratedClass")]
        import struct
        r = b.call_on(g, c, lib, "Create", [c["pc"], wcls, c["pc"]])
        w = struct.unpack("<Q", r["ReturnValue"])[0]
        if not b.valid(w):
            return None
        b.call_on(g, c, w, "AddToViewport", [10000])
        return w
    except b.BridgeError:
        return None


def _hide_loading_screen(g, c, w):
    try:
        if g.class_name(w) == "W_LoadingScreenMain_C":
            b.call_on(g, c, w, "RemoveFromParent", [])
    except b.BridgeError:
        pass


def _fade(g, c, cam, a, b_, seconds, hold):
    if cam:
        try:
            b.call_on(g, c, cam, "StartCameraFade", [a, b_, seconds, (0.0, 0.0, 0.0, 1.0), True, hold])
        except b.BridgeError:
            pass                                             # no fade is fine; the trip itself still works


def _jump(g, dest_id):
    if dest_id.startswith("spot:"):
        b.run_command(g, "TeleportToSpawnLocation", [dest_id[5:], 0], ext="RespawnCheatManager")
    else:
        con = _save()
        row = con.execute("select location_x, location_y, location_z from actors where id = ?", (int(dest_id[5:]),)).fetchone()
        con.close()
        if not row:
            raise b.BridgeError("That base wasn't found in the save.")
        x, y, z = row
        # TeleportToFindSpot silently does nothing when the destination's terrain isn't loaded yet (far away),
        # so go to 3 m above the base itself; _safe_landing below catches a fall through not-yet-loaded ground.
        b.run_command(g, "TeleportTo", [x, y, z + 300, 0, 0, 0, 0])


def _height(g):
    import struct
    c = g.ctx()
    root = g.ptr(c["char"], "RootComponent")
    p = g.find_prop_in(g.cls(root), "RelativeLocation")
    return struct.unpack("<ddd", g.read(root + p["off"], 24))[2]


def _safe_landing(g, watch=8.0):
    """After a teleport the terrain may still be streaming in, so you can drop through it. Watch the height for a
    few seconds; if you sink more than 15 m below where you arrived, the game's own Unstuck puts you on real ground."""
    import time
    time.sleep(0.6)
    start = _height(g)
    end = time.time() + watch
    while time.time() < end:
        time.sleep(0.25)
        if _height(g) < start - 1500:
            time.sleep(1.0)                                   # give the ground a moment to finish loading
            b.run_command(g, "Unstuck", ["true", 0])
            return "rescued"
    return "ok"
