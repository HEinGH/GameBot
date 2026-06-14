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
CHARACTERS_DIR = CONFIG_DIR / "characters"
TEMPLATES_DIR = ROOT_DIR / "templates"
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

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
    "last_char_start": 1,
    "last_log_debug": False,
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
    """Parse a template reference: string or {"template":..., "threshold":..., "color_threshold":..., "reject_flip":...}.
    Returns (template_name: str, threshold: float).
    """
    if default is None:
        default = DEFAULT_TEMPLATE_THRESHOLD
    if not value:
        return None, default
    if isinstance(value, dict):
        name = value.get("template") or None
        thr = value.get("threshold", default)
        ct = value.get("color_threshold", 0.0)
        if ct > 0 and name:
            from recognition.template import _color_registry
            _color_registry[name] = ct
        if value.get("reject_flip") and name:
            from recognition.template import _flip_registry
            _flip_registry.add(name)
        return name, thr
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


CHARACTER_PROFILE_FIELDS = (
    "portrait_template", "skill_bar_template",
    "result_screen_template", "avatar_template",
)


def load_character_profile(name):
    path = CHARACTERS_DIR / f"{name}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_character_profile(name, data):
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    path = CHARACTERS_DIR / f"{name}.json"
    data["name"] = name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def list_character_profiles():
    if not CHARACTERS_DIR.exists():
        return []
    return sorted(p.stem for p in CHARACTERS_DIR.glob("*.json") if not p.stem.startswith("_"))


def resolve_characters(preset):
    chars = []
    for ref in preset.get("characters", []):
        if isinstance(ref, str):
            name = ref
            overrides = {}
        else:
            name = ref.get("name", "")
            overrides = dict(ref)
            overrides.pop("name", None)
        profile = load_character_profile(name)
        if profile:
            merged = dict(profile)
            merged.update(overrides)
            chars.append(merged)
        elif any(k in overrides for k in CHARACTER_PROFILE_FIELDS):
            chars.append(dict(ref))
        else:
            ref_copy = dict(ref) if isinstance(ref, dict) else {"name": ref}
            chars.append(ref_copy)
    return chars


def serialize_characters(chars, profile_map=None):
    if profile_map is None:
        profile_map = {}
        for ch in chars:
            name = ch.get("name", "")
            if name:
                profile_map[name] = load_character_profile(name)
    result = []
    for ch in chars:
        name = ch.get("name", "")
        profile = profile_map.get(name)
        if profile:
            ref = {"name": name}
            for k in ("runs", "combos", "fallback_combos"):
                if k in ch:
                    val = ch[k]
                    ref[k] = val
            for k in CHARACTER_PROFILE_FIELDS:
                if k in ch and ch[k] != profile.get(k):
                    ref[k] = ch[k]
            result.append(ref)
        else:
            result.append(dict(ch))
    return result
