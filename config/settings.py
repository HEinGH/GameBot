import json
import os
import sys
import threading
from pathlib import Path


def get_resource_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def get_writable_dir():
    if getattr(sys, 'frozen', False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent.parent


ROOT_DIR = get_resource_dir()
CONFIG_DIR = ROOT_DIR / "config"
PRESETS_DIR = CONFIG_DIR / "presets"
TEMPLATES_DIR = ROOT_DIR / "templates"

DEFAULT_SETTINGS = {
    "resolution": [1920, 1080],
    "capture_method": "auto",
    "fps_limit": 30,
    "stuck_threshold_sec": 15,
    "ssim_threshold": 0.95,
    "combo_randomness": 0.2,
    "mouse_bezier_steps": 20,
    "click_jitter_px": 5,
    "blue_hsv_lower": [100, 140, 140],
    "blue_hsv_upper": [130, 255, 255],
    "npc_template": "npc_icon.png",
    "min_npc_match_count": 15,
    "line_roi_ratio": [0.3, 0.5, 0.8, 1.0],
    "debug_screenshot": False,
    "last_preset": "",
    "last_char_count": 1,
    "last_stealth": False,
    "last_background": False,
}


class Settings:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._data = dict(DEFAULT_SETTINGS)

    def __getattr__(self, name):
        if name.startswith("_"):
            return super().__getattribute__(name)
        return self._data.get(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def load(self, path=None):
        if path is None:
            path = CONFIG_DIR / "settings.json"
        path = Path(path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._data.update(data)

    def save(self, path=None):
        if path is None:
            path = get_writable_dir() / "config" / "settings.json"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)


PRESETS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TEMPLATE_THRESHOLD = 0.65


def parse_template_ref(value, default=None):
    """Parse a template reference which can be a string or {"template": ..., "threshold": ...} dict.
    Returns (template_name: str, threshold: float). Returns (None, default) if value is empty/None.
    """
    if default is None:
        default = DEFAULT_TEMPLATE_THRESHOLD
    if not value:
        return None, default
    if isinstance(value, dict):
        return value.get("template") or None, value.get("threshold", default)
    return str(value), default


def parse_template_chain(chain_list, default=None):
    """Parse a list of template references (strings or dicts).
    Returns list of (template_name, threshold) tuples.
    """
    if default is None:
        default = DEFAULT_TEMPLATE_THRESHOLD
    if not chain_list:
        return []
    result = []
    for item in chain_list:
        if isinstance(item, dict):
            name = item.get("template")
            thr = item.get("threshold", default)
        else:
            name = str(item) if item else None
            thr = default
        if name:
            result.append((name, thr))
    return result
