"""Global shortcuts for DuneShark (work while the game has focus).

Edit hotkeys.json (next to this file) to change them; DuneShark reads it at start. Combos use Ctrl / Alt / Shift / Win
plus a key: letters, digits, F1-F24, PageUp, PageDown, Home, End, Insert, Delete, Up, Down, Left, Right, Num0-Num9.
Controller players: map a button to the same combo in reWASD (free version is enough) - see REWASD_GUIDE.md.
Story/side "finish step" shortcuts need a second press within 3 seconds, like the button's "Sure?".
"""
import ctypes, ctypes.wintypes as w, json, os, queue, threading, time
import app_paths

HERE = app_paths.APP_DIR
CONFIG = os.path.join(HERE, "hotkeys.json")
LOG = os.path.join(HERE, "hotkeys_log.txt")

DEFAULTS = {
    "Ctrl+Alt+B": "instant_build",
    "Ctrl+Alt+P": "infinite_power",
    "Ctrl+Alt+PageUp": "suspensor_faster",
    "Ctrl+Alt+PageDown": "suspensor_slower",
    "Ctrl+Alt+D": "daytime",
    "Ctrl+Alt+N": "night",
    "Ctrl+Alt+H": "refill_health",
    "Ctrl+Alt+W": "refill_water",
    "Ctrl+Alt+U": "unstuck",
    "Ctrl+Alt+G": "god_mode",
    "Ctrl+Alt+S": "finish_story_step",
    "Ctrl+Alt+X": "finish_side_step",
    "Ctrl+Alt+1": "kit:build",
    "Ctrl+Alt+2": "kit:survival",
    "Ctrl+Alt+Home": "show_duneshark",
    "Ctrl+Alt+C": "instant_cooldowns",
    "Ctrl+Alt+M": "solaris:100000",
}
ACTIONS_HELP = {
    "instant_build": "Instant build on/off", "infinite_power": "Infinite power on/off",
    "suspensor_faster": "Suspensor speed +0.5", "suspensor_slower": "Suspensor speed -0.5",
    "daytime": "Time 10:00", "night": "Time 22:00", "refill_health": "Refill health", "refill_water": "Refill water",
    "unstuck": "Unstuck (nearest safe spot)", "god_mode": "God mode on/off",
    "finish_story_step": "Finish story step (press twice)", "finish_side_step": "Finish side step (press twice)",
    "kit:<name>": "Spawn a kit from kits.json (e.g. kit:build, kit:survival)",
    "show_duneshark": "Show DuneShark on top of the game / hide it again",
    "instant_cooldowns": "Instant ability cooldowns on/off",
    "solaris:<amount>": "Grant Solaris, e.g. solaris:100000",
    "profile:<key>": "Switch to a saved profile (profiles.json), e.g. profile:combat",
}


def toggle_window(title="DUNESHARK - Tales of the Dark Hulud"):
    """Show DuneShark on top of a fullscreen game, or send it back (minimized, not on top). The hotkey arrives
    in this process, which gives it the right to take the foreground from the game. Returns True if shown."""
    import ctypes
    u = ctypes.windll.user32
    h = ctypes.c_void_p(u.FindWindowW(None, title) or 0)
    if not h.value:
        return None
    topmost = bool(u.GetWindowLongW(h, -20) & 0x8)          # GWL_EXSTYLE & WS_EX_TOPMOST
    if topmost and not u.IsIconic(h):
        u.SetWindowPos(h, ctypes.c_void_p(-2), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)  # HWND_NOTOPMOST (64-bit handle!)
        u.ShowWindow(h, 6)                                   # SW_MINIMIZE -> the game gets focus back
        return False
    u.ShowWindow(h, 9)                                       # SW_RESTORE
    u.SetWindowPos(h, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x40)      # HWND_TOPMOST + SHOWWINDOW
    u.SetForegroundWindow(h)
    return True

MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
NAMED = {"pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27,
         "down": 0x28, "insert": 0x2D, "delete": 0x2E, "space": 0x20}


def parse(combo):
    mods, vk = 0x4000, None                                   # MOD_NOREPEAT
    for part in combo.replace(" ", "").split("+"):
        p = part.lower()
        if p in MODS:
            mods |= MODS[p]
        elif p in NAMED:
            vk = NAMED[p]
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
        elif p.startswith("f") and p[1:].isdigit() and 1 <= int(p[1:]) <= 24:
            vk = 0x6F + int(p[1:])
        elif p.startswith("num") and p[3:].isdigit():
            vk = 0x60 + int(p[3:])
    if vk is None:
        raise ValueError("Unknown key in " + combo)
    return mods, vk


def load_config():
    if not os.path.exists(CONFIG):
        json.dump({"_help": "Change the key combos on the left. Actions: " + ", ".join(ACTIONS_HELP),
                   "hotkeys": DEFAULTS}, open(CONFIG, "w", encoding="utf-8"), indent=2)
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    return cfg.get("hotkeys", DEFAULTS)


def log(msg):
    try:
        open(LOG, "a", encoding="utf-8").write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- small on-screen notice (game-style toast)
class Toast:
    def __init__(self):
        self.q = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def show(self, text, good=True):
        self.q.put((text, good))

    def _run(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.92)
        win.configure(bg="#14161c")
        label = tk.Label(win, text="", font=("Bahnschrift SemiLight", 16), fg="#f0cf8e", bg="#14161c", padx=26, pady=12)
        label.pack()
        win.withdraw()
        state = {"hide": 0}

        def poll():
            try:
                while True:
                    text, good = self.q.get_nowait()
                    label.configure(text=text.upper(), fg="#f0cf8e" if good else "#ff7a6a")
                    win.update_idletasks()
                    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
                    ww = label.winfo_reqwidth()
                    win.geometry("+%d+%d" % ((sw - ww) // 2, int(sh * 0.80)))
                    win.deiconify()
                    win.lift()
                    state["hide"] = time.time() + 1.8
            except queue.Empty:
                pass
            if state["hide"] and time.time() > state["hide"]:
                win.withdraw()
                state["hide"] = 0
            root.after(80, poll)

        root.after(80, poll)
        root.mainloop()


# ---------------------------------------------------------------- actions (call the same code as the DuneShark buttons)
class Actions:
    def __init__(self, server, toast):
        self.s, self.toast = server, toast
        self.armed = {}

    def _twice(self, name, label):
        now = time.time()
        if now - self.armed.get(name, 0) > 3:
            self.armed[name] = now
            self.toast.show(label + " - press again to confirm")
            return False
        self.armed.pop(name, None)
        return True

    def run(self, name):
        s, t = self.s, self.toast
        if name == "instant_build":
            on = not s.building_status()["instant"]
            s.building_apply({"instant": on})
            t.show("Instant build " + ("on" if on else "off"))
        elif name == "infinite_power":
            on = not s.keep_status()["power"]
            s.keep_set({"power": on})
            t.show("Infinite power " + ("on" if on else "off"))
        elif name in ("suspensor_faster", "suspensor_slower"):
            cur = s.move_status()["suspensor"]
            new = s.move_set({"suspensor": cur + (0.5 if name == "suspensor_faster" else -0.5)})["suspensor"]
            t.show("Suspensor speed x%g" % new)
        elif name in ("daytime", "night"):
            s.live("SetTimeOfDay", [10 if name == "daytime" else 22])
            t.show("Time " + ("10:00" if name == "daytime" else "22:00"))
        elif name in ("refill_health", "refill_water"):
            s.live("RefillHealth" if name == "refill_health" else "RefillWater", [])
            t.show(("Health" if name == "refill_health" else "Water") + " refilled")
        elif name == "unstuck":
            s.live("Unstuck", ["true", 0])
            t.show("Unstuck")
        elif name == "god_mode":
            st = s.load_settings()
            on = not st.get("god", True)
            s.live("SetGodMode", [1 if on else 0])
            with s._settings_lock:
                st = s.load_settings()
                st["god"] = on
                s.write_settings(st)
            t.show("God mode " + ("on" if on else "off"))
        elif name == "instant_cooldowns":
            on = not s.cooldowns_status()["on"]
            s.cooldowns_set(on)
            t.show("Instant cooldowns " + ("on" if on else "off"))
        elif name.startswith("profile:"):
            r = s.profile_load(name[8:])
            t.show("Profile: " + r["profile"], not r["notes"])
        elif name == "show_duneshark":
            shown = toggle_window()
            if shown is None:
                t.show("DuneShark window not found", False)
            elif not shown:
                t.show("DuneShark hidden - back to Arrakis")
        elif name.startswith("solaris:"):
            amount = max(1, min(int(name[8:]), 100000000))
            s.live("AddSolarisToAccount", [str(amount)])
            t.show("+%s Solaris" % format(amount, ","))
        elif name.startswith("kit:"):
            r = s.kit_spawn(name[4:])
            t.show(r["kit"] + " in your backpack", not r.get("failed"))
        elif name in ("finish_story_step", "finish_side_step"):
            kind = "story" if name == "finish_story_step" else "side"
            step = s.story_status().get(kind)
            if not step:
                t.show("Nothing to finish right now", False)
                return
            if not self._twice(name, "Finish: " + step["step"][:48]):
                return
            s.live("FinishStep", [step["node"]])
            t.show(("Story" if kind == "story" else "Side") + " step finished")
        else:
            t.show("Unknown action " + name, False)


def start(server):
    """Register the shortcuts and listen for them (own thread with a Windows message loop)."""
    toast = Toast()
    actions = Actions(server, toast)

    def loop():
        user32 = ctypes.windll.user32
        binds = {}
        for i, (combo, action) in enumerate(load_config().items(), start=1):
            try:
                mods, vk = parse(combo)
            except ValueError as e:
                log(str(e))
                continue
            if user32.RegisterHotKey(None, i, mods, vk):
                binds[i] = (combo, action)
            else:
                log("Could not register %s (another program already uses it)" % combo)
        log("registered: " + ", ".join("%s=%s" % v for v in binds.values()))
        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312 and msg.wParam in binds:   # WM_HOTKEY
                combo, action = binds[msg.wParam]
                threading.Thread(target=_safe_run, args=(actions, action, toast), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()


def _safe_run(actions, action, toast):
    try:
        actions.run(action)
    except Exception as e:                                       # game closed, menu open, ...
        toast.show(str(e)[:60] or "Didn't work", False)
        log("%s failed: %s" % (action, e))
