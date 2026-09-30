"""Instant ability cooldowns (recipe from the Prometheu5 CT, verified for 1.5.3.4).

Walks the player's DuneAbilitySystemComponent.ActivatableAbilities.Items (FGameplayAbilitySpec, 256 bytes each);
each spec's instance arrays (+112/+128/+144) hold the player's own ability objects (outer == character). On those
instances only - never shared class defaults - the cooldown is switched off:
  CooldownGameplayEffectClass = None, bUseHiddenCooldown = false, SkipCooldownCommit = true,
  CacheCooldownFor* / CachedCooldown* = 0.
Originals are kept per game process in abilities_original.json and written back when turned off.
"""
import json, os, struct
import app_paths
import dune_bridge as b

HERE = app_paths.APP_DIR
ORIG = os.path.join(HERE, "abilities_original.json")
SPEC_SIZE = 256
INSTANCE_ARRAYS = (112, 128, 144)
ZERO_FIELDS = ("CacheCooldownForCancelled", "CacheCooldownForTargetUsage", "CacheCooldownForTerrainUsage",
               "CachedCooldownFull", "CachedCooldownReduced")


def _prop(g, obj, name):
    """(offset, type, size, bool byte mask) of a property on obj's class chain."""
    s = g.cls(obj)
    while b.valid(s):
        f = g.u64(s + 0x50)
        while b.valid(f):
            if g.fname(f + 0x28) == name:
                t = g.fname(g.u64(f + 8))
                mask = g.read(f + 0x7A, 1)[0] if t == "BoolProperty" else None
                return g.i32(f + 0x4C), t, g.i32(f + 0x3C), mask
            f = g.u64(f + 0x20)
        s = g.u64(s + 0x40)
    return None


def _is_ability(g, obj):
    cache = g.__dict__.setdefault("_isab", {})                # per game process, never carried over
    cl = g.cls(obj)
    if cl not in cache:
        s, hit = cl, False
        for _ in range(20):
            if not b.valid(s):
                break
            if g.obj_name(s) == "GameplayAbility":
                hit = True
                break
            s = g.u64(s + 0x40)
        cache[cl] = hit
    return cache[cl]


def abilities(g, c):
    owner = c["char"]
    asc = g.ptr(owner, "m_AbilitySystemComponent")
    if not asc:
        return []
    cont = g.find_prop_in(g.cls(asc), "ActivatableAbilities")
    st = g.find_objects({("GameplayAbilitySpecContainer", "ScriptStruct"): None})[
        ("GameplayAbilitySpecContainer", "ScriptStruct")]
    items = g.find_prop_in(st, "Items")
    if not cont or not items:
        return []
    arr = asc + cont["off"] + items["off"]
    data, n, cap = g.u64(arr), g.i32(arr + 8), g.i32(arr + 12)
    if not b.valid(data) or not 0 <= n <= cap <= 1000:
        return []
    out, seen = [], set()
    for i in range(n):
        spec = data + i * SPEC_SIZE
        for off in INSTANCE_ARRAYS:
            a, k, kc = g.u64(spec + off), g.i32(spec + off + 8), g.i32(spec + off + 12)
            if not b.valid(a) or not 0 <= k <= kc < 100:
                continue
            for j in range(k):
                o = g.u64(a + j * 8)
                if b.valid(o) and o not in seen and g.u64(o + 0x20) == owner and _is_ability(g, o):
                    seen.add(o)
                    out.append(o)
    return out


def _load(g):
    try:
        o = json.load(open(ORIG, encoding="utf-8"))
    except Exception:
        o = {}
    return o if o.get("pid") == g.pid else {"pid": g.pid, "objs": {}}


def _targets(g, obj):
    """[(name, offset, type, size, mask, wanted raw bytes)]"""
    out = []
    for name, want in [("CooldownGameplayEffectClass", 0), ("bUseHiddenCooldown", False), ("SkipCooldownCommit", True)] + \
                      [(n, 0) for n in ZERO_FIELDS]:
        p = _prop(g, obj, name)
        if p:
            out.append((name,) + p + (want,))
    return out


def apply(g, c):
    orig = _load(g)
    changed = 0
    for obj in abilities(g, c):
        key = str(obj)
        rec = orig["objs"].setdefault(key, {"cls": g.cls(obj), "vals": {}})
        if rec["cls"] != g.cls(obj):                                   # address reused by something else
            rec = orig["objs"][key] = {"cls": g.cls(obj), "vals": {}}
        for name, off, t, size, mask, want in _targets(g, obj):
            a = obj + off
            if t == "BoolProperty":
                cur = g.read(a, 1)[0]
                rec["vals"].setdefault(name, cur & mask)
                new = (cur | mask) if want else (cur & ~mask & 0xFF)
                if new != cur:
                    g.write(a, bytes([new]))
                    changed += 1
            elif size in (4, 8):
                cur = g.read(a, size)
                rec["vals"].setdefault(name, cur.hex())
                new = bytes(size)
                if cur != new:
                    g.write(a, new)
                    changed += 1
    json.dump(orig, open(ORIG, "w", encoding="utf-8"))
    return {"abilities": len(orig["objs"]), "changed": changed}


def restore(g):
    orig = _load(g)
    for key, rec in orig["objs"].items():
        obj = int(key)
        if g.cls(obj) != rec["cls"]:
            continue
        for name, val in rec["vals"].items():
            p = _prop(g, obj, name)
            if not p:
                continue
            off, t, size, mask = p
            if t == "BoolProperty":
                cur = g.read(obj + off, 1)[0]
                g.write(obj + off, bytes([(cur & ~mask & 0xFF) | (val & mask)]))
            else:
                g.write(obj + off, bytes.fromhex(val))
    try:
        os.remove(ORIG)
    except OSError:
        pass
