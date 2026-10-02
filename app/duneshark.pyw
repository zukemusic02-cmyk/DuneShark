"""DUNESHARK - Tales of the Dark Hulud. Opens the console in its own app window (no browser).

Borderless: no Windows title bar. The page draws its own minimize / maximize / close buttons and a
resize grip, and the header works as a drag area (class "pywebview-drag-region").
Window position and size are remembered in settings.json (same file as the UI size).
"""
import ctypes
import os
import sys
import threading


def unblock_own_files():
    """A downloaded zip marks every file as "from the internet" (Zone.Identifier stream), and .NET then refuses
    to load pythonnet's Python.Runtime.dll, so the window never opens. Remove the mark from our own folder."""
    if not getattr(sys, "frozen", False):
        return
    for base, _, files in os.walk(os.path.dirname(sys.executable)):
        for f in files:
            try:
                os.remove(os.path.join(base, f) + ":Zone.Identifier")
            except OSError:
                pass


unblock_own_files()
import webview
from http.server import ThreadingHTTPServer
import server


class WindowApi:
    """Called from the page as window.pywebview.api.<name>(). Private (_) attributes are not exposed to the page."""
    def __init__(self):
        self._win = None
        self._maxed = False

    def minimize(self):
        self._win.minimize()

    def toggle_maximize(self):
        if self._maxed:
            self._win.restore()
        else:
            self._win.maximize()
        self._maxed = not self._maxed
        server.save_settings({"maxed": self._maxed})
        return self._maxed

    def close(self):
        self._win.destroy()

    def size(self):
        return [self._win.width, self._win.height]

    def resize(self, w, h):
        self._win.resize(max(480, int(w)), max(360, int(h)))


# ---- remember window position / size (saved shortly after each move or resize) ----
_pending = {}
_timer = None


def _remember(**kw):
    global _timer
    if api._maxed:
        return                        # keep the normal geometry, not the maximized one
    # Windows parks minimized windows at about -32000/-16000 with a tiny size: never save that
    if any(v < -10000 for k, v in kw.items() if k in ("x", "y")) or kw.get("w", 9999) < 480 or kw.get("h", 9999) < 360:
        return
    _pending.update(kw)
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(0.6, lambda: server.save_settings(dict(_pending)))
    _timer.start()


s = server.load_settings()
geo = {"width": max(480, int(s.get("w", 1400))), "height": max(360, int(s.get("h", 900)))}
if -4000 < s.get("x", -9999) < 10000 and -4000 < s.get("y", -9999) < 10000:
    geo.update(x=int(s["x"]), y=int(s["y"]))

httpd = ThreadingHTTPServer(("127.0.0.1", server.PORT), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
try:
    import dune_hotkeys
    dune_hotkeys.start(server)          # global shortcuts (hotkeys.json) + on-screen notices
except Exception as e:                  # shortcuts are optional; the app still works without them
    open(server.os.path.join(server.HERE, "hotkeys_log.txt"), "a").write("hotkeys disabled: %s\n" % e)
# own taskbar identity, so Windows shows DuneShark's icon instead of Python's
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DuneShark.TalesOfTheDarkHulud")
except Exception:
    pass


def _set_icon():
    """Put duneshark.ico on the window (title/taskbar/Alt+Tab)."""
    try:
        u = ctypes.windll.user32
        u.LoadImageW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        h = u.FindWindowW(None, "DUNESHARK - Tales of the Dark Hulud")
        ico = server.os.path.join(server.HERE, "duneshark.ico")
        for kind, size in ((1, 32), (0, 16)):                     # ICON_BIG, ICON_SMALL
            hicon = u.LoadImageW(None, ico, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h and hicon:
                u.SendMessageW(h, 0x80, kind, hicon)              # WM_SETICON
    except Exception:
        pass


api = WindowApi()
api._win = webview.create_window("DUNESHARK - Tales of the Dark Hulud", f"http://127.0.0.1:{server.PORT}",
                                 background_color="#0c0d11", frameless=True, easy_drag=False, js_api=api, **geo)
api._win.events.moved += lambda x, y: _remember(x=x, y=y)
api._win.events.resized += lambda w, h: _remember(w=w, h=h)
api._win.events.shown += _set_icon
if s.get("maxed"):
    api._win.events.shown += lambda: (api._win.maximize(), setattr(api, "_maxed", True))
webview.start()
httpd.shutdown()
