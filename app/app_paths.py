"""Where DuneShark keeps its files: next to DuneShark.exe in the portable build, next to the source otherwise.
Settings, profiles, kits, logs and data files all live in this one folder, so the app stays portable."""
import os
import sys

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
