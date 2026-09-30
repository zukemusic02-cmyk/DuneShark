"""Extract item-card details from the running game: description, volume and crafting recipes.

Writes item_details.json: { itemId: {desc, volume}, ... , "_recipes": {outcomeId: {...}}, "_fabricators": {...} }
Texts (descriptions, fabricator names) are translated by the game itself (dune_items.texts_to_strings).
Usage:  python dune_details.py
"""
import json, os, struct, sys
import app_paths
import dune_bridge as b
import dune_items as di

HERE = app_paths.APP_DIR
OUT = os.path.join(HERE, "item_details.json")

S_LONGDESC, S_SIZE = 0x30, 0x120                 # GameItemStaticData offsets (StaticData @ row+0xB8)
R_RECIPE = 0x40                                  # ItemCraftingRecipeRowBase.Recipe (ItemCraftingRecipe)
RC_OUTCOME, RC_WATER_OUT, RC_TIME, RC_PERQ = 0x0, 0x50, 0x54, 0x60
R_PRODTYPES = 0xB0
ING_MAP, ING_WATER, ING_SIZE = 0x0, 0x50, 0x58   # Ingredients struct
MAP_STRIDE = 0x14                                # TSetElement<TPair<FName,int32>>: 8 + 4 + hash 8
SET_STRIDE = 0x10                                # TSetElement<FName>: 8 + hash 8


def name_int_map(g, a):
    """TMap<FName-struct, int32> -> {name: value} (skips free slots)."""
    ptr, num = struct.unpack("<Qi", g.read(a, 12))
    out = {}
    if not b.valid(ptr) or not 0 <= num < 256:
        return out
    raw = g.read(ptr, num * MAP_STRIDE) or b""
    for i in range(num):
        n = g.fname(ptr + i * MAP_STRIDE)
        v = struct.unpack_from("<i", raw, i * MAP_STRIDE + 8)[0]
        if n and n != "None" and 0 < v < 1_000_000:
            out[n] = v
    return out


def name_set(g, a):
    ptr, num = struct.unpack("<Qi", g.read(a, 12))
    if not b.valid(ptr) or not 0 <= num < 64:
        return []
    return [n for n in (g.fname(ptr + i * SET_STRIDE) for i in range(num)) if n and n != "None"]


def table_rows(g, name):
    dt = g.find_objects({(name, "DataTable"): None})[(name, "DataTable")]
    return list(di.rows(g, dt))


def main():
    g = b.Game()
    g.attach()
    c = g.ctx()
    g.require_solo(c)

    # ---- item descriptions + volume ----
    items = []
    for cat, dt in di.item_tables(g).items():
        for rid, row in di.rows(g, dt):
            st = row + di.STATIC
            vol = struct.unpack("<f", g.read(st + S_SIZE, 4))[0]
            items.append((rid, st + S_LONGDESC, round(vol, 2)))

    # ---- fabricators ----
    fabs = table_rows(g, "DT_CraftingProductionTypes")

    # ---- recipes ----
    recipes = {}
    for rname, row in table_rows(g, "DT_ItemsCraftingRecipes"):
        r = row + R_RECIPE
        outcome = name_int_map(g, r + RC_OUTCOME)
        ptr, num = struct.unpack("<Qi", g.read(r + RC_PERQ, 12))
        grades = []
        if b.valid(ptr) and 0 < num <= 8:
            for q in range(num):
                e = ptr + q * ING_SIZE
                grades.append({"items": name_int_map(g, e + ING_MAP),
                               "water": struct.unpack("<i", g.read(e + ING_WATER, 4))[0]})
        rec = {"recipe": rname, "out": outcome,
               "time": round(struct.unpack("<f", g.read(r + RC_TIME, 4))[0], 1),
               "grades": grades, "fabricators": name_set(g, row + R_PRODTYPES)}
        for oid in outcome:
            recipes.setdefault(oid, []).append(rec)

    # ---- translate texts in one go ----
    texts = [t for _, t, _ in items] + [row + 0x10 for _, row in fabs]
    strings = di.texts_to_strings(g, c, texts)
    details = {rid: {"desc": (s or "").strip(), "volume": vol}
               for (rid, _, vol), s in zip(items, strings[:len(items)])}
    details["_fabricators"] = {fid: s or fid for (fid, _), s in zip(fabs, strings[len(items):])}
    details["_recipes"] = recipes
    json.dump(details, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("items", len(items), "recipes for", len(recipes), "items ->", OUT)


if __name__ == "__main__":
    try:
        main()
    except b.BridgeError as e:
        print("ERROR:", e); sys.exit(1)
