"""Builds the portable DuneShark: release\\DuneShark\\DuneShark.exe + data, zipped as release\\DuneShark_portable.zip.

Only app files and data go in. Personal state stays out: settings, profiles, custom kits, saved originals, logs.
Item icons: only the ones DuneShark uses, shrunk to 128 px (tester build).

Run:  python build_portable.py
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
REL = os.path.join(ROOT, "release")
DIST = os.path.join(REL, "DuneShark")
VERSION = "0.2.0"

DATA = ["index.html", "duneshark.ico", "duneshark.png", "items.json", "item_details.json", "augment_stats.json",
        "item_stats.json", "game_colors.json", "icon_lum.json", "journey_names.json"]
DOCS = [(os.path.join(REL, "README.md"), "README.md"), (os.path.join(REL, "TROUBLESHOOTING.md"), "TROUBLESHOOTING.md"),
        (os.path.join(ROOT, "REWASD_GUIDE.md"), "REWASD_GUIDE.md")]


def build_exe():
    work = os.path.join(REL, "_build")
    cmd = [sys.executable, "-m", "PyInstaller", "duneshark.pyw", "--name", "DuneShark", "--noconsole", "--onedir",
           "--icon", os.path.join(APP, "duneshark.ico"), "--noconfirm", "--clean",
           "--distpath", os.path.join(REL, "_dist"), "--workpath", work, "--specpath", work]
    subprocess.run(cmd, cwd=APP, check=True)
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    shutil.move(os.path.join(REL, "_dist", "DuneShark"), DIST)
    shutil.rmtree(os.path.join(REL, "_dist"), ignore_errors=True)


def copy_data():
    for f in DATA:
        shutil.copy2(os.path.join(APP, f), os.path.join(DIST, f))
    for src, name in DOCS:
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DIST, name))
    # kits: only the built-in ones, never the user's own ("custom") kits
    kits = json.load(open(os.path.join(APP, "kits.json"), encoding="utf-8"))
    kits["kits"] = {k: v for k, v in kits.get("kits", {}).items() if not v.get("custom")}
    json.dump(kits, open(os.path.join(DIST, "kits.json"), "w", encoding="utf-8"), indent=2)
    # hotkeys.json is created with the defaults on first start
    open(os.path.join(DIST, "VERSION.txt"), "w").write("DuneShark " + VERSION + "\nFor Dune: Awakening solo worlds (launch with -nobattleye)\n")


def copy_icons(size=128):
    """The item icons DuneShark uses (819 of the 6,554 exported), shrunk to 128 px, into DuneShark/icons/."""
    from PIL import Image
    src_root = os.path.join(ROOT, "icons_export", "DuneSandbox", "Content")
    dst_root = os.path.join(DIST, "icons", "DuneSandbox", "Content")
    items = json.load(open(os.path.join(APP, "items.json"), encoding="utf-8"))
    done = 0
    for ic in sorted({i.get("icon", "") for i in items}):
        if not ic.startswith("/Game/"):
            continue
        src = os.path.join(src_root, ic[6:] + ".png")
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dst_root, ic[6:] + ".png")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im = Image.open(src).convert("RGBA")
        im.thumbnail((size, size), Image.LANCZOS)
        im.save(dst, "PNG", optimize=True)
        done += 1
    print("icons:", done)


def zip_it():
    out = os.path.join(REL, "DuneShark_%s_portable.zip" % VERSION)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _, files in os.walk(DIST):
            for f in files:
                full = os.path.join(base, f)
                z.write(full, os.path.join("DuneShark", os.path.relpath(full, DIST)))
    return out


if __name__ == "__main__":
    build_exe()
    copy_data()
    copy_icons()
    print("zip:", zip_it())
