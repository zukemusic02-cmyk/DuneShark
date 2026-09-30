"""Extract Dune's full item catalogue from the running game (read-only except for text lookups).

Reads every DT_BaseItems_* DataTable in memory and asks the game itself to turn each item's
Name (an FText localisation key) into the English display string, via
KismetTextLibrary.Conv_TextToString run on the game thread (dune_bridge.dispatch), a batch per frame.

Usage:  python dune_items.py            -> writes items_game.json next to this file
"""
import json, os, struct, sys
import app_paths
import dune_bridge as b

HERE = app_paths.APP_DIR
OUT = os.path.join(HERE, "items_game.json")

STATIC = 0xB8            # BaseItemTableRow.StaticData (GameItemStaticData)
S_NAME, S_SHORT, S_TAGS, S_ENABLED, S_MAXQ = 0x0, 0x18, 0x80, 0xA8, 0xAC
ROW_DEPRECATED = 0x2C0
BATCH = 400


def item_tables(g):
    """All DT_BaseItems_* DataTables currently loaded."""
    chunks = g.u64(g.GObjects)
    count = g.i32(g.GObjects + 0x14)
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
            cls, nci, nnum = struct.unpack("<QII", hdr)
            nm = g.name(nci)
            if nm and nm.startswith("DT_BaseItems_") and not nnum and g.obj_name(cls) == "DataTable":
                out[nm[len("DT_BaseItems_"):]] = obj
    return out


def rows(g, dt):
    data, num = g.u64(dt + 0x30), g.i32(dt + 0x38)
    if not b.valid(data) or not 0 <= num < 20000:
        return
    raw = g.read(data, num * 0x18) or b""
    for i in range(num):
        key, row = struct.unpack_from("<QQ", raw, i * 0x18)
        name = g.fname(data + i * 0x18)
        if name and b.valid(row):
            yield name, row


def tags(g, a):
    ptr, num = struct.unpack("<Qi", g.read(a, 12))
    if not b.valid(ptr) or not 0 < num < 64:
        return []
    return [g.fname(ptr + i * 8) for i in range(num)]


def texts_to_strings(g, c, text_addrs):
    """English display strings for FText objects, computed by the game."""
    objs = g.find_objects({("Default__KismetTextLibrary", "KismetTextLibrary"): None,
                           ("Default__GameplayStatics", "GameplayStatics"): None})
    lib = objs[("Default__KismetTextLibrary", "KismetTextLibrary")]
    gameplay = objs[("Default__GameplayStatics", "GameplayStatics")]
    fn = g.find_function(lib, "Conv_TextToString")
    ps = {p["name"]: p for p in g.params(fn)}
    if ps.get("InText", {}).get("off") != 0 or ps.get("ReturnValue", {}).get("off") != 0x18:
        raise b.BridgeError("Conv_TextToString layout changed: %s" % ps)
    pe = g.u64(g.u64(gameplay) + b.PE_SLOT)
    if pe != g.ev("objectEvent", pe):
        raise b.BridgeError("ProcessEvent check failed.")
    out = []
    for start in range(0, len(text_addrs), BATCH):
        part = text_addrs[start:start + BATCH]
        blocks = g.alloc(len(part) * 0x30)
        buf = bytearray(len(part) * 0x30)
        for i, a in enumerate(part):
            buf[i * 0x30:i * 0x30 + 0x18] = g.read(a, 0x18)   # FText by value (read-only use, never destroyed)
        g.write(blocks, bytes(buf))
        calls = b"".join(b.pe_call(pe, lib, fn, blocks + i * 0x30) for i in range(len(part)))
        b.dispatch(g, c, calls, timeout=10)
        res = g.read(blocks, len(part) * 0x30)
        for i in range(len(part)):
            p, n, m = struct.unpack_from("<Qii", res, i * 0x30 + 0x18)
            s = None
            if b.valid(p) and 0 < n < 1024:
                raw = g.read(p, n * 2)
                s = raw.decode("utf-16le", "replace").rstrip("\0") if raw else None
            out.append(s)
        print("  names %d/%d" % (min(start + BATCH, len(text_addrs)), len(text_addrs)))
    return out


def main():
    g = b.Game()
    g.attach()
    c = g.ctx()
    g.require_solo(c)
    tables = item_tables(g)
    print("tables:", ", ".join("%s" % k for k in sorted(tables)))
    items = []
    for cat, dt in sorted(tables.items()):
        for rid, row in rows(g, dt):
            st = row + STATIC
            items.append({
                "id": rid, "category": cat,
                "enabled": bool((g.read(st + S_ENABLED, 1) or b"\0")[0]),
                "deprecated": bool((g.read(row + ROW_DEPRECATED, 1) or b"\0")[0]),
                "max": g.i32(st + S_MAXQ),
                "tags": tags(g, st + S_TAGS),
                "_text": st + S_NAME,
            })
    print("rows:", len(items))
    names = texts_to_strings(g, c, [it["_text"] for it in items])
    for it, nm in zip(items, names):
        it["name"] = nm or ""
        del it["_text"]
    json.dump(items, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", OUT, "named:", sum(1 for i in items if i["name"]))


if __name__ == "__main__":
    try:
        main()
    except b.BridgeError as e:
        print("ERROR:", e); sys.exit(1)
