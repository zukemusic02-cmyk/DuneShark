"""Ornithopter tuning: flight-height ceiling multiplier and boost-only multiplier.

The ornithopter actor's m_OrnithopterConfig is only a MIRROR; the physics reads a component in the game's entity
system ("FGL"). Same method as the Prometheu5 Studios CT: find the FGL registry by byte patterns, look up the
vehicle's components by its entity handle, and identify the config component because it holds the same two
pointers as the mirror (m_LocomotionModifierDataAsset + m_YawTurnRateCurve). The component uses the struct layout.

  height : m_MaxAltitudeRange (FloatRange: lower = engines start fading, upper = hard ceiling, cm) x mult
           (+ the broken-engine range), written to the FGL component and the mirror.
  boost  : actor m_CachedBoostConfig m_VultureModeBoostSpeed / m_DragonflyModeBoostSpeed x mult (boost only).
Originals are kept per game process in vehicles_original.json; runtime only.
"""
import json, os, re, struct
import app_paths
import dune_bridge as b

HERE = app_paths.APP_DIR
ORIG = os.path.join(HERE, "vehicles_original.json")
# Thin-air zone (engines fade, extra fuel burn, warning) = between the two altitude bounds. With a raised ceiling it
# gets narrower in proportion: width = stock width x mult^-log2(1.5) -> x1 150 m, x2 100 m, x3 ~79 m.
# no_burn = zone width 0: cruise right up to the ceiling with normal fuel use.
ZONE_EXP = -0.5849625007211562


def zone_lower(lo, hi, mult, no_burn=False):
    new_hi = hi * mult
    if no_burn:
        return new_hi
    if mult > 1 and lo > 0:
        return new_hi - (hi - lo) * mult ** ZONE_EXP
    return lo * mult

# Landing-gear fix: the chassis animation lowers the gear when its ground probe reads < 3.5 m, and above ~1200 m
# the probe misses (reads 0), so the legs dropped in mid-air. While the probe misses, the threshold is set to -1;
# as soon as it hits ground again (far above landing height) the stock value comes back.
GEAR_OFF = {"driver": 1088, "dist": 164, "trace": 184, "boosting": 1}

# Boost caps: the physics clamps speed to m_MaxSpeed / m_VultureModeMaxSpeed (config) and, in hover, to
# m_DragonFlyBoostModifier.MaxSpeedFactor. The hover factor only acts while boosting, so it is scaled statically;
# the two max speeds are raised ONLY while the thopter is boosting (anim driver m_bBoosting), stock otherwise.
BOOST_CAPS = ("m_MaxSpeed", "m_VultureModeMaxSpeed")

# Per-vehicle speed multipliers (recipe from the Prometheu5 CT, verified for 1.5.3.4):
#  thopter: flight config m_MaxSpeed + m_VultureModeMaxSpeed (mirror + physics), the wing module asset's
#           m_VultureModeMaxSpeed (glide max, shared per wing type), both boost speeds, actor m_TerminalVelocitySpeed.
#           The boost multiplier stacks on top (caps x speed, x boost as well only while boosting).
#  ground : engine config m_MaxSpeed + m_MaxEnginePower x s, backward speed/power x min(s, 1.5),
#           m_DecelerationScale x max(1, s) (mirror + physics), actor m_TerminalVelocitySpeed x s.
SPEED_KEYS = ("bike", "buggy", "crawler", "light", "medium", "transport")


def category(g, veh):
    name = (g.class_name(veh) or "").lower()
    if "ornithopter" in name:
        if "transport" in name or "carrier" in name or "heavy" in name:
            return "transport"
        if "medium" in name or "assault" in name:
            return "medium"
        return "light"
    s = g.cls(veh)
    for _ in range(12):
        n = g.obj_name(s) or ""
        if n == "DuneSandbikeVehicle":
            return "bike"
        if n in ("DuneHarvesterVehicle", "DuneTreadWheelVehicle") or "crawler" in name or "harvester" in name:
            return "crawler"
        if n == "DuneWheeledVehicle":
            return "buggy"
        s = g.u64(s + 0x40)
        if not b.valid(s):
            break
    return None


def _struct_of(g, cls_, prop):
    s = cls_
    while b.valid(s):
        f = g.u64(s + 0x50)
        while b.valid(f):
            if g.fname(f + 0x28) == prop:
                return g.i32(f + 0x4C), g.u64(f + 0x78)
            f = g.u64(f + 0x20)
        s = g.u64(s + 0x40)
    return None, None


def _f(g, a):
    return struct.unpack("<f", g.read(a, 4))[0]

SIGS = {
    "reg": "48 83 EC 28 48 8B D1 41 B8 02 00 00 00 48 8B 0D ?? ?? ?? ?? E8 ?? ?? ?? ?? 48 8B 80 ?? ?? ?? ?? 48 05 ?? ?? ?? ?? 48 83 C4 28 C3",
    "storage": "48 8B 81 ?? ?? ?? ?? 48 63 D2 48 8B 04 D0 48 83 C0 ?? C3",
    "lookup": "48 81 C1 ?? ?? ?? ?? E8 ?? ?? ?? ?? 48 63 44 24 ?? 83 F8 FF 74 ?? 4C 8B C8 48 8D 3C 45 01 00 00 00 48 8B 86 ?? ?? ?? ??",
    "fuel": ("48 8B 86 ?? ?? ?? ?? 48 89 84 24 ?? ?? ?? ?? 8B 04 3B 39 05 ?? ?? ?? ?? 0F 8F ?? ?? ?? ?? 8B 15 ?? ?? ?? ?? "
             "48 8B 4C 24 ?? E8 ?? ?? ?? ?? 41 B0 01 48 8D 94 24 ?? ?? ?? ?? 48 8B 08 4C 8B 89 ?? ?? ?? ?? 48 8B C8 41 FF D1 "
             "C5 FA 10 8E ?? ?? ?? ?? C5 F8 57 C0 C5 F8 2F C8 72 ?? C5 F2 5D 05 ?? ?? ?? ?? C5 FA 59 40 ?? C5 FA 11 40 ??"),
}


def _find_all(g, pat):
    if not hasattr(g, "_code"):
        g.scan([], lambda t: False)                           # fills g._code with the executable sections
    rx = re.compile(b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in pat.split()), re.S)
    return [va + m.start() for va, data in g._code for m in rx.finditer(data)]


def fgl(g):
    if getattr(g, "_fgl", None):
        return g._fgl
    hits = {k: _find_all(g, v) for k, v in SIGS.items()}
    if not all(hits.values()) or len(hits["reg"]) != 1 or len(hits["storage"]) != 1 or len(hits["fuel"]) != 1:
        raise b.BridgeError("Vehicle system not found in this game build.")
    a, s_, f = hits["reg"][0], hits["storage"][0], hits["fuel"][0]
    elems = {g.i32(h + 36) for h in hits["lookup"]}
    if len(elems) != 1:
        raise b.BridgeError("Vehicle system layout is ambiguous.")
    g._fgl = {"worldOff": g.i32(a + 28), "regOff": g.i32(a + 34), "tableOff": g.i32(s_ + 3),
              "storageAdd": (g.read(s_ + 17, 1) or b"\0")[0], "elemsOff": elems.pop(), "handleOff": g.i32(f + 3)}
    return g._fgl


def fgl_table(g, world):
    F = fgl(g)
    x = g.u64(world + F["worldOff"])
    tbl = g.u64(x + F["regOff"] + F["tableOff"]) if b.valid(x) else 0
    return tbl if b.valid(tbl) else None


def fgl_comp(g, tbl, type_id, handle, max_n=20000):
    F = fgl(g)
    s = g.u64(tbl + type_id * 8)
    if not b.valid(s):
        return None
    s += F["storageAdd"]
    elems, n = g.u64(s + F["elemsOff"]), g.i32(s + F["elemsOff"] + 8)
    if not b.valid(elems) or not 0 < n <= max_n:
        return None
    raw = g.read(elems, n * 0x18) or b""
    for i in range(n):
        h, comp = struct.unpack_from("<QQ", raw, i * 0x18)
        if h == handle:
            return comp if b.valid(comp) else None
    return None


def ornithopters(g):
    """All ornithopter actors currently loaded (live ones only, not class defaults)."""
    return instances_of(g, "DuneOrnithopter")


def instances_of(g, base_name):
    """Live (non-default) objects whose class derives from the native class base_name."""
    base = g.find_objects({(base_name, "Class"): None})[(base_name, "Class")]
    chunks, count = g.u64(g.GObjects), g.i32(g.GObjects + 0x14)
    out, is_orni = [], g.__dict__.setdefault("_isa_" + base_name, {})
    for ci in range((count + 65535) // 65536):
        chunk = g.u64(chunks + ci * 8)
        n = min(65536, count - ci * 65536)
        raw = g.read(chunk, n * 24) if b.valid(chunk) else None
        for i in range(n if raw else 0):
            o = struct.unpack_from("<Q", raw, i * 24)[0]
            if not b.valid(o):
                continue
            cl = g.u64(o + 0x10)
            if cl not in is_orni:
                s, hit = cl, False
                for _ in range(20):
                    if s == base or not b.valid(s):
                        hit = s == base
                        break
                    s = g.u64(s + 0x40)
                is_orni[cl] = hit
            if is_orni[cl] and not (g.obj_name(o) or "").startswith("Default__"):
                out.append(o)
    return out


def _config_struct(g):
    orni = g.find_objects({("DuneOrnithopter", "Class"): None})[("DuneOrnithopter", "Class")]
    f = g.u64(orni + 0x50)
    s = orni
    while b.valid(s):
        f = g.u64(s + 0x50)
        while b.valid(f):
            if g.fname(f + 0x28) == "m_OrnithopterConfig":
                return g.i32(f + 0x4C), g.u64(f + 0x78)
            f = g.u64(f + 0x20)
        s = g.u64(s + 0x40)
    raise b.BridgeError("Ornithopter config not found.")


def config_component(g, world, veh):
    """The FGL copy of this ornithopter's flight config (the one the physics reads)."""
    cfg_off, st = _config_struct(g)
    oa = g.find_prop_in(st, "m_LocomotionModifierDataAsset")["off"]
    oc = g.find_prop_in(st, "m_YawTurnRateCurve")["off"]
    pa, pc = g.u64(veh + cfg_off + oa), g.u64(veh + cfg_off + oc)
    if not (b.valid(pa) and b.valid(pc)):
        return None
    tbl = fgl_table(g, world)
    handle = g.u64(veh + fgl(g)["handleOff"])
    cache = g.__dict__.setdefault("_cfgtype", {})
    types = [cache["t"]] if "t" in cache else range(0, 1024)
    for t in types:
        comp = fgl_comp(g, tbl, t, handle, 512)
        if comp and g.u64(comp + oa) == pa and g.u64(comp + oc) == pc:
            cache["t"] = t
            return comp
    return None


def _load_orig(g):
    try:
        o = json.load(open(ORIG, encoding="utf-8"))
    except Exception:
        o = {}
    o = o if o.get("pid") == g.pid else {"pid": g.pid}
    for k in ("height", "boost", "caps"):
        o.setdefault(k, {})
    return o


def apply(g, c, height=None, boost=None, no_burn=False, speeds=None):
    """Apply multipliers to every loaded ornithopter. Returns a small report."""
    orig = _load_orig(g)
    cfg_off, st = _config_struct(g)
    ranges = [g.find_prop_in(st, n)["off"] for n in ("m_MaxAltitudeRange", "m_BrokenEngineMaxAltitudeRange")]
    report = []
    for veh in ornithopters(g):
        key = str(g.u64(veh + fgl(g)["handleOff"]))
        name = g.obj_name(veh)
        if height is not None:
            comp = config_component(g, c["world"], veh)
            targets = [veh + cfg_off] + ([comp] if comp else [])
            saved = orig["height"].setdefault(key, [[struct.unpack("<f", g.read(veh + cfg_off + r + o, 4))[0] for o in (4, 12)]
                                                    for r in ranges])
            for base_addr in targets:
                for r, (lo, hi) in zip(ranges, saved):
                    if hi > 0:
                        new_hi = hi * float(height)
                        new_lo = zone_lower(lo, hi, float(height), no_burn and lo > 0)
                        g.write(base_addr + r + 4, struct.pack("<f", new_lo))
                        g.write(base_addr + r + 12, struct.pack("<f", new_hi))
            report.append({"vehicle": name, "ceiling_m": round(saved[0][1] * float(height) / 100),
                           "thin_air_m": round(zone_lower(*saved[0], float(height), no_burn) / 100), "physics": bool(comp)})
        if boost is not None:
            sp = float((speeds or {}).get(category(g, veh), 1.0))
            bo, bs = _struct_of(g, g.cls(veh), "m_CachedBoostConfig")
            if bs:
                offs = [g.find_prop_in(bs, n)["off"] for n in ("m_VultureModeBoostSpeed", "m_DragonflyModeBoostSpeed")]
                saved = orig["boost"].setdefault(key, [_f(g, veh + bo + o) for o in offs])
                for o, v in zip(offs, saved):
                    if v > 0:
                        g.write(veh + bo + o, struct.pack("<f", v * float(boost) * sp))
                report.append({"vehicle": name, "boost": [round(v * float(boost) * sp) for v in saved], "speed": sp})
            # hover boost cap factor (boost-only by nature) + stock top speeds for the live speed/boost loop
            comp = config_component(g, c["world"], veh)
            fo = g.find_prop_in(st, "m_DragonFlyBoostModifier")["off"]
            cap_offs = [g.find_prop_in(st, n)["off"] for n in BOOST_CAPS]
            saved = orig["caps"].setdefault(key, [_f(g, veh + cfg_off + o) for o in [fo] + cap_offs])
            for base_addr in [veh + cfg_off] + ([comp] if comp else []):
                g.write(base_addr + fo, struct.pack("<f", saved[0] * float(boost)))
            extra = orig.setdefault("orni_extra", {})
            if key not in extra:
                tv = g.find_prop_in(g.cls(veh), "m_TerminalVelocitySpeed")
                e = extra[key] = {}
                if tv:
                    e["terminal"] = [tv["off"], _f(g, veh + tv["off"])]
                qa = g.find_prop_in(st, "m_LocomotionModifierDataAsset")
                asset = g.u64(veh + cfg_off + qa["off"]) if qa else 0
                ga = g.find_prop_in(g.cls(asset), "m_VultureModeMaxSpeed") if b.valid(asset) else None
                if ga:
                    wing = orig.setdefault("wing", {})
                    wing.setdefault(str(asset), _f(g, asset + ga["off"]))
                    e["wing"] = [asset, ga["off"]]
    if height is not None:
        report += apply_engines(g, c, float(height), no_burn, orig)
    report += apply_ground(g, c, speeds or {}, orig)
    json.dump(orig, open(ORIG, "w", encoding="utf-8"), indent=1)
    return report


def gear_targets(g, vehicles):
    """(anim instance, its class, threshold offset) for each ornithopter, for the fast gear loop."""
    out = []
    for veh in vehicles:
        m = g.u64(veh + g.find_prop_in(g.cls(veh), "m_Mesh")["off"])
        ai = g.ptr(m, "AnimScriptInstance") if b.valid(m) else None
        p = g.find_prop_in(g.cls(ai), "DistanceToGroundToLowerGear") if ai else None
        if p:
            out.append((ai, g.cls(ai), p["off"], struct.unpack("<d", g.read(ai + p["off"], 8))[0]))
    return out


def gear_tick(g, targets):
    stock = g.__dict__.setdefault("_gear_stock", {})          # per game process, never carried over
    for ai, cl, off, first in targets:
        if g.u64(ai + 0x10) != cl:
            continue
        cur = struct.unpack("<d", g.read(ai + off, 8))[0]
        if cur >= 0:
            stock[ai] = cur
        e = ai + GEAR_OFF["driver"]
        dist = struct.unpack("<f", g.read(e + GEAR_OFF["dist"], 4))[0]
        missed = dist == 0.0 and struct.unpack("<f", g.read(e + GEAR_OFF["trace"] + 4, 4))[0] == 1.0   # Time 1 = no hit
        want = -1.0 if missed else stock.get(ai, first if first >= 0 else 350.0)
        if abs(cur - want) > 1e-6:
            g.write(ai + off, struct.pack("<d", want))


def boost_targets(g, c, vehicles, speeds=None):
    """Per thopter: (anim instance, class, [(addr, stock)], speed mult) - every top-speed limiter, for the
    live loop that holds them at stock x speed, x boost as well while boosting."""
    cfg_off, st = _config_struct(g)
    cap_offs = [g.find_prop_in(st, n)["off"] for n in BOOST_CAPS]
    orig = _load_orig(g)
    out = []
    for veh in vehicles:
        key = str(g.u64(veh + fgl(g)["handleOff"]))
        m = g.u64(veh + g.find_prop_in(g.cls(veh), "m_Mesh")["off"])
        ai = g.ptr(m, "AnimScriptInstance") if b.valid(m) else None
        comp = config_component(g, c["world"], veh)
        stock = orig["caps"].get(key)
        if not (ai and comp and stock):
            continue
        slots = [(a + o, v) for a in (veh + cfg_off, comp) for o, v in zip(cap_offs, stock[1:])]
        e = orig.get("orni_extra", {}).get(key, {})
        if "terminal" in e:
            slots.append((veh + e["terminal"][0], e["terminal"][1]))
        if "wing" in e and str(e["wing"][0]) in orig.get("wing", {}):
            slots.append((e["wing"][0] + e["wing"][1], orig["wing"][str(e["wing"][0])]))
        out.append((ai, g.cls(ai), slots, float((speeds or {}).get(category(g, veh), 1.0))))
    return out


def boost_tick(g, targets, mult):
    for ai, cl, slots, sp in targets:
        if g.u64(ai + 0x10) != cl:
            continue
        boosting = (g.read(ai + GEAR_OFF["driver"] + GEAR_OFF["boosting"], 1) or bytes(1))[0] & 1
        k = sp * (float(mult) if boosting else 1.0)
        for a, v in slots:
            want = struct.pack("<f", v * k)
            if g.read(a, 4) != want:
                g.write(a, want)


# ---------------------------------------------------------------- ground vehicles (bike / buggy / crawler)
GROUND_ROWS = (("m_MaxSpeed", "s"), ("m_MaxEnginePower", "s"), ("m_MaxBackwardSpeed", "back"),
               ("m_MaxBackwardEnginePower", "back"), ("m_DecelerationScale", "decel"))


def _engine_comp(g, world, veh, eo, size=0x40):
    """The physics copy of a ground vehicle's engine config: same handle, same bytes as the actor mirror."""
    tbl = fgl_table(g, world)
    handle = g.u64(veh + fgl(g)["handleOff"])
    mirror = g.read(veh + eo, size)
    t0 = getattr(g, "_gtype", None)
    for t in ([t0] if t0 is not None else []) + list(range(1024)):
        comp = fgl_comp(g, tbl, t, handle, 4096)
        if comp and g.read(comp, size) == mirror:
            g._gtype = t
            return comp
    return None


def _engine_comp_cached(g, world, veh):
    t = getattr(g, "_gtype", None)
    if t is None:
        return None
    return fgl_comp(g, fgl_table(g, world), t, g.u64(veh + fgl(g)["handleOff"]), 4096)


def apply_ground(g, c, speeds, orig):
    rep = []
    idle = all(abs(float(speeds.get(k, 1.0)) - 1.0) < 1e-6 for k in ("bike", "buggy", "crawler"))
    if idle and not orig.get("ground"):
        return rep
    gr = orig.setdefault("ground", {})
    for veh in instances_of(g, "DuneWheeledVehicle"):
        cat = category(g, veh)
        if cat not in ("bike", "buggy", "crawler"):
            continue
        sp = float(speeds.get(cat, 1.0))
        eo, est = _struct_of(g, g.cls(veh), "m_EngineConfig")
        if not est:
            continue
        key = str(g.u64(veh + fgl(g)["handleOff"]))
        tv = g.find_prop_in(g.cls(veh), "m_TerminalVelocitySpeed")
        if key not in gr:
            rows = [(g.find_prop_in(est, n), kind) for n, kind in GROUND_ROWS]
            rows = [(p["off"], kind) for p, kind in rows if p and p["type"] == "FloatProperty"]
            comp = _engine_comp(g, c["world"], veh, eo)
            gr[key] = {"rows": [[o, kind, _f(g, veh + eo + o)] for o, kind in rows],
                       "terminal": _f(g, veh + tv["off"]) if tv else None}
        else:
            comp = _engine_comp_cached(g, c["world"], veh)
        saved = gr[key]
        factor = {"s": sp, "back": min(sp, 1.5), "decel": max(1.0, sp)}
        for a in [veh + eo] + ([comp] if comp else []):
            for o, kind, v in saved["rows"]:
                g.write(a + o, struct.pack("<f", v * factor[kind]))
        if tv and saved["terminal"]:
            g.write(veh + tv["off"], struct.pack("<f", saved["terminal"] * sp))
        rep.append({"vehicle": g.obj_name(veh), "type": cat, "speed": sp, "physics": bool(comp)})
    return rep


# ---------------------------------------------------------------- engine module (thin air: fuel + heat)
# The ENGINE module is its own entity: VehicleModuleComponent.m_OrnithoperEngineConfig (OrnithopterEngineSettings)
# holds another m_MaxAltitudeRange + m_AltitudePowerConsumptionMultiplierCurve (light thopter: 750 m x1 -> 900 m x250).
# That curve is the thin-air warning, the extra fuel burn AND the altitude overheating (power -> temperature).
# Height mult moves its range + curve keys with the ceiling; no_burn flattens the curve to x1 (boost heat untouched).
def _engine_layout(g):
    if getattr(g, "_eng", None):
        return g._eng
    vmc = g.find_objects({("VehicleModuleComponent", "ScriptStruct"): None})[("VehicleModuleComponent", "ScriptStruct")]
    cfg = g.find_prop_in(vmc, "m_OrnithoperEngineConfig")["off"]
    es = g.find_objects({("OrnithopterEngineSettings", "ScriptStruct"): None})[("OrnithopterEngineSettings", "ScriptStruct")]
    lay = {"cfg": cfg, "range": g.find_prop_in(es, "m_MaxAltitudeRange")["off"],
           "broken": g.find_prop_in(es, "m_BrokenEngineMaxAltitudeRange")["off"],
           "curve": g.find_prop_in(es, "m_AltitudePowerConsumptionMultiplierCurve")["off"]}
    cf = g.find_objects({("CurveFloat", "Class"): None})[("CurveFloat", "Class")]
    rc = g.find_objects({("RichCurve", "ScriptStruct"): None})[("RichCurve", "ScriptStruct")]
    rk = g.find_objects({("RichCurveKey", "ScriptStruct"): None})[("RichCurveKey", "ScriptStruct")]
    lay["keys"] = g.find_prop_in(cf, "FloatCurve")["off"] + g.find_prop_in(rc, "Keys")["off"]
    lay["ksize"] = g.i32(rk + 0x58)
    lay["ktime"], lay["kval"] = g.find_prop_in(rk, "Time")["off"], g.find_prop_in(rk, "Value")["off"]
    g._eng = lay
    return lay


def engines(g, world):
    """Engine module components of live thopters (templates have no curve)."""
    L, F = _engine_layout(g), fgl(g)
    tbl = fgl_table(g, world)
    types = [g._engtype] if getattr(g, "_engtype", None) is not None else range(1024)
    for t in types:
        s = g.u64(tbl + t * 8)
        if not b.valid(s):
            continue
        s += F["storageAdd"]
        el, n = g.u64(s + F["elemsOff"]), g.i32(s + F["elemsOff"] + 8)
        if not b.valid(el) or not 0 < n <= 20000:
            continue
        raw = g.read(el, n * 0x18) or b""
        out = []
        for i in range(n):
            comp = struct.unpack_from("<Q", raw, i * 0x18 + 8)[0]
            cur = g.u64(comp + L["cfg"] + L["curve"]) if b.valid(comp) else 0
            if b.valid(cur) and g.class_name(cur) == "CurveFloat" and "AltitudeToPower" in (g.obj_name(cur) or ""):
                out.append((comp, cur))
        if out:
            g._engtype = t
            return out
    return []


def apply_engines(g, c, height, no_burn, orig):
    L = _engine_layout(g)
    rep = []
    for comp, cur in engines(g, c["world"]):
        base = comp + L["cfg"]
        rs = orig.setdefault("engine", {}).setdefault(str(comp), [[struct.unpack("<f", g.read(base + r + o, 4))[0]
                                                                     for o in (4, 12)] for r in (L["range"], L["broken"])])
        (lo, hi), (blo, bhi) = rs
        new_hi = hi * height
        new_lo = zone_lower(lo, hi, height, no_burn)
        g.write(base + L["range"] + 4, struct.pack("<f", new_lo))
        g.write(base + L["range"] + 12, struct.pack("<f", new_hi))
        if bhi > 0:
            g.write(base + L["broken"] + 4, struct.pack("<f", zone_lower(blo, bhi, height, no_burn) if blo > 0 else 0.0))
            g.write(base + L["broken"] + 12, struct.pack("<f", bhi * height))
        # curve keys: times follow the zone (start -> ceiling), tangents rescaled; no_burn = flat x1, no tangents
        arr, n = g.u64(cur + L["keys"]), g.i32(cur + L["keys"] + 8)
        if b.valid(arr) and 0 < n <= 16 and L["ksize"] == 28:
            keys = orig.setdefault("curve", {}).setdefault(g.obj_name(cur), [g.read(arr + i * 28, 28).hex()
                                                                            for i in range(n)])
            keys = [bytearray.fromhex(k) for k in keys]
            t0, t1 = struct.unpack_from("<f", keys[0], 4)[0], struct.unpack_from("<f", keys[-1], 4)[0]
            span = new_hi - new_lo
            for i, k in enumerate(keys):
                t, val, at, aw, lt, lw = struct.unpack_from("<ffffff", k, 4)
                if t1 > t0 and span > 0:
                    nt, scale = new_lo + (t - t0) / (t1 - t0) * span, (t1 - t0) / span
                else:
                    nt, scale = new_lo + i, 0.0
                if no_burn:
                    val, scale = 1.0, 0.0
                struct.pack_into("<ff", k, 4, nt, val)
                stretch = span / (t1 - t0) if t1 > t0 and span > 0 else 1.0      # weighted tangents: weight ~ time
                struct.pack_into("<ffff", k, 12, at * scale, aw * stretch, lt * scale, lw * stretch)
                g.write(arr + i * 28, bytes(k))
        warn = apply_hud(g, 1e9 if no_burn else new_lo, orig)
        rep.append({"hud_warning_m": warn})
        rep.append({"engine": g.obj_name(cur), "thin_air_m": round(new_lo / 100), "ceiling_m": round(new_hi / 100),
                    "curve": "flat" if no_burn else "vanilla"})
    return rep


# ---------------------------------------------------------------- HUD fuel warning
# W_OrnithopterHUD's ubergraph hardcodes "altitude > 750 m -> fuel consumption warning" (EX_DoubleConst 0x37
# 75000.0 passed to CheckAltitudeAndTriggerFuelWarning). The constant is patched in the loaded bytecode to the
# thin-air start (or out of reach with no_burn). Runtime only, stock back on game restart.
HUD_STOCK = 75000.0


def apply_hud(g, threshold_cm, orig):
    cls = g.find_objects({("W_OrnithopterHUD_C", "WidgetBlueprintGeneratedClass"): None})[
        ("W_OrnithopterHUD_C", "WidgetBlueprintGeneratedClass")]
    f = g.u64(cls + 0x48)
    while b.valid(f) and g.obj_name(f) != "ExecuteUbergraph_W_OrnithopterHUD":
        f = g.u64(f + 0x28)
    if not b.valid(f):
        return None
    arr, n = g.u64(f + 0x60), g.i32(f + 0x60 + 8)
    if not b.valid(arr) or not 0 < n < 200000:
        return None
    pos = orig.get("hud_pos")
    code = g.read(arr, n) or b""
    if pos is None or code[pos - 1:pos] != b"\x37":
        j = code.find(b"\x37" + struct.pack("<d", HUD_STOCK))
        if j < 0:
            return None
        pos = orig["hud_pos"] = j + 1
    g.write(arr + pos, struct.pack("<d", float(threshold_cm)))
    return round(threshold_cm / 100)
