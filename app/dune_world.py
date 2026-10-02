"""Building limits + world settings for the running solo world (same method as the Prometheu5 Studios CT).

Building settings (Default__BuildingSettings, runtime only - back to normal after a game restart):
  m_StakingUnitExtensionDefaultTimeInSingleplayer  float  staking-unit countdown (s)
  m_MaxNumLandclaimSegments + m_MaxLandclaimSegmentsPerMap  horizontal staking expansions
World settings (EServerCustomSetting, kept in the save): the live value in ServerCustomSettingsSubsystem's
hash set (node: key byte @0, value @4, next @8, stride 16) + the copy on Default__UserServerCustomSettings.
"""
import json, os, struct
import app_paths
import dune_bridge as b

WORLD = {  # key -> (property on UserServerCustomSettings, kind)   (CT table, read from the enum)
    2: ("GatheringAmount", "f"), 3: ("CraftingCost", "f"), 5: ("CraftingTimeMultiplier", "f"),
    7: ("BuildingCostMultiplier", "f"), 9: ("FuelBurnTimeMultiplier", "f"), 10: ("InventoryVolumeMultiplier", "f"),
    19: ("GlobalXpMultiplier", "f"), 26: ("IntelPointsGainMultiplier", "f"), 20: ("CombatXp", "f"), 21: ("GatheringXp", "f"), 22: ("MissionXp", "f"),
    25: ("PlayerStaminaDrain", "f"), 29: ("HeatBuildupRate", "f"), 31: ("ThirstMultiplier", "f"),
    44: ("BuildingPieceLimitMultiplier", "f"), 45: ("bBuildingInfiniteStability", "b"),
}
BASE_PIECE_LIMIT = 5000


def building_settings(g):
    return g.find_objects({("Default__BuildingSettings", "BuildingSettings"): None})[("Default__BuildingSettings", "BuildingSettings")]


def _prop(g, obj, name, typ):
    p = g.find_prop_in(g.cls(obj), name)
    if not p or p["type"] != typ:
        raise b.BridgeError("Building setting %s changed in this game build." % name)
    return p


def get_building(g):
    o = building_settings(g)
    t = _prop(g, o, "m_StakingUnitExtensionDefaultTimeInSingleplayer", "FloatProperty")
    h = _prop(g, o, "m_MaxNumLandclaimSegments", "IntProperty")
    m = _prop(g, o, "m_MaxLandclaimSegmentsPerMap", "MapProperty")
    data, n = struct.unpack("<Qi", g.read(o + m["off"], 12))
    per_map = [g.i32(data + i * 20 + 8) for i in range(n if b.valid(data) and 0 < n <= 128 else 0)]
    return {"staking_time": struct.unpack("<f", g.read(o + t["off"], 4))[0],
            "horizontal": g.i32(o + h["off"]), "horizontal_per_map": per_map}


def set_staking_time(g, seconds):
    seconds = float(seconds)
    if not 0.5 <= seconds <= 86400:
        raise b.BridgeError("Staking time must be between 0.5 and 86400 seconds.")
    o = building_settings(g)
    t = _prop(g, o, "m_StakingUnitExtensionDefaultTimeInSingleplayer", "FloatProperty")
    g.write(o + t["off"], struct.pack("<f", seconds))


def set_horizontal(g, count):
    count = int(count)
    if not 1 <= count <= 100:
        raise b.BridgeError("Horizontal expansions must be between 1 and 100.")
    o = building_settings(g)
    h = _prop(g, o, "m_MaxNumLandclaimSegments", "IntProperty")
    m = _prop(g, o, "m_MaxLandclaimSegmentsPerMap", "MapProperty")
    g.write(o + h["off"], struct.pack("<i", count))
    data, n = struct.unpack("<Qi", g.read(o + m["off"], 12))
    for i in range(n if b.valid(data) and 0 < n <= 128 else 0):   # per-map overrides (TPair<FName,int32> + hash)
        if 1 <= g.i32(data + i * 20 + 8) <= 10000:
            g.write(data + i * 20 + 8, struct.pack("<i", count))


# ---------------------------------------------------------------- one-click (instant) building
# Same as the CT's "One-Click Building": BuildingSettings m_DefaultBuildAndFillTimeInSeconds -> 0.001,
# m_BuildAndFillStartThresholdTimerInSeconds -> 0, m_BuildableBuildAndFillHoldTimes map (enum key @0, float @4, stride 16)
# -> 0.001 each. Originals kept per game process so "off" puts them back.
def _instant_fields(g):
    o = building_settings(g)
    out = []
    for name, value in (("m_DefaultBuildAndFillTimeInSeconds", 0.001), ("m_BuildAndFillStartThresholdTimerInSeconds", 0.0)):
        p = _prop(g, o, name, "FloatProperty")
        out.append((name, o + p["off"], value))
    m = _prop(g, o, "m_BuildableBuildAndFillHoldTimes", "MapProperty")
    data, n, cap = struct.unpack("<Qii", g.read(o + m["off"], 16))
    if not (0 <= n <= cap <= 64 and (n == 0 or b.valid(data))):
        raise b.BridgeError("The building hold times are not ready.")
    for i in range(n):
        out.append(("hold[%d]" % (g.read(data + i * 16, 1) or b"\0")[0], data + i * 16 + 4, 0.001))
    return out


def set_instant_build(g, on):
    path = os.path.join(app_paths.APP_DIR, "instant_build_original.json")
    fields = _instant_fields(g)
    try:
        saved = json.load(open(path, encoding="utf-8"))
    except Exception:
        saved = {}
    if saved.get("pid") != g.pid:                         # first time in this game session: remember originals
        saved = {"pid": g.pid, "values": {n: struct.unpack("<f", g.read(a, 4))[0] for n, a, _ in fields}}
        if any(not 0 <= v <= 3600 for v in saved["values"].values()):
            raise b.BridgeError("Unexpected building delay found - nothing changed.")
        json.dump(saved, open(path, "w", encoding="utf-8"), indent=1)
    for n, a, fast in fields:
        g.write(a, struct.pack("<f", fast if on else saved["values"].get(n, fast)))
    return {n: round(struct.unpack("<f", g.read(a, 4))[0], 4) for n, a, _ in fields}


# ---------------------------------------------------------------- building height (up and down)
# DT_DuneTotemData rows (Totem_Placeable = advanced sub-fief, Totem_Small_Placeable = console):
# m_VerticalRangeLevels @0x18 = tiers of {byte, float min(cm) @4, byte, float max(cm) @12}, 16 bytes each.
# Stretching the existing tiers in place (no new tiers, no allocation) = safer than the CT's 31-tier variant.
# Plus BuildingSettings.m_BuildingHeightLimitInM (980 m). Originals kept per game process; runtime only.
def _vertical_rows(g):
    import dune_details as dd
    out = {}
    for n, r in dd.table_rows(g, "DT_DuneTotemData"):
        p, num, cap = struct.unpack("<Qii", g.read(r + 0x18, 16))
        if b.valid(p) and 0 < num <= cap <= 64:
            out[n] = [(p + i * 16 + 4, p + i * 16 + 12) for i in range(num)]
    return out


def vertical_ranges(g):
    return {n: [(round(struct.unpack("<f", g.read(lo, 4))[0] / 100, 1), round(struct.unpack("<f", g.read(hi, 4))[0] / 100, 1))
                for lo, hi in tiers] for n, tiers in _vertical_rows(g).items()}


def set_height_multiplier(g, mult):
    """Stretch every sub-fief vertical tier (down and up) and the general building height cap by `mult`."""
    mult = float(mult)
    if not 1 <= mult <= 5:
        raise b.BridgeError("Height multiplier must be between 1 and 5.")
    path = os.path.join(app_paths.APP_DIR, "height_original.json")
    rows = _vertical_rows(g)
    o = building_settings(g)
    hp = _prop(g, o, "m_BuildingHeightLimitInM", "FloatProperty")
    try:
        saved = json.load(open(path, encoding="utf-8"))
    except Exception:
        saved = {}
    if saved.get("pid") != g.pid:
        saved = {"pid": g.pid, "cap": struct.unpack("<f", g.read(o + hp["off"], 4))[0],
                 "tiers": {n: [[struct.unpack("<f", g.read(lo, 4))[0], struct.unpack("<f", g.read(hi, 4))[0]] for lo, hi in t]
                           for n, t in rows.items()}}
        if not (100 <= saved["cap"] <= 20000) or any(not (-1e6 < a < 0 < c < 1e6) for t in saved["tiers"].values() for a, c in t):
            raise b.BridgeError("Unexpected height values - nothing changed.")
        json.dump(saved, open(path, "w", encoding="utf-8"), indent=1)
    for n, tiers in rows.items():
        for (lo, hi), (a, c) in zip(tiers, saved["tiers"].get(n, [])):
            g.write(lo, struct.pack("<f", a * mult))
            g.write(hi, struct.pack("<f", c * mult))
    g.write(o + hp["off"], struct.pack("<f", saved["cap"] * mult))
    return {"cap_m": round(saved["cap"] * mult), "tiers_m": vertical_ranges(g)}


def stretch_placed_claims(g, c, mult):
    """Already-placed sub-fiefs keep the 3-D box they got when staked (LandclaimBaseComponent.m_LandclaimedVolume,
    a BoxComponent; e.g. half-height 115.2 m = the old -100 / +131 m). Grow each box's height by `mult` through the
    game's own BoxComponent.SetBoxExtent so collision/overlap checks update. Originals per game process."""
    mult = float(mult)
    path = os.path.join(app_paths.APP_DIR, "claims_original.json")
    try:
        saved = json.load(open(path, encoding="utf-8"))
    except Exception:
        saved = {}
    if saved.get("pid") != g.pid:
        saved = {"pid": g.pid, "boxes": {}}
    chunks, count = g.u64(g.GObjects), g.i32(g.GObjects + 0x14)
    comps = []
    for ci in range((count + 65535) // 65536):
        chunk = g.u64(chunks + ci * 8)
        n = min(65536, count - ci * 65536)
        raw = g.read(chunk, n * 24) if b.valid(chunk) else None
        for i in range(n if raw else 0):
            o = struct.unpack_from("<Q", raw, i * 24)[0]
            if b.valid(o) and "LandclaimComponent" in (g.class_name(o) or "") and not (g.obj_name(o) or "").startswith("Default__"):
                comps.append(o)
    done = 0
    for comp in comps:
        p = g.find_prop_in(g.cls(comp), "m_LandclaimedVolume")
        box = g.u64(comp + p["off"]) if p else 0
        if not b.valid(box) or g.class_name(box) != "BoxComponent":
            continue
        ep = g.find_prop_in(g.cls(box), "BoxExtent")
        x, y, z = struct.unpack("<ddd", g.read(box + ep["off"], 24))
        key = str(box)
        if key not in saved["boxes"]:
            if not (100 < z < 1e6):
                continue
            saved["boxes"][key] = [x, y, z]
        ox, oy, oz = saved["boxes"][key]
        b.call_on(g, c, box, "SetBoxExtent", [(ox, oy, oz * mult), True])
        done += 1
    json.dump(saved, open(path, "w", encoding="utf-8"), indent=1)
    return done


# ---------------------------------------------------------------- stack size multiplier (materials only)
# BaseItemTableRow.StackAndDurability.MaxStackSize for every row of DT_BaseItems_Resources (+ solid fuels) that already
# stacks (> 1). Weapons, armor, tools stay 1. Originals per game process in stack_original.json; runtime only.
STACK_TABLES = ("Resources", "SolidFuels")


def set_stack_multiplier(g, mult):
    import dune_items as di
    mult = float(mult)
    if not 1 <= mult <= 20:
        raise b.BridgeError("Stack multiplier must be between 1 and 20.")
    path = os.path.join(app_paths.APP_DIR, "stack_original.json")
    try:
        saved = json.load(open(path, encoding="utf-8"))
    except Exception:
        saved = {}
    tables = di.item_tables(g)
    rows = []
    for cat in STACK_TABLES:
        dt = tables.get(cat)
        if not dt:
            continue
        rs = g.u64(dt + 0x28)
        sad = g.find_prop_in(rs, "StackAndDurability")
        f = g.u64(rs + 0x50)
        while b.valid(f) and g.fname(f + 0x28) != "StackAndDurability":
            f = g.u64(f + 0x20)
        mss = g.find_prop_in(g.u64(f + 0x78), "MaxStackSize")
        for rid, row in di.rows(g, dt):
            rows.append((rid, row + sad["off"] + mss["off"]))
    # the game reads CDT_BaseItems (a composite table with its OWN copy of every row), so change it there too
    ids = {rid for rid, _ in rows}
    cdt = g.find_objects({("CDT_BaseItems", "CompositeDataTable"): None})[("CDT_BaseItems", "CompositeDataTable")]
    crs = g.u64(cdt + 0x28)
    csad = g.find_prop_in(crs, "StackAndDurability")
    f = g.u64(crs + 0x50)
    while b.valid(f) and g.fname(f + 0x28) != "StackAndDurability":
        f = g.u64(f + 0x20)
    cmss = g.find_prop_in(g.u64(f + 0x78), "MaxStackSize")
    for rid, row in di.rows(g, cdt):
        if rid in ids:
            rows.append((rid, row + csad["off"] + cmss["off"]))
    if saved.get("pid") != g.pid:
        saved = {"pid": g.pid, "stacks": {rid: g.i32(a) for rid, a in rows}}
        json.dump(saved, open(path, "w", encoding="utf-8"), indent=0)
    changed = 0
    for rid, a in rows:
        base = saved["stacks"].get(rid, 0)
        if base > 1:                                        # only things that already stack
            g.write(a, struct.pack("<i", int(base * mult)))
            changed += 1
    return changed


# ---------------------------------------------------------------- per-category building limits
# DT_BuildableStructureCategoryData rows (BuildingPiece, Light, Decoration, Storage, Production, Doors, Projections, ...):
# m_BuildableStructureLimitsOnServer @0x78 = [{m_TargetNumberOfLandclaims, m_MaximumNumberOfBuildables}] - the limit grows
# with the number of stake extensions but the table stops at 6 (BuildingPiece 2500..5000, Light 30..60).
# Runtime only. Originals are remembered per game process so repeated multiplies never stack.
import json, os
_ORIG = os.path.join(app_paths.APP_DIR, "building_limits_original.json")


def _category_rows(g):
    import dune_details as dd
    return dict(dd.table_rows(g, "DT_BuildableStructureCategoryData"))


def _limit_entries(g, row):
    p, n = struct.unpack("<Qi", g.read(row + 0x78, 12))
    return [(p + k * 8 + 4, g.i32(p + k * 8 + 4)) for k in range(n if b.valid(p) and 0 < n < 32 else 0)]


def category_limits(g):
    return {cat: [v for _, v in _limit_entries(g, r)] for cat, r in _category_rows(g).items()}


def set_category_multiplier(g, category, mult):
    mult = float(mult)
    if not 0.5 <= mult <= 20:
        raise b.BridgeError("Multiplier must be between 0.5 and 20.")
    rows = _category_rows(g)
    if category not in rows:
        raise b.BridgeError("Unknown building category " + category)
    try:
        orig = json.load(open(_ORIG, encoding="utf-8"))
    except Exception:
        orig = {}
    if orig.get("pid") != g.pid:                           # new game session: current values are the originals
        orig = {"pid": g.pid, "limits": category_limits(g)}
        json.dump(orig, open(_ORIG, "w", encoding="utf-8"), indent=1)
    base = orig["limits"][category]
    for (addr, _), v in zip(_limit_entries(g, rows[category]), base):
        g.write(addr, struct.pack("<i", max(1, int(round(v * mult)))))
    return category_limits(g)[category]


# ---------------------------------------------------------------- world settings
def _settings_objects(g, c):
    cache = g.__dict__.setdefault("_world", {})
    sub = cache.get("sub")
    if not (sub and g.class_name(sub) == "ServerCustomSettingsSubsystem" and g.u64(sub + 0x20) == g.ptr(c["world"], "OwningGameInstance")):
        gi = g.ptr(c["world"], "OwningGameInstance")
        sub = b.find_owned(g, gi, ("ServerCustomSettingsSubsystem",))
        cache["sub"] = sub
    if not cache.get("defaults"):
        cache["defaults"] = g.find_objects({("Default__UserServerCustomSettings", "UserServerCustomSettings"): None})[
            ("Default__UserServerCustomSettings", "UserServerCustomSettings")]
    return sub, cache["defaults"]


def _node(g, sub, key):
    data, n, cap = struct.unpack("<Qii", g.read(sub + 0x38, 16))
    size = g.i32(sub + 0x80)
    if not (b.valid(data) and 0 < n <= cap <= 128 and 1 <= size <= 256 and size & (size - 1) == 0):
        raise b.BridgeError("The world settings are not ready.")
    buckets = g.u64(sub + 0x78)
    if not b.valid(buckets):
        buckets = sub + 0x70
    idx, seen = g.i32(buckets + (key & (size - 1)) * 4), set()
    while idx != -1:
        if not 0 <= idx < n or idx in seen:
            raise b.BridgeError("The world settings list changed.")
        seen.add(idx)
        node = data + idx * 16
        if (g.read(node, 1) or b"\xff")[0] == key:
            return node
        idx = g.i32(node + 8)
    raise b.BridgeError("This world has no setting %d." % key)


def get_world(g, c, key):
    name, kind = WORLD[key]
    sub, defaults = _settings_objects(g, c)
    node = _node(g, sub, key)
    return struct.unpack("<f", g.read(node + 4, 4))[0] if kind == "f" else g.i32(node + 4)


def set_world(g, c, key, value):
    """Write the live value and the copy that is saved with the world."""
    name, kind = WORLD[key]
    sub, defaults = _settings_objects(g, c)
    node = _node(g, sub, key)
    p = g.find_prop_in(g.cls(defaults), name)
    if not p:
        raise b.BridgeError("This game build has no %s setting." % name)
    if kind == "f":
        v = float(value)
        if not 0 <= v <= 1000:
            raise b.BridgeError("%s accepts 0 to 1000." % name)
        g.write(node + 4, struct.pack("<f", v))
        g.write(defaults + p["off"], struct.pack("<f", v))
    else:
        v = 1 if int(value) else 0
        g.write(node + 4, struct.pack("<i", v))
        g.write(defaults + p["off"], bytes([v]))
    return get_world(g, c, key)
