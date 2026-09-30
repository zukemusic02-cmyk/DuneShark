"""Extract base stats of weapons and armor from the running game -> item_stats.json (read-only except text lookups).

Ranged weapon: DT_ItemTableWeapons row -> WeaponStats.HandheldRef (weapon actor class) -> CDO m_DefaultModID_Frame ->
               DT_WeaponsAndMods_* row (WeaponModBase): the frame carries the stats (barrel/ammo parts are empty).
Melee weapon:  DT_MeleeWeaponItemTable MeleeWeaponName -> DT_MeleeWeaponDataTable row.
Armor:         DT_ArmorItemTable ArmorStats (+ DT_ClothingHydrationItemTable for stillsuit capture).
Grade scaling: DA_StatChangesPerQuality -> damage x CF_WeaponsDamageBoostPerQuality, shield damage x
               CF_WeaponsShieldDamageBoostPerQuality, armor value x CF_ArmorBoostPerQuality.
Stat wording + "higher is better" flags: DT_WeaponStats / DT_ModifiableItemStats (StatDisplayName, bIncreasingIsGoodForPlayer).
Verified: Tabr Softstep Boots armor 146 x 1.6857 (G5) = 246 like the in-game card.
Usage:  python dune_item_stats.py
"""
import json, os, struct, sys
import app_paths
import dune_bridge as b
import dune_items as di
import dune_details as dd

HERE = app_paths.APP_DIR
OUT = os.path.join(HERE, "item_stats.json")
MOD_TABLES = ("DT_WeaponsAndMods_LightDart", "DT_WeaponsAndMods_HeavyDart", "DT_WeaponsAndMods_Exotic",
              "DT_WeaponsAndMods_Melee", "DT_WeaponsAndMods_Tools")
SWD = 0x80                                   # WeaponModBase.StaticWeaponData
RANGED = {"Damage": 0x3C, "ShieldDamage": 0x40, "CritDamage": 0x88, "MaxRange": 0xF8, "EffectiveRange": 0x100,
          "FireRate": 0x12C, "MaxAmmo": 0x1D0, "ReloadTime": 0x1D4, "PowerConsumptionPerShot": 0x1E0}
HIP, ADS = 0xBA0, 0xC80                      # WeaponModBase.PrimaryFireModeData / ADSFireModeData (FireMode)
MELEE = {"MeleeDamage": 0xD0, "ShieldDamage": 0xD4, "AttackStaminaBaseCost": 0xF0, "BlockStaminaCost": 0xF4}


def f32(g, a):
    return round(struct.unpack("<f", g.read(a, 4))[0], 4)


def curve_points(g, curve, key_size):
    p, n = struct.unpack("<Qi", g.read(curve + 0x30 + 0x70, 12))
    pts = {}
    for i in range(n if b.valid(p) and 0 < n < 32 else 0):
        t, v = struct.unpack("<ff", g.read(p + i * key_size + 4, 8))
        pts[int(round(t))] = round(v, 5)
    return [pts.get(q, 1.0) for q in range(6)]


def main():
    g = b.Game()
    g.attach()
    c = g.ctx()
    g.require_solo(c)
    key_size = g.i32(g.find_objects({("RichCurveKey", "ScriptStruct"): None})[("RichCurveKey", "ScriptStruct")] + 0x58)
    curves = g.find_objects({("CF_WeaponsDamageBoostPerQuality", "CurveFloat"): None,
                             ("CF_WeaponsShieldDamageBoostPerQuality", "CurveFloat"): None,
                             ("CF_ArmorBoostPerQuality", "CurveFloat"): None})
    grade = {"damage": curve_points(g, curves[("CF_WeaponsDamageBoostPerQuality", "CurveFloat")], key_size),
             "shield": curve_points(g, curves[("CF_WeaponsShieldDamageBoostPerQuality", "CurveFloat")], key_size),
             "armor": curve_points(g, curves[("CF_ArmorBoostPerQuality", "CurveFloat")], key_size)}

    # ---- weapon part rows ----
    mods = {}
    for t in MOD_TABLES:
        for n, r in dd.table_rows(g, t):
            mods[n] = r

    out = {}
    # ---- ranged + tools ----
    wt = g.find_objects({("DT_ItemTableWeapons", "DataTable"): None})[("DT_ItemTableWeapons", "DataTable")]
    hr_off = g.find_prop_in(g.u64(wt + 0x28), "WeaponStats")["off"]          # HandheldRef is its first field
    frame_off = {}
    for rid, row in di.rows(g, wt):
        cls = g.u64(row + hr_off)
        if not b.valid(cls):
            continue
        if cls not in frame_off:
            p = g.find_prop_in(cls, "m_DefaultModID_Frame")
            frame_off[cls] = p["off"] if p else None
        if frame_off[cls] is None:
            continue
        frame = g.fname(g.u64(cls + b.CDO_OFF) + frame_off[cls])
        r = mods.get(frame)
        if not r:
            continue
        s = {k: f32(g, r + SWD + o) for k, o in RANGED.items()}
        if s["Damage"] <= 0 and s["MaxAmmo"] <= 0:
            continue
        s["FireRate"] = round(1 / s["FireRate"], 2) if s["FireRate"] > 0 else 0     # seconds per shot -> shots/s
        s["HipFireBaseAccuracyOffset"] = f32(g, r + HIP)
        s["ADSBaseAccuracyOffset"] = f32(g, r + ADS)
        s["VerticalRecoil"] = f32(g, r + HIP + 0x24)
        out[rid] = {"kind": "ranged", "frame": frame, "stats": s}

    # ---- melee ----
    md = dict(dd.table_rows(g, "DT_MeleeWeaponDataTable"))
    for rid, row in dd.table_rows(g, "DT_MeleeWeaponItemTable"):
        mrow = md.get(g.fname(row + 0x38))
        if mrow:
            out[rid] = {"kind": "melee", "stats": {k: f32(g, mrow + o) for k, o in MELEE.items()}}

    # ---- armor (+ hydration) ----
    at = g.find_objects({("DT_ArmorItemTable", "DataTable"): None})[("DT_ArmorItemTable", "DataTable")]
    ast = g.find_objects({("ArmorItemStats", "ScriptStruct"): None})[("ArmorItemStats", "ScriptStruct")]
    afields = []
    s = ast
    while b.valid(s):                             # walk parent structs too (ArmorValue lives in one)
        f = g.u64(s + 0x50)
        while b.valid(f):
            t = g.fname(g.u64(f + 8))
            if t in ("FloatProperty", "IntProperty"):
                afields.append((g.fname(f + 0x28), g.i32(f + 0x4C), t))
            f = g.u64(f + 0x20)
        s = g.u64(s + 0x40)
    hyd = dict(dd.table_rows(g, "DT_ClothingHydrationItemTable"))
    for rid, row in di.rows(g, at):
        s = {}
        for n, o, t in afields:
            v = f32(g, row + 0x10 + o) if t == "FloatProperty" else g.i32(row + 0x10 + o)
            if abs(v) > 1e-6:
                s[n.replace("Armor", "") if n != "ArmorValue" else n] = v
        h = hyd.get(rid)
        if h:
            cap, mit = f32(g, h + 0x10 + 4), f32(g, h + 0x10)
            if cap:
                s["DehydrationCaptureModifier"] = cap
            if mit:
                s["DehydrationMitigationModifier"] = mit
        if s:
            out[rid] = {"kind": "armor", "stats": s}

    # ---- equip effects (worm threat, dash stamina cost, ...) on wearables ----
    # WearableItemTableRow.EquipableStats.GameplayEffectOnEquip[] -> GE class CDO Modifiers[] (GameplayModifierInfo 0x338):
    # Attribute.AttributeName (FString @0), ModifierOp (@0x38: 0 add, 1 multiply, 2 divide, 3 override),
    # ModifierMagnitude.ScalableFloatMagnitude (@0x48: Value float, Curve table @+8, RowName @+0x10).
    # Curve table rows: FSimpleCurve keys (time, value) at +0x78.
    curve_cache = {}

    def table_curve(ct, row):
        key = (ct, row)
        if key not in curve_cache:
            val = None
            mp, mn = struct.unpack("<Qi", g.read(ct + 0x30, 12))
            for i in range(mn if b.valid(mp) and 0 < mn < 5000 else 0):
                if g.fname(mp + i * 0x18) == row:
                    cv = g.u64(mp + i * 0x18 + 8)
                    kp, kn = struct.unpack("<Qi", g.read(cv + 0x78, 12))
                    d = g.read(kp, kn * 8) if b.valid(kp) and 0 < kn < 64 else None
                    if d:
                        val = [struct.unpack_from("<ff", d, j * 8) for j in range(kn)]
                    break
            curve_cache[key] = val
        return curve_cache[key]

    ge_cache = {}

    def effect_mods(ge):
        if ge in ge_cache:
            return ge_cache[ge]
        mods_out = []
        mp = g.find_prop_in(ge, "Modifiers")
        cdo = g.u64(ge + b.CDO_OFF)
        if mp and b.valid(cdo):
            p, n = struct.unpack("<Qi", g.read(cdo + mp["off"], 12))
            for k in range(n if b.valid(p) and 0 < n < 16 else 0):
                e = p + k * 0x338
                sp, sn, _ = struct.unpack("<Qii", g.read(e, 16))
                attr = g.read(sp, sn * 2).decode("utf-16le", "replace").rstrip("\0") if b.valid(sp) and 0 < sn < 128 else ""
                op = (g.read(e + 0x38, 1) or b"\0")[0]
                sf = e + 0x40 + 0x8
                val = struct.unpack("<f", g.read(sf, 4))[0]
                ct, row = g.u64(sf + 8), g.fname(sf + 0x10)
                if b.valid(ct) and row and row != "None":
                    pts = table_curve(ct, row)
                    if pts:
                        val *= pts[0][1]
                if attr:
                    mods_out.append({"attr": attr, "op": op, "value": round(val, 4)})
        ge_cache[ge] = mods_out
        return mods_out

    eis = g.find_objects({("EquippableItemStats", "ScriptStruct"): None})[("EquippableItemStats", "ScriptStruct")]
    ge_off = g.find_prop_in(eis, "GameplayEffectOnEquip")["off"]
    for tbl in ("DT_Items_Wearables_Clothing", "DT_Items_Wearables_MiscEquipment"):
        try:
            rows_ = dd.table_rows(g, tbl)
        except b.BridgeError:
            continue
        for rid, row in rows_:
            gp, gn = struct.unpack("<Qi", g.read(row + 0x10 + ge_off, 12))   # EquipableStats.GameplayEffectOnEquip
            extras = []
            for k in range(gn if b.valid(gp) and 0 < gn < 8 else 0):
                ge = g.u64(gp + k * 8)
                if b.valid(ge):
                    extras += effect_mods(ge)
            if extras:
                out.setdefault(rid, {"kind": "armor", "stats": {}})["extras"] = extras
    disp_rows = dd.table_rows(g, "DT_WeaponStats") + dd.table_rows(g, "DT_ModifiableItemStats")
    names = di.texts_to_strings(g, c, [r + 0x10 for _, r in disp_rows])
    display = {}
    for (sid, r), nm in zip(disp_rows, names):
        fl = g.read(r + 0x30, 3)
        display[sid] = {"name": nm or sid, "higherGood": bool(fl[0]), "percent": bool(fl[1]), "invert": bool(fl[2])}

    json.dump({"grade": grade, "display": display, "items": out}, open(OUT, "w", encoding="utf-8"), indent=0)
    kinds = {}
    for v in out.values():
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    print(kinds, "display names", len(display), "->", OUT)


if __name__ == "__main__":
    try:
        main()
    except b.BridgeError as e:
        print("ERROR:", e); sys.exit(1)
