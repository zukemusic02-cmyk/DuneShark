"""DuneShark live bridge - runs Dune's built-in DuneCheatManager commands in a SOLO world.

Same method as the Prometheu5 Studios CT (v1.5.2, verified on game build 1.5.3.4), rebuilt in
Python so DuneShark doesn't need Cheat Engine or UE4SS:
  1. find GNames / GObjects / GWorld by byte pattern, walk World -> GameInstance -> LocalPlayer -> PC
  2. clone the character's vtable, point its ProcessEvent slot (0x2A8) at a small stub
  3. the next time the game calls ProcessEvent on the character (game thread, every frame) the stub
     restores the original vtable, runs our calls through the real UObject::ProcessEvent, then forwards
  4. calls: Conv_StringToName for FName args -> GameplayStatics.SpawnObject(DuneCheatManager, PC)
     -> DuneCheatManager.<Command>(args)

Usage:  python dune_bridge.py info
        python dune_bridge.py call SetTimeOfDay 22
        python dune_bridge.py call AddItemToInventory CopperBar 10 1 0
"""
import ctypes, ctypes.wintypes as w, json, os, re, struct, sys, time
import app_paths

EXE = "DuneSandbox-Win64-Shipping.exe"
BUILDS = {  # known game builds, by PE timestamp - offsets from the CT
    0x6AB3D64E: {"version": "1.5.3.4",
                 "actorEvent": 0x71C6C30, "objectEvent": 0x5666DF0, "gameThread": 0xC7B1890,
                 "componentEvent": 0x73AB9A0},   # UActorComponent::ProcessEvent (CT checks the same address)
}
# Builds DuneShark hasn't seen: the ProcessEvent addresses are learned from the live vtables (only real-looking
# functions accepted) and remembered here; the game thread is the process's main thread.
LEARNED_FILE = os.path.join(app_paths.APP_DIR, "game_builds.json")
ACTOR_PE_START = bytes([0x48, 0x89, 0x74, 0x24, 0x18, 0x48, 0x89, 0x7C, 0x24, 0x20, 0x41, 0x56])


def _learned():
    try:
        return json.load(open(LEARNED_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember(ts, build):
    data = _learned()
    data["%08X" % ts] = build
    try:
        json.dump(data, open(LEARNED_FILE, "w", encoding="utf-8"), indent=2)
    except OSError:
        pass
PE_SLOT = 0x2A8           # ProcessEvent vtable slot in this game (stock 5.2: 0x268)
CDO_OFF = 0x128           # UClass::ClassDefaultObject in this game (stock 5.2: 0x110)

SIGS = {
    "GWorld":   [("48 8B 1D ?? ?? ?? ?? 48 85 DB 74 ?? 41 B0 01", 3),
                 ("48 8B 15 ?? ?? ?? ?? 48 39 82 F0 03 00 00 75", 3)],
    "GObjects": [("48 8B 05 ?? ?? ?? ?? 48 8B 0C C8 4C 8D 04 D1", 3),
                 ("48 8B 05 ?? ?? ?? ?? 48 8B 0C C8 48 8D 04 D1", 3)],
    "GNames":   [("4C 8D 05 ?? ?? ?? ?? 48 89 B4 24 F0 00 00 00 0F", 3),
                 ("4C 8D 05 ?? ?? ?? ?? 4C 3B 75 ?? 0F", 3),
                 ("48 8D 05 ?? ?? ?? ?? C3 CC CC CC CC CC CC CC CC 40 53 48 83 EC 20 80 3D", 3)],
}

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = w.HANDLE
k32.VirtualAllocEx.restype = ctypes.c_uint64
k32.VirtualAllocEx.argtypes = [w.HANDLE, ctypes.c_uint64, ctypes.c_size_t, w.DWORD, w.DWORD]
k32.ReadProcessMemory.argtypes = [w.HANDLE, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [w.HANDLE, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.CreateToolhelp32Snapshot.restype = w.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
for _f in ("Module32FirstW", "Module32NextW", "Process32FirstW", "Process32NextW", "Thread32First", "Thread32Next"):
    getattr(k32, _f).argtypes = [w.HANDLE, ctypes.c_void_p]
k32.CloseHandle.argtypes = [w.HANDLE]
k32.OpenThread.restype = w.HANDLE
k32.GetThreadTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
INVALID_HANDLE = ctypes.c_void_p(-1).value


class BridgeError(Exception):
    pass


def snapshot(flags, pid=0, tries=10):
    """Toolhelp snapshot; retries while the game is still loading modules (it fails then, handle = -1)."""
    for _ in range(tries):
        snap = k32.CreateToolhelp32Snapshot(flags, pid)
        if snap and snap != INVALID_HANDLE:
            return snap
        time.sleep(0.1)
    raise BridgeError("Windows could not list the game's modules yet - try again in a moment.")


def valid(p):
    return isinstance(p, int) and 0x10000 < p < 0x7FFFFFFFFFFF


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("th32ModuleID", w.DWORD), ("th32ProcessID", w.DWORD),
                ("GlblcntUsage", w.DWORD), ("ProccntUsage", w.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", w.DWORD), ("hModule", w.HMODULE), ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                ("th32OwnerProcessID", w.DWORD), ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", w.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


BATTLEYE_EXE = "DuneSandbox_BE.exe"   # only runs when the game was started WITH BattlEye (no -nobattleye)


def find_pid(exe=EXE):
    snap = snapshot(0x2)
    e = PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    pid = None
    while ok:
        if e.szExeFile.lower() == exe.lower():
            pid = e.th32ProcessID
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return pid


class Game:
    def __init__(self):
        self.pid = find_pid()
        if not self.pid:
            raise BridgeError("Dune is not running.")
        if find_pid(BATTLEYE_EXE):
            raise BridgeError("BattlEye is running, so DuneShark stays off. Close the game, add -nobattleye to its "
                              "Steam launch options and start it again.")
        self.h = k32.OpenProcess(0x1F0FFF, False, self.pid)
        if not self.h:
            raise BridgeError("Windows blocked access to the game. Run DuneShark and Dune the same way "
                              "(both normally, or both as administrator).")
        snap = snapshot(0x8 | 0x10, self.pid)
        m = MODULEENTRY32W(); m.dwSize = ctypes.sizeof(m)
        ok = k32.Module32FirstW(snap, ctypes.byref(m))
        self.base = None
        while ok:
            if m.szModule.lower() == EXE.lower():
                self.base, self.size = m.modBaseAddr, m.modBaseSize
                break
            ok = k32.Module32NextW(snap, ctypes.byref(m))
        k32.CloseHandle(snap)
        if not self.base:
            raise BridgeError("Game module not found.")
        pe = self.base + self.u32(self.base + 0x3C)
        self.ts = self.u32(pe + 8)
        self.known = self.ts in BUILDS
        self.build = dict(BUILDS.get(self.ts) or _learned().get("%08X" % self.ts) or {})
        self.names = {}

    # ---------------------------------------------------------------- build-specific addresses
    def real_function(self, a):
        """True for an address inside the game's code that starts a real function (not a 'return 0' stub)."""
        if not any(va <= a < va + size for va, size in self.code_sections()):
            return False
        code = self.read(a, 0x20)
        return bool(code) and code[0] not in (0xC2, 0xC3, 0xCC, 0xE9) and b"\xC3\xCC" not in code

    def ev(self, kind, seen):
        """The expected ProcessEvent `kind` (objectEvent / actorEvent / componentEvent) for this game build.
        Unknown build: the first real function seen in that vtable slot is learned and remembered."""
        rva = self.build.get(kind)
        if rva is not None:
            return self.base + rva
        if not self.real_function(seen):
            raise BridgeError("This game version isn't supported yet (%s check failed) - nothing was changed."
                              % kind)
        self.build[kind] = seen - self.base
        self.build.setdefault("version", "learned")
        _remember(self.ts, self.build)
        return seen

    def main_thread(self):
        """The game's main thread (Unreal runs the game loop on it): the process's oldest thread."""
        snap = snapshot(0x4)
        e = THREADENTRY32(); e.dwSize = ctypes.sizeof(e)
        best = None
        ok = k32.Thread32First(snap, ctypes.byref(e))
        while ok:
            if e.th32OwnerProcessID == self.pid:
                h = k32.OpenThread(0x0800, False, e.th32ThreadID)   # THREAD_QUERY_LIMITED_INFORMATION
                if h:
                    t = [w.FILETIME() for _ in range(4)]
                    if k32.GetThreadTimes(h, *[ctypes.byref(x) for x in t]):
                        created = (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
                        if best is None or created < best[0]:
                            best = (created, e.th32ThreadID)
                    k32.CloseHandle(h)
            ok = k32.Thread32Next(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        return best[1] if best else 0

    def game_thread(self):
        if "gameThread" in self.build:
            return self.u32(self.base + self.build["gameThread"])
        if not getattr(self, "_tid", 0):
            self._tid = self.main_thread()
        return self._tid

    # ---------------------------------------------------------------- memory
    def read(self, a, n):
        buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, a, buf, n, ctypes.byref(got)) or got.value != n:
            return None
        return buf.raw

    def write(self, a, data):
        got = ctypes.c_size_t()
        if not k32.WriteProcessMemory(self.h, a, data, len(data), ctypes.byref(got)) or got.value != len(data):
            raise BridgeError("Memory write failed at %X" % a)

    def u64(self, a):
        b = self.read(a, 8); return struct.unpack("<Q", b)[0] if b else 0

    def u32(self, a):
        b = self.read(a, 4); return struct.unpack("<I", b)[0] if b else 0

    def i32(self, a):
        b = self.read(a, 4); return struct.unpack("<i", b)[0] if b else 0

    def alloc(self, n):
        a = k32.VirtualAllocEx(self.h, 0, n, 0x3000, 0x40)  # MEM_COMMIT|RESERVE, PAGE_EXECUTE_READWRITE
        if not a:
            raise BridgeError("Could not allocate memory in the game.")
        return a

    # ---------------------------------------------------------------- scanning
    def code_sections(self):
        pe = self.base + self.u32(self.base + 0x3C)
        nsec = struct.unpack("<H", self.read(pe + 6, 2))[0]
        opt = struct.unpack("<H", self.read(pe + 20, 2))[0]
        for i in range(nsec):
            s = self.read(pe + 24 + opt + 40 * i, 40)
            vsize, va = struct.unpack_from("<II", s, 8)
            chars = struct.unpack_from("<I", s, 36)[0]
            if chars & 0x20000000:  # executable
                yield self.base + va, vsize

    def scan(self, sigs, check):
        if not hasattr(self, "_code"):
            self._code = []
            for va, size in self.code_sections():
                data = bytearray()
                for off in range(0, size, 0x100000):
                    n = min(0x100000, size - off)
                    data += self.read(va + off, n) or b"\0" * n
                self._code.append((va, bytes(data)))
        for pat, disp in sigs:
            rx = re.compile(b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in pat.split()), re.S)
            for va, data in self._code:
                for m in rx.finditer(data):
                    a = va + m.start()
                    d = struct.unpack_from("<i", data, m.start() + disp)[0]
                    t = a + disp + 4 + d
                    if check(t):
                        return t
        return None

    # ---------------------------------------------------------------- names / reflection
    def name(self, ci):
        if ci in self.names:
            return self.names[ci]
        blk = self.u64(self.GNames + (ci >> 16) * 8)
        if not valid(blk):
            return None
        e = blk + (ci & 0xFFFF) * 2
        hdr = struct.unpack("<H", self.read(e, 2) or b"\0\0")[0]
        ln, wide = hdr >> 6, hdr & 1
        if ln <= 0 or ln > 1024:
            return None
        raw = self.read(e + 2, ln * 2 if wide else ln)
        s = raw.decode("utf-16le" if wide else "latin-1", "replace") if raw else None
        self.names[ci] = s
        return s

    def fname(self, a):
        b = self.read(a, 8)
        if not b:
            return None
        ci, num = struct.unpack("<II", b)
        s = self.name(ci)
        if s and num:
            s += "_%d" % (num - 1)
        return s

    def obj_name(self, o):
        return self.fname(o + 0x18) if valid(o) else None

    def cls(self, o):
        c = self.u64(o + 0x10) if valid(o) else 0
        return c if valid(c) else None

    def class_name(self, o):
        return self.obj_name(self.cls(o))

    def find_prop_in(self, struct_, pname):
        s, depth = struct_, 0
        while valid(s) and depth < 64:
            f, n = self.u64(s + 0x50), 0
            while valid(f) and n < 5000:
                if self.fname(f + 0x28) == pname:
                    return {"off": self.i32(f + 0x4C), "size": self.i32(f + 0x3C),
                            "type": self.fname(self.u64(f + 0x08))}
                f, n = self.u64(f + 0x20), n + 1
            s, depth = self.u64(s + 0x40), depth + 1
        return None

    def params(self, fn):
        out, f, n = [], self.u64(fn + 0x50), 0
        while valid(f) and n < 64:
            flags = self.u64(f + 0x40)
            out.append({"name": self.fname(f + 0x28), "type": self.fname(self.u64(f + 0x08)),
                        "off": self.i32(f + 0x4C), "size": self.i32(f + 0x3C),
                        "ret": bool(flags & 0x400)})   # CPF_ReturnParm
            f, n = self.u64(f + 0x20), n + 1
        return out

    def ptr(self, o, pname):
        p = self.find_prop_in(self.cls(o), pname)
        if not p:
            return None
        v = self.u64(o + p["off"])
        return v if valid(v) else None

    def find_function(self, obj, name):
        cache = self.__dict__.setdefault("_fn", {})
        f = cache.get((obj, name))
        if f and self.obj_name(f) == name:
            return f
        f = cache[(obj, name)] = self._find_function(obj, name)
        return f

    def _find_function(self, obj, name):
        c = self.cls(obj)
        for _ in range(64):
            if not valid(c):
                break
            f = self.u64(c + 0x48)
            for _ in range(5000):
                if not valid(f):
                    break
                if self.obj_name(f) == name:
                    return f
                f = self.u64(f + 0x28)
            c = self.u64(c + 0x40)
        raise BridgeError("Function not found: " + name)

    def find_objects(self, wanted):
        """wanted = {(name, className): None}; one pass over GObjects."""
        chunks = self.u64(self.GObjects)
        count = self.i32(self.GObjects + 0x14)
        if not valid(chunks) or not 1000 < count < 3000000:
            raise BridgeError("Object list is not ready.")
        want_names = {n for n, _ in wanted}
        for ci in range((count + 65535) // 65536):
            chunk = self.u64(chunks + ci * 8)
            n = min(65536, count - ci * 65536)
            raw = self.read(chunk, n * 24) if valid(chunk) else None
            if not raw:
                continue
            for i in range(n):
                obj = struct.unpack_from("<Q", raw, i * 24)[0]
                if not valid(obj):
                    continue
                hdr = self.read(obj + 0x10, 0x10)
                if not hdr:
                    continue
                cls_, nci, nnum = struct.unpack("<QII", hdr)
                nm = self.name(nci)
                if nm is None or nnum or nm not in want_names:
                    continue
                cn = self.obj_name(cls_)
                if (nm, cn) in wanted and wanted[(nm, cn)] is None:
                    wanted[(nm, cn)] = obj
                    if all(v is not None for v in wanted.values()):
                        return wanted
        missing = [k for k, v in wanted.items() if v is None]
        if missing:
            raise BridgeError("Not loaded yet: %s" % missing)
        return wanted

    # ---------------------------------------------------------------- context
    def attach(self):
        def gnames_ok(t):
            b0 = self.u64(t)
            return valid(b0) and self.read(b0 + 2, 4) == b"None"
        self.GNames = self.scan(SIGS["GNames"], gnames_ok)
        if not self.GNames:
            raise BridgeError("GNames not found.")

        def gobj_ok(t):
            m, n = self.i32(t + 0x10), self.i32(t + 0x14)
            return n > 1000 and n <= m
        self.GObjects = self.scan(SIGS["GObjects"], gobj_ok)

        def gworld_ok(t):
            wv = self.u64(t)
            return valid(wv) and self.class_name(wv) == "World"
        self.GWorld = self.scan(SIGS["GWorld"], gworld_ok)
        if not (self.GObjects and self.GWorld):
            raise BridgeError("GObjects/GWorld not found.")

    def ctx(self):
        c = {"world": self.u64(self.GWorld)}
        if not valid(c["world"]):
            raise BridgeError("No world loaded.")
        gi = self.ptr(c["world"], "OwningGameInstance")
        p = self.find_prop_in(self.cls(gi), "LocalPlayers") if gi else None
        arr = self.u64(gi + p["off"]) if p else 0
        lp = self.u64(arr) if valid(arr) else 0
        if not valid(lp):
            raise BridgeError("Load your character first.")
        c["pc"] = self.ptr(lp, "PlayerController")
        if not c["pc"]:
            raise BridgeError("No PlayerController.")
        c["pawn"] = self.ptr(c["pc"], "AcknowledgedPawn") or self.ptr(c["pc"], "Pawn")
        inv = self.ptr(c["pc"], "m_InventoryCoordinator")
        c["char"] = (self.ptr(inv, "m_OwnerCharacter") if inv else None) or c["pawn"]
        c["gm"] = self.ptr(c["world"], "AuthorityGameMode")
        role = self.find_prop_in(self.cls(c["pc"]), "Role")
        c["role"] = (self.read(c["pc"] + role["off"], 1) or b"\0")[0] if role else None
        return c

    def require_solo(self, c):
        if not (c.get("char") and c.get("gm")):
            raise BridgeError("Load your own solo world first.")
        if c["role"] != 3:
            raise BridgeError("Not the authority (not solo).")
        if self.ptr(c["pc"], "NetConnection"):
            raise BridgeError("You're on an online server. DuneShark only works in your own solo world.")


# -------------------------------------------------------------------- x64 encoding helpers
def mov_imm(reg, v):
    enc = {"rax": b"\x48\xB8", "rcx": b"\x48\xB9", "rdx": b"\x48\xBA",
           "r8": b"\x49\xB8", "r9": b"\x49\xB9", "r10": b"\x49\xBA", "r11": b"\x49\xBB"}[reg]
    return enc + struct.pack("<Q", v)

CALL_RAX = b"\xFF\xD0"


def pe_call(pe, obj, fn, parms):
    """ProcessEvent(obj, fn, parms) with the real UObject::ProcessEvent."""
    return mov_imm("rcx", obj) + mov_imm("rdx", fn) + mov_imm("r8", parms) + mov_imm("rax", pe) + CALL_RAX


def run_command(g, name, args, ext=None):
    """ext = a CheatManagerExtension class (e.g. 'JourneyCheatManager'): created with the DuneCheatManager as its Outer."""
    c = g.ctx()
    g.require_solo(c)
    objs = getattr(g, "_objs", None)
    if not objs or any(g.obj_name(v) != k[0] for k, v in objs.items()):   # cache, re-validated each call
        objs = g._objs = g.find_objects({("DuneCheatManager", "Class"): None,
                                         ("Default__GameplayStatics", "GameplayStatics"): None,
                                         ("Default__KismetStringLibrary", "KismetStringLibrary"): None})
    mgr_cls = objs[("DuneCheatManager", "Class")]
    gameplay = objs[("Default__GameplayStatics", "GameplayStatics")]
    strlib = objs[("Default__KismetStringLibrary", "KismetStringLibrary")]
    mgr_cdo = g.u64(mgr_cls + CDO_OFF)
    ext_cls = None
    if ext:
        ext_cls = g.__dict__.setdefault("_ext", {}).get(ext)
        if not (ext_cls and g.obj_name(ext_cls) == ext):
            ext_cls = g._ext[ext] = g.find_objects({(ext, "Class"): None})[(ext, "Class")]
        fn = g.find_function(g.u64(ext_cls + CDO_OFF), name)
    else:
        fn = g.find_function(mgr_cdo, name)
    spawn = g.find_function(gameplay, "SpawnObject")
    conv = g.find_function(strlib, "Conv_StringToName")
    pe = g.u64(g.u64(gameplay) + PE_SLOT)
    if pe != g.ev("objectEvent", pe):
        raise BridgeError("ProcessEvent check failed - no action applied.")

    # ---- build the parameter block (same layout as the CT) ----
    mem = g.alloc(0x5000)
    code_at, vt, state, p = mem, mem + 0x1008, mem + 0x4000, mem + 0x4100
    buf = bytearray(0xE00)
    struct.pack_into("<QQ", buf, 0, mgr_cls, c["pc"])          # SpawnObject(ObjectClass, Outer) -> ret @0x10
    if ext_cls:
        struct.pack_into("<Q", buf, 0x40, ext_cls)              # 2nd SpawnObject(ext, Outer=[manager]) -> ret @0x50
    plist = [x for x in g.params(fn) if not x["ret"]]
    if len(args) < len(plist):
        raise BridgeError("%s needs %d args: %s" % (name, len(plist), ", ".join(x["name"] + ":" + str(x["type"]) for x in plist)))
    cursor, convslot, conversions = 0x800, 0x400, b""
    for spec, val in zip(plist, args):
        at = 0x100 + spec["off"]
        t = spec["type"]
        if t in ("StrProperty", "NameProperty"):
            s = str(val)
            if not re.fullmatch(r"[\w./ +-]+", s):
                raise BridgeError("Unsupported text: " + s)
            ws = s.encode("utf-16le") + b"\0\0"
            n = len(s) + 1
            buf[cursor:cursor + len(ws)] = ws
            if t == "StrProperty":
                struct.pack_into("<Qii", buf, at, p + cursor, n, n)
            else:
                struct.pack_into("<Qii", buf, convslot, p + cursor, n, n)
                conversions += pe_call(pe, strlib, conv, p + convslot)
                # mov r11,p ; mov rax,[r11+convslot+16] ; mov [r11+at],rax
                conversions += mov_imm("r11", p) + b"\x49\x8B\x83" + struct.pack("<i", convslot + 16) \
                    + b"\x49\x89\x83" + struct.pack("<i", at)
                convslot += 32
            cursor += len(ws)
        elif t == "FloatProperty":
            struct.pack_into("<f", buf, at, float(val))
        elif t == "DoubleProperty":
            struct.pack_into("<d", buf, at, float(val))
        elif t in ("BoolProperty", "ByteProperty", "EnumProperty") and spec["size"] == 1:
            v = str(val).lower()
            buf[at] = 1 if v in ("true", "on", "yes") else 0 if v in ("false", "off", "no") else int(val) & 0xFF
        elif t in ("IntProperty", "EnumProperty"):
            struct.pack_into("<i", buf, at, int(val))
        elif t == "UInt32Property":
            struct.pack_into("<I", buf, at, int(val))
        elif t == "Int64Property":
            struct.pack_into("<q", buf, at, int(val))
        else:
            raise BridgeError("Unsupported parameter type %s (%s)" % (t, spec["name"]))
    g.write(p, bytes(buf))

    # ---- the calls: conversions -> SpawnObject -> command -> done flag ----
    body = conversions + pe_call(pe, gameplay, spawn, p)
    target = 0x10
    if ext_cls:
        # mov r11,p ; mov rax,[r11+10h] ; mov [r11+48h],rax  (manager becomes the extension's Outer), then spawn it
        body += mov_imm("r11", p) + b"\x49\x8B\x43\x10" + b"\x49\x89\x43\x48" + pe_call(pe, gameplay, spawn, p + 0x40)
        target = 0x50
    tail = mov_imm("r11", p) + b"\x49\x8B\x4B" + bytes([target]) + b"\x48\x85\xC9"          # rcx=[p+0x10]; test rcx,rcx
    after_mgr = mov_imm("r10", pe) + b"\x48\x8B\x01" + b"\x4C\x39\x90" + struct.pack("<i", PE_SLOT)  # rax=[rcx]; cmp [rax+2A8],r10
    call_cmd = mov_imm("rdx", fn) + mov_imm("r8", p + 0x100) + mov_imm("rax", pe) + CALL_RAX \
        + mov_imm("r11", p) + b"\x41\xC6\x43\x18\x01"                           # mov byte [r11+18],1
    after_mgr += b"\x0F\x85" + struct.pack("<i", len(call_cmd)) + call_cmd       # jne adminDone
    tail += b"\x0F\x84" + struct.pack("<i", len(after_mgr)) + after_mgr          # jz adminDone
    calls = body + tail
    dispatch(g, c, calls)
    if (g.read(p + 0x18, 1) or b"\0")[0] != 1:
        raise BridgeError("The command could not start (manager check failed).")
    return True


def dispatch(g, c, calls, timeout=5):
    """Run raw x64 `calls` once on the game thread (vtable swap on the character, like CT E.gameCall)."""
    owner = c["char"]
    orig_vt = g.u64(owner)
    orig_pe = g.u64(orig_vt + PE_SLOT)
    if g.read(orig_pe, 12) != ACTOR_PE_START:          # checked before learning, so only the real one is kept
        raise BridgeError("Character code changed - no action applied.")
    if orig_pe != g.ev("actorEvent", orig_pe):
        raise BridgeError("Unexpected character code - no action applied.")
    tid = g.game_thread()
    if not tid:
        raise BridgeError("Game thread not ready.")
    mem = g.alloc(0x2100 + len(calls) + 0x200)
    vt, state, code_at = mem + 0x8, mem + 0x2010, mem + 0x2100
    g.write(vt - 8, g.read(orig_vt - 8, 0x2008))
    g.write(vt + PE_SLOT, struct.pack("<Q", code_at))
    g.write(state, b"\0" * 4)

    inner = (b"\x51\x52\x41\x50\x41\x51"                 # push rcx,rdx,r8,r9
             b"\x48\x83\xEC\x48"                          # sub rsp,48h
             b"\xF3\x0F\x7F\x4C\x24\x30"                  # movdqu [rsp+30h],xmm1
             + mov_imm("rax", orig_vt) + b"\x48\x89\x01"  # mov [rcx],rax  (restore vtable)
             + mov_imm("r11", state) + b"\x41\xC7\x03\x01\x00\x00\x00"
             + calls
             + mov_imm("r11", state) + b"\x41\xC7\x03\x02\x00\x00\x00"
             + b"\xF3\x0F\x6F\x4C\x24\x30"                # movdqu xmm1,[rsp+30h]
             b"\x48\x83\xC4\x48"                          # add rsp,48h
             b"\x41\x59\x41\x58\x5A\x59")                 # pop r9,r8,rdx,rcx
    head = b"\x65\x8B\x04\x25\x48\x00\x00\x00" + b"\x3D" + struct.pack("<I", tid)   # mov eax,gs:[48h]; cmp eax,tid
    head += b"\x0F\x85" + struct.pack("<i", len(inner))                              # jne forward
    code = head + inner + mov_imm("rax", orig_pe) + b"\xFF\xE0"                      # forward: jmp orig
    g.write(code_at, code)

    if g.u64(owner) != orig_vt:
        raise BridgeError("Character changed - try again.")
    g.write(owner, struct.pack("<Q", vt))
    deadline = time.time() + timeout
    while time.time() < deadline and g.u32(state) != 2:
        time.sleep(0.01)
    if g.u64(owner) == vt:                 # never fired: put the real vtable back
        g.write(owner, struct.pack("<Q", orig_vt))
    if g.u32(state) != 2:
        raise BridgeError("Timed out - close any menu and try again.")


def find_owned(g, owner, class_names):
    """First live object whose Outer is `owner` and whose class is one of `class_names` (cached)."""
    cache = g.__dict__.setdefault("_owned", {})
    key = (owner, tuple(class_names))
    o = cache.get(key)
    if o and g.u64(o + 0x20) == owner and g.class_name(o) in class_names:
        return o
    chunks, count = g.u64(g.GObjects), g.i32(g.GObjects + 0x14)
    for ci in range((count + 65535) // 65536):
        chunk = g.u64(chunks + ci * 8)
        n = min(65536, count - ci * 65536)
        raw = g.read(chunk, n * 24) if valid(chunk) else None
        if not raw:
            continue
        for i in range(n):
            o = struct.unpack_from("<Q", raw, i * 24)[0]
            if valid(o) and g.u64(o + 0x20) == owner and g.class_name(o) in class_names:
                cache[key] = o
                return o
    raise BridgeError("%s not found." % "/".join(class_names))


def call_on(g, c, obj, name, args=()):
    """Call UFunction `name` on any object, on the game thread. Supports Str/Name/Bool/Int/Float args;
    returns the raw parameter block (read return values from it with the offsets in g.params)."""
    fn = g.find_function(obj, name)
    pe = g.u64(g.u64(obj) + PE_SLOT)
    known = {g.base + g.build[k] for k in ("objectEvent", "actorEvent", "componentEvent") if k in g.build}
    if pe not in known and (g.known or not g.real_function(pe)):
        raise BridgeError("Unexpected ProcessEvent on %s - no action applied." % g.obj_name(obj))
    plist = [x for x in g.params(fn) if not x["ret"]]
    if len(args) != len(plist):
        raise BridgeError("%s needs %d args" % (name, len(plist)))
    p = g.alloc(0x1000)
    buf = bytearray(0x1000)
    cursor = 0x800
    for spec, val in zip(plist, args):
        at, t = spec["off"], spec["type"]
        if t == "StrProperty":
            ws = str(val).encode("utf-16le") + b"\0\0"
            if cursor + len(ws) > 0x1000:
                raise BridgeError("Text too long.")
            buf[cursor:cursor + len(ws)] = ws
            n = len(str(val)) + 1
            struct.pack_into("<Qii", buf, at, p + cursor, n, n)
            cursor += len(ws)
        elif t == "BoolProperty":
            buf[at] = 1 if str(val).lower() in ("1", "true", "on", "yes") else 0
        elif t in ("ByteProperty", "EnumProperty") and spec["size"] == 1:
            buf[at] = int(val) & 0xFF                                                   # small number (enum / byte)
        elif t == "IntProperty":
            struct.pack_into("<i", buf, at, int(val))
        elif t == "FloatProperty":
            struct.pack_into("<f", buf, at, float(val))
        elif t == "Int64Property" or (t == "StructProperty" and spec["size"] == 8):   # e.g. PersistenceItemId
            struct.pack_into("<q", buf, at, int(val))
        elif t == "StructProperty" and spec["size"] == 24 and isinstance(val, (tuple, list)) and len(val) == 3:
            struct.pack_into("<ddd", buf, at, *map(float, val))                         # FVector (UE5 doubles)
        elif t in ("ObjectProperty", "ClassProperty", "SoftClassProperty") and spec["size"] == 8:
            struct.pack_into("<Q", buf, at, int(val))                                   # object / class pointer
        elif t == "StructProperty" and spec["size"] == 16 and isinstance(val, (tuple, list)) and len(val) == 4:
            struct.pack_into("<ffff", buf, at, *map(float, val))                        # FLinearColor (r, g, b, a)
        else:
            raise BridgeError("Unsupported parameter type " + str(t))
    g.write(p, bytes(buf))
    dispatch(g, c, pe_call(pe, obj, fn, p))
    return {x["name"]: g.read(p + x["off"], max(1, x["size"])) for x in g.params(fn)}


def player_attribute(g, c, set_class, attr):
    """Current value of a GAS attribute on the player character, e.g.
    ('DuneCharacterAttributeSet', 'MaxHealth') or ('DuneHydrationAttributeSet', 'MaxHydration')."""
    cache = g.__dict__.setdefault("_attrsets", {})
    obj = cache.get(set_class)
    if not (obj and g.u64(obj + 0x20) == c["char"] and g.class_name(obj) == set_class):
        obj = None
        chunks, count = g.u64(g.GObjects), g.i32(g.GObjects + 0x14)
        for ci in range((count + 65535) // 65536):
            chunk = g.u64(chunks + ci * 8)
            n = min(65536, count - ci * 65536)
            raw = g.read(chunk, n * 24) if valid(chunk) else None
            if not raw:
                continue
            for i in range(n):
                o = struct.unpack_from("<Q", raw, i * 24)[0]
                if valid(o) and g.u64(o + 0x20) == c["char"] and g.class_name(o) == set_class:
                    obj = o
                    break
            if obj:
                break
        if not obj:
            raise BridgeError("Attribute set %s not found on the character." % set_class)
        cache[set_class] = obj
    p = g.find_prop_in(g.cls(obj), attr)
    if not p:
        raise BridgeError("Attribute %s not found." % attr)
    return struct.unpack("<f", g.read(obj + p["off"] + 0xC, 4))[0]   # FGameplayAttributeData.CurrentValue


def attribute_address(g, c, set_class, attr):
    """Address of FGameplayAttributeData.CurrentValue for a player attribute (cached per set + name)."""
    player_attribute(g, c, set_class, attr)                    # locates + validates + caches the set object
    obj = g._attrsets[set_class]
    cache = g.__dict__.setdefault("_attroff", {})
    key = (g.cls(obj), attr)
    if key not in cache:
        cache[key] = g.find_prop_in(g.cls(obj), attr)["off"]
    return obj + cache[key] + 0xC


def set_player_attribute(g, c, set_class, attr, value):
    """Write a GAS attribute's CurrentValue directly (used for health: SetPlayerHealth does nothing in this build)."""
    player_attribute(g, c, set_class, attr)                    # locates + caches the attribute set
    obj = g._attrsets[set_class]
    p = g.find_prop_in(g.cls(obj), attr)
    g.write(obj + p["off"] + 0xC, struct.pack("<f", float(value)))


def main(argv):
    g = Game()
    g.attach()
    if not argv or argv[0] == "info":
        c = g.ctx()
        print("base %X  GNames %X  GObjects %X  GWorld %X" % (g.base, g.GNames, g.GObjects, g.GWorld))
        for k in ("world", "pc", "pawn", "char", "gm"):
            v = c.get(k)
            print("%-5s %s  %s" % (k, "%X" % v if v else "-", g.class_name(v) if v else ""))
        print("role", c["role"])
        return
    if argv[0] == "call":
        run_command(g, argv[1], argv[2:])
        print("OK", argv[1], " ".join(argv[2:]))
    if argv[0] == "ext":                     # python dune_bridge.py ext JourneyCheatManager JourneyCompleteTrackedNode
        run_command(g, argv[2], argv[3:], ext=argv[1])
        print("OK", argv[1], argv[2], " ".join(argv[3:]))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BridgeError as e:
        print("ERROR:", e); sys.exit(1)
