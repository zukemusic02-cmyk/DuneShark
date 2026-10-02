"""Crate loot / enemy loot multipliers for the running solo world.

How the game's loot works (read from the live tables, 2026-10-01):
  LootTableRow          NumRolls @384 (int), PercentageChance @388 (float)
  LootWeightedTableRow  MinRolls @384, MaxRolls @388 (int), Weight @392 (float)
  BaseLootTableRow      ItemTemplateId @16 (FName), LootTable @112 / LootWeightedTable @160 (soft refs)
A row either gives an item (rolls = how many) or rolls another loot table (rolls = how many times).

Roots (the table a source starts from):
  crates  = every table a LootDistributionSettings row (DT_LootDistributionSettings / ..Override_*) or DT_ContainerLoot points at
  enemies = DT_LootTable_NPC* tables no other table points at (NPC, NPCAssault, NPCRusher, ...)
Only the ROOT rows are multiplied (each sub-table / item is rolled N times more), so a shared sub-table
like Basic_Base_ResourceT1 never gets both multipliers. Contract / specialization-key rows are left alone.
Runtime only: back to normal after a game restart. Originals are remembered per game process.
"""
import json, os, struct
import app_paths
import dune_bridge as b
import dune_items as di

ROWS = {"LootTableRow": "single", "LootWeightedTableRow": "weighted"}
DIST = ("LootDistributionSettingsRowBase",)
SKIP = ("Contract", "Specialization", "Keystone", "NPE")       # quest / progression rows: never duplicate
_ORIG = os.path.join(app_paths.APP_DIR, "loot_original.json")


def _soft(g, a):
    """Asset name of a soft object reference (TPersistentObjectPtr: weak ptr, tag, FSoftObjectPath)."""
    n = g.fname(a + 24)
    return n if n and n != "None" else None


def _tables(g):
    chunks, count = g.u64(g.GObjects), g.i32(g.GObjects + 0x14)
    if not b.valid(chunks) or not 1000 < count < 3000000:
        raise b.BridgeError("Object list is not ready.")
    out = {}
    for ci in range((count + 65535) // 65536):
        chunk = g.u64(chunks + ci * 8)
        n = min(65536, count - ci * 65536)
        raw = g.read(chunk, n * 24) if b.valid(chunk) else None
        if not raw:
            continue
        for i in range(n):
            obj = struct.unpack_from("<Q", raw, i * 24)[0]
            if not b.valid(obj):
                continue
            hdr = g.read(obj + 0x10, 0x10)
            if not hdr:
                continue
            cls_, nci, nnum = struct.unpack("<QII", hdr)
            nm = g.name(nci)
            if nm and nm.startswith("DT_") and "Loot" in nm and not nnum and g.obj_name(cls_) == "DataTable":
                out[nm] = (obj, g.obj_name(g.u64(obj + 0x28)))
    return out


def scan(g):
    """-> {"crate": [table names], "enemy": [...]}, plus the rows of every loot table."""
    cache = g.__dict__.setdefault("_loot", {})
    if cache.get("pid") == g.pid and cache.get("roots"):
        return cache
    tabs = _tables(g)
    rows, referenced, crate = {}, set(), set()
    for nm, (dt, rs) in tabs.items():
        if rs in ROWS:
            rows[nm] = []
            for rid, row in di.rows(g, dt):
                sub = _soft(g, row + 112) or _soft(g, row + 160)
                item = g.fname(row + 16)
                rows[nm].append((rid, row, ROWS[rs], sub, item if item and item != "None" else None))
                if sub:
                    referenced.add(sub)
        elif rs in DIST:
            for rid, row in di.rows(g, dt):
                for off in (96, 144):
                    t = _soft(g, row + off)
                    if t:
                        crate.add(t)
        elif rs == "ContainerLootRowBase":
            for rid, row in di.rows(g, dt):
                for off in (16, 64):
                    t = _soft(g, row + off)
                    if t:
                        crate.add(t)
    enemy = {nm for nm in rows if nm.startswith("DT_LootTable_NPC") and nm not in referenced}
    cache.update({"pid": g.pid, "rows": rows,
                  "roots": {"crate": sorted(t for t in crate if t in rows and t not in enemy and not any(k in t for k in SKIP)),
                            "enemy": sorted(enemy)}})
    return cache


def _addrs(g, kind):
    """(key, address, size) of every roll count to scale for this kind."""
    sc = scan(g)
    out = []
    for nm in sc["roots"][kind]:
        for rid, row, typ, sub, item in sc["rows"][nm]:
            ref = (sub or item or "")
            if any(s in ref for s in SKIP):
                continue
            if typ == "single":
                out.append(("%s/%s/n" % (nm, rid), row + 384))
            else:
                out.append(("%s/%s/min" % (nm, rid), row + 384))
                out.append(("%s/%s/max" % (nm, rid), row + 388))
    return out


def set_multiplier(g, kind, mult):
    if kind not in ("crate", "enemy"):
        raise b.BridgeError("Unknown loot kind " + str(kind))
    mult = int(float(mult))
    if not 1 <= mult <= 5:
        raise b.BridgeError("Loot multiplier: 1 to 5.")
    try:
        orig = json.load(open(_ORIG, encoding="utf-8"))
    except Exception:
        orig = {}
    if orig.get("pid") != g.pid:
        orig = {"pid": g.pid, "rolls": {}, "mult": {}}
    addrs = _addrs(g, kind)
    for key, a in addrs:                                      # first time we see a row: its value is the original
        orig["rolls"].setdefault(key, g.i32(a))
    changed = 0
    for key, a in addrs:
        base = orig["rolls"][key]
        if 0 < base < 1000:
            g.write(a, struct.pack("<i", base * mult))
            changed += 1
    orig["mult"][kind] = mult
    json.dump(orig, open(_ORIG, "w", encoding="utf-8"), indent=0)
    return {"kind": kind, "mult": mult, "rows": changed, "tables": len(scan(g)["roots"][kind])}


def current(g):
    try:
        orig = json.load(open(_ORIG, encoding="utf-8"))
    except Exception:
        return {"crate": 1, "enemy": 1}
    m = orig.get("mult", {}) if orig.get("pid") == g.pid else {}
    return {"crate": m.get("crate", 1), "enemy": m.get("enemy", 1)}


if __name__ == "__main__":
    import sys
    g = b.Game()
    g.attach()
    sc = scan(g)
    for k, v in sc["roots"].items():
        print(k, len(v), v[:12])
    if len(sys.argv) == 3:
        print(set_multiplier(g, sys.argv[1], sys.argv[2]))
