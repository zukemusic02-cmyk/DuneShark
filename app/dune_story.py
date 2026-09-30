"""Story helper: find and finish the quest step you're on (one step at a time, in the game's order).

Progress comes from the solo save (journey_story_node, rewritten by the game every ~20 s); which step is
really next is asked live from the game (DunePlayerJourneyComponent.CanNodeBeCompletedFromFullname), so the
save's delay doesn't matter. Finishing uses the game's own CompleteStoryNodeByName.

kind "story" = main quests + story DLC (DA_MQ_, DA_DLC_); kind "side" = side / faction / Landsraad (DA_SQ_, DA_FQ_, DA_LDR_).
"""
import glob, os, re, sqlite3, struct, tempfile, zlib
import app_paths
import dune_bridge as b

SAVE_ROOT = os.path.expandvars(r"%LOCALAPPDATA%\DuneSandbox\Saved\Cloud\PlayerClientStorage\FLS_retail")


def find_save():
    """The solo save of whoever plays (any Steam account): the most recently written game.db."""
    saves = glob.glob(os.path.join(SAVE_ROOT, "*", "game.db"))
    if not saves:
        raise b.BridgeError("Solo save not found - load your solo world once first.")
    return max(saves, key=os.path.getmtime)
KINDS = {"story": ("DA_MQ_", "DA_DLC_"), "side": ("DA_SQ_", "DA_FQ_", "DA_LDR_")}
IN_PROGRESS = 0x8C


def save_nodes():
    """[(node_id, state_byte)] from the solo save, in save order."""
    raw = open(find_save(), "rb").read()
    tmp = os.path.join(tempfile.gettempdir(), "duneshark_journey.sqlite")
    open(tmp, "wb").write(zlib.decompress(raw[8:]))
    con = sqlite3.connect(tmp)
    rows = [(nid, (st or b"\0")[0]) for nid, st in
            con.execute("select story_node_id, complete_condition_state from journey_story_node")]
    con.close()
    return rows


NAMES_FILE = os.path.join(app_paths.APP_DIR, "journey_names.json")
_names = None


def build_names(g, c):
    """Real on-screen names of every quest / step (JourneyStoryNode m_GUIData.m_Name, translated by the game)
    -> journey_names.json {full.node.path: "Name"}."""
    import json
    import dune_items as di
    cls = g.find_objects({("JourneyStoryNode", "Class"): None})[("JourneyStoryNode", "Class")]
    chunks, count = g.u64(g.GObjects), g.i32(g.GObjects + 0x14)
    nodes = []
    for ci in range((count + 65535) // 65536):
        chunk = g.u64(chunks + ci * 8)
        n = min(65536, count - ci * 65536)
        raw = g.read(chunk, n * 24) if b.valid(chunk) else None
        for i in range(n if raw else 0):
            o = struct.unpack_from("<Q", raw, i * 24)[0]
            if b.valid(o) and g.u64(o + 0x10) == cls:
                nodes.append(o)

    def fstr(a):
        p, n, _ = struct.unpack("<Qii", g.read(a, 16))
        return g.read(p, n * 2).decode("utf-16le", "replace").rstrip("\0") if b.valid(p) and 0 < n < 300 else ""

    paths, texts = [], []
    for o in nodes:
        chain, x = [], o
        while b.valid(x) and len(chain) < 12 and g.class_name(x) == "JourneyStoryNode":
            chain.append(fstr(x + 0x28))                      # m_UniqueName
            x = g.u64(x + 0x210)                              # m_ParentNode
        gui = g.u64(o + 0x48)                                 # m_GUIData (JourneyTreeNodeGuiData)
        if not b.valid(gui) or not all(chain):
            continue
        paths.append(".".join(reversed(chain)))
        texts.append(gui + 0xD8)                              # DuneTreeEntryData.m_Name
    strings = di.texts_to_strings(g, c, texts)
    names = {p: s.strip() for p, s in zip(paths, strings) if s and "MISSING STRING" not in s}
    json.dump(names, open(NAMES_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    global _names
    _names = names
    return len(names)


def real_name(path):
    global _names
    if _names is None:
        try:
            import json
            _names = json.load(open(NAMES_FILE, encoding="utf-8"))
        except Exception:
            _names = {}
    return _names.get(path)


def pretty(s):
    s = re.sub(r"^DA_(MQ|DLC|SQ|FQ|LDR)_(Combat_|Crafting_|Gathering_|Exploration_)?", "", s)
    s = s.replace("_", " ")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s).strip()


def candidates(kind):
    """Leaf steps of the quests (of this kind) that have something in progress."""
    rows = save_nodes()
    ids = [r[0] for r in rows]
    idset = set(ids)
    pref = KINDS[kind]
    # active quest = a step in progress, or some steps done (0x01) while others aren't (0x0C)
    seen = {}
    for nid, st in rows:
        if nid.startswith(pref):
            seen.setdefault(nid.split(".")[0], set()).add(st)
    roots = {r for r, sts in seen.items() if IN_PROGRESS in sts or (0x01 in sts and 0x0C in sts)}
    leaves = [nid for nid in ids if nid.split(".")[0] in roots and "." in nid
              and not any(x.startswith(nid + ".") for x in idset)]
    return leaves


def journey_component(g, c):
    return b.find_owned(g, c["pc"], ("BP_JourneyComponent_C", "DunePlayerJourneyComponent"))


def can_complete_many(g, c, comp, nodes):
    """One game-thread pass: CanNodeBeCompletedFromFullname for every node -> [bool]."""
    if not nodes:
        return []
    fn = g.find_function(comp, "CanNodeBeCompletedFromFullname")
    ps = {p["name"]: p for p in g.params(fn)}
    arg, ret = ps["NodeFullPathName"]["off"], ps["ReturnValue"]["off"]
    pe = g.u64(g.u64(comp) + b.PE_SLOT)
    if pe != g.ev("componentEvent", pe):
        raise b.BridgeError("Unexpected ProcessEvent on the journey component.")
    blk = 0x20
    strs = [n.encode("utf-16le") + b"\0\0" for n in nodes]
    size = len(nodes) * blk + sum(len(s) for s in strs)
    mem = g.alloc(size)
    buf = bytearray(size)
    cur = len(nodes) * blk
    for i, s in enumerate(strs):
        buf[cur:cur + len(s)] = s
        struct.pack_into("<Qii", buf, i * blk + arg, mem + cur, len(s) // 2, len(s) // 2)
        cur += len(s)
    g.write(mem, bytes(buf))
    b.dispatch(g, c, b"".join(b.pe_call(pe, comp, fn, mem + i * blk) for i in range(len(nodes))), timeout=10)
    res = g.read(mem, len(nodes) * blk)
    return [bool(res[i * blk + ret]) for i in range(len(nodes))]


def current_step(g, c, kind):
    """The step of this kind the game will accept right now (tracked quest first), or None.
    Side: your tracked contract (NPC / trainer jobs) comes first, then side journey quests."""
    if kind == "side":
        try:
            st = current_contract_step(g, c)
            if st:
                return st
        except b.BridgeError:
            pass
    comp = journey_component(g, c)
    leaves = candidates(kind)
    ok = [n for n, yes in zip(leaves, can_complete_many(g, c, comp, leaves)) if yes]
    if not ok:
        return None
    tracked = ""
    try:
        card = b.find_owned(g, c["pc"], ("JourneyCardStateComponent",))
        p = g.find_prop_in(g.cls(card), "m_SavedTrackedJourneyCardName")
        ptr, num, _ = struct.unpack("<Qii", g.read(card + p["off"], 16))
        tracked = g.read(ptr, num * 2).decode("utf-16le").rstrip("\0") if b.valid(ptr) and 0 < num < 512 else ""
    except b.BridgeError:
        pass
    ok.sort(key=lambda n: n.split(".")[0] != tracked)          # tracked quest first
    n = ok[0]
    parts = n.split(".")
    # same wording as the in-game tracker: Mission (e.g. "The Fall of the Mithra") > objective > step
    mission = ".".join(parts[:2]) if len(parts) > 2 else parts[0]
    return {"node": n,
            "quest": real_name(mission) or real_name(parts[0]) or pretty(parts[min(1, len(parts) - 1)]),
            "section": (real_name(".".join(parts[:-1])) or pretty(parts[-2])) if len(parts) > 3 else "",
            "step": real_name(n) or pretty(parts[-1]), "others": len(ok) - 1}


# ---------------------------------------------------------------- contracts (side jobs from NPCs / trainers)
def _contract_layout(g):
    lay = g.__dict__.get("_contract_lay")
    if not lay:
        st = g.find_objects({("ActiveContractStateFastArrayItem", "ScriptStruct"): None})[("ActiveContractStateFastArrayItem", "ScriptStruct")]
        lay = g._contract_lay = {"uid": g.find_prop_in(st, "ItemUid")["off"], "name": g.find_prop_in(st, "Name")["off"],
                                 "prog": g.find_prop_in(st, "Progress")["off"], "size": g.i32(st + 0x3C) or 0xE0}
    return lay


def contracts(g, c):
    """[(uid, name, [(progress, max)...])] from the player's ContractsCoordinatorComponent, plus the tracked uid."""
    comp = b.find_owned(g, c["char"], ("ContractsCoordinatorComponent",))
    cls = g.cls(comp)
    act = g.find_prop_in(cls, "m_ActiveContracts")["off"]
    tracked = struct.unpack("<q", g.read(comp + g.find_prop_in(cls, "m_TrackedContractItemUid")["off"], 8))[0]
    lay = _contract_layout(g)
    items_off = 0x128                                          # ActiveContractStateFastArray.Items
    ptr, num = struct.unpack("<Qi", g.read(comp + act + items_off, 12))
    out = []
    for k in range(num if b.valid(ptr) and 0 <= num < 64 else 0):
        e = ptr + k * lay["size"]
        uid = struct.unpack("<q", g.read(e + lay["uid"], 8))[0]
        ap, an = struct.unpack("<Qi", g.read(e + lay["prog"], 12))
        prog = []
        for j in range(an if b.valid(ap) and 0 <= an < 32 else 0):
            mx, p = struct.unpack("<ii", g.read(ap + j * 8, 8))
            prog.append((p, mx))
        out.append((uid, g.fname(e + lay["name"]) or "", prog))
    return comp, tracked, out


_contract_text = {}


def contract_texts(g, c, name):
    """(title, [step titles]) of a contract, from its DA_CT_<name> ContractDataAsset, translated by the game."""
    if name in _contract_text:
        return _contract_text[name]
    import dune_items as di
    try:
        da = g.find_objects({("DA_CT_" + name, "ContractDataAsset"): None})[("DA_CT_" + name, "ContractDataAsset")]
    except b.BridgeError:
        _contract_text[name] = (None, [])
        return _contract_text[name]
    cs = g.find_objects({("ContractSettings", "ScriptStruct"): None})[("ContractSettings", "ScriptStruct")]
    st = da + 0x30                                            # m_Settings
    title_off = g.find_prop_in(cs, "Title")["off"]
    f = g.u64(cs + 0x50)
    while b.valid(f) and g.fname(f + 0x28) != "Conditions":
        f = g.u64(f + 0x20)
    cp, cn = struct.unpack("<Qi", g.read(st + g.i32(f + 0x4C), 12))
    csize = g.i32(g.u64(f + 0x78) + 0x3C)                     # ContractCondition element size; Title @ +0x8
    texts = [st + title_off] + [cp + i * csize + 0x8 for i in range(cn if b.valid(cp) and 0 < cn < 32 else 0)]
    s = di.texts_to_strings(g, c, texts)
    _contract_text[name] = (s[0] or None, [x or "" for x in s[1:]])
    return _contract_text[name]


def current_contract_step(g, c):
    comp, tracked, cons = contracts(g, c)
    cons.sort(key=lambda x: x[0] != tracked)                   # tracked contract first
    for uid, name, prog in cons:
        for idx, (p, mx) in enumerate(prog):
            if p < mx:
                title, steps = contract_texts(g, c, name)
                step = steps[idx] if idx < len(steps) and steps[idx] else "Step %d of %d" % (idx + 1, len(prog))
                return {"contract": uid, "cond": idx, "missing": mx - p,
                        "quest": title or pretty(re.sub(r"_\d+$", "", name)),
                        "section": "Contract · step %d of %d" % (idx + 1, len(prog)),
                        "step": step + (" (%d/%d)" % (p, mx) if mx > 1 else ""),
                        "node": "contract:%d:%d" % (uid, idx), "others": 0}
    return None


def finish_contract(g, c, uid, idx):
    comp, tracked, cons = contracts(g, c)
    con = next((x for x in cons if x[0] == uid), None)
    if not con or idx >= len(con[2]):
        raise b.BridgeError("That contract step isn't active anymore.")
    p, mx = con[2][idx]
    if p >= mx:
        raise b.BridgeError("That contract step is already done.")
    # the same call the game makes when you make progress: add exactly what's missing
    b.call_on(g, c, comp, "ServerAddProgressToContractCondition", [uid, idx, mx - p])
    return True


def finish(g, c, node):
    if node.startswith("contract:"):
        _, uid, idx = node.split(":")
        return finish_contract(g, c, int(uid), int(idx))
    comp = journey_component(g, c)
    if not can_complete_many(g, c, comp, [node])[0]:
        raise b.BridgeError("That step can't be completed right now.")
    b.call_on(g, c, comp, "CompleteStoryNodeByName", [node])
    return True
