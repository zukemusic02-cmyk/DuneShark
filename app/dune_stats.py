"""Extract augment stat ranges from the running game -> augment_stats.json (read-only).

Each augment has a DataAsset DA_AUGMENT_<itemId> (AugmentStatsPerQualityDataAsset):
  MinQualityLevel, StatsData[] = { Operation, WeaponStats[] / ArmorStats[] / ItemStats[] (stat names),
                                   RangeCurve (CurveVector: X and Y = the two ends of the roll range, key time = grade) }
Verified against the in-game card: Defensive Grip Adjuster G5 = Attack Stamina Cost +10%, Block Stamina Cost -18..-20%
(in-game roll -19.3%).
Usage:  python dune_stats.py
"""
import json, os, struct, sys
import app_paths
import dune_bridge as b

HERE = app_paths.APP_DIR
OUT = os.path.join(HERE, "augment_stats.json")
STRUCT_SIZE = 0x58          # UStruct::PropertiesSize
CURVE_KEYS = 0x70           # FRichCurve::Keys (TArray<FRichCurveKey>)
FLOATCURVES = 0x30          # UCurveVector::FloatCurves[3], each FRichCurve 0x80


def main():
    g = b.Game()
    g.attach()
    # ---- one pass: all augment data assets + needed structs ----
    chunks, count = g.u64(g.GObjects), g.i32(g.GObjects + 0x14)
    assets = {}
    for ci in range((count + 65535) // 65536):
        chunk = g.u64(chunks + ci * 8)
        n = min(65536, count - ci * 65536)
        raw = g.read(chunk, n * 24) if b.valid(chunk) else None
        if not raw:
            continue
        for i in range(n):
            o = struct.unpack_from("<Q", raw, i * 24)[0]
            if not b.valid(o):
                continue
            hdr = g.read(o + 0x10, 0x10)
            if not hdr:
                continue
            cls, nci, nnum = struct.unpack("<QII", hdr)
            nm = g.name(nci)
            if nm and nm.startswith("DA_AUGMENT_") and not nnum and g.obj_name(cls) == "AugmentStatsPerQualityDataAsset":
                assets[nm[len("DA_AUGMENT_"):]] = o
    if not assets:
        raise b.BridgeError("No augment data loaded.")
    rk = g.find_objects({("RichCurveKey", "ScriptStruct"): None})[("RichCurveKey", "ScriptStruct")]
    key_size = g.i32(rk + STRUCT_SIZE)

    def curve(a):
        p, n = struct.unpack("<Qi", g.read(a + CURVE_KEYS, 12))
        pts = {}
        for i in range(n if b.valid(p) and 0 < n < 64 else 0):
            t, v = struct.unpack("<ff", g.read(p + i * key_size + 4, 8))
            pts[int(round(t))] = round(v, 5)
        return pts

    def names(arr_addr, elem_size):
        p, n = struct.unpack("<Qi", g.read(arr_addr, 12))
        return [g.fname(p + i * elem_size) for i in range(n if b.valid(p) and 0 < n < 16 else 0)]

    any_da = next(iter(assets.values()))
    dcls = g.cls(any_da)
    sd = g.find_prop_in(dcls, "StatsData")
    minq = g.find_prop_in(dcls, "MinQualityLevel")["off"]
    f = g.u64(dcls + 0x50)                    # StatsData element struct
    while b.valid(f) and g.fname(f + 0x28) != "StatsData":
        f = g.u64(f + 0x20)
    inner = g.u64(f + 0x78)
    est, esz = g.u64(inner + 0x78), g.i32(inner + 0x3C)
    off = {k: g.find_prop_in(est, k)["off"] for k in ("Operation", "WeaponStats", "ArmorStats", "ItemStats", "RangeCurve")}

    out = {}
    for item, da in sorted(assets.items()):
        p, n = struct.unpack("<Qi", g.read(da + sd["off"], 12))
        stats = []
        for k in range(n if b.valid(p) and 0 < n < 16 else 0):
            e = p + k * esz
            cv = g.u64(e + off["RangeCurve"])
            if not b.valid(cv):
                continue
            lo, hi = curve(cv + FLOATCURVES), curve(cv + FLOATCURVES + 0x80)
            stats.append({"op": (g.read(e + off["Operation"], 1) or b"\0")[0],
                          "weapon": names(e + off["WeaponStats"], 8), "armor": names(e + off["ArmorStats"], 8),
                          "item": names(e + off["ItemStats"], 8), "curve": g.obj_name(cv),
                          "range": {str(q): sorted([lo.get(q, 1.0), hi.get(q, 1.0)]) for q in range(6)}})
        out[item] = {"min_grade": (g.read(da + minq, 1) or b"\0")[0], "stats": stats}
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print(len(out), "augments ->", OUT)


if __name__ == "__main__":
    try:
        main()
    except b.BridgeError as e:
        print("ERROR:", e); sys.exit(1)
