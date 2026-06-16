import json
import os
import sys
import hashlib
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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
COMBO_DIR = ROOT_DIR / "combos"
TEMPLATES_DIR = ROOT_DIR / "templates"
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
COMBO_DIR.mkdir(parents=True, exist_ok=True)

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
    "last_log_debug": False,
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
            ct = item.get("color_threshold", 0.0)
            if ct > 0 and name:
                from recognition.template import _color_registry
                _color_registry[name] = ct
            if item.get("reject_flip") and name:
                from recognition.template import _flip_registry
                _flip_registry.add(name)
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


def list_combos():
    if not COMBO_DIR.exists():
        return []
    return sorted(p.stem for p in COMBO_DIR.glob("*.json") if not p.stem.startswith("_"))


def load_combo(name):
    path = COMBO_DIR / f"{name}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_combo(name, data):
    COMBO_DIR.mkdir(parents=True, exist_ok=True)
    path = COMBO_DIR / f"{name}.json"
    data["name"] = name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def delete_combo(name):
    path = COMBO_DIR / f"{name}.json"
    if path.exists():
        path.unlink()


def _combo_content_hash(actions):
    return hashlib.md5(json.dumps(actions, sort_keys=True).encode()).hexdigest()


def _find_combo_by_content(actions):
    target_hash = _combo_content_hash(actions)
    for name in list_combos():
        data = load_combo(name)
        if data and _combo_content_hash(data.get("actions", [])) == target_hash:
            return name
    return None


def _migrate_combo_to_file(actions, preferred_name, preset_name=""):
    if not actions:
        return None, False
    existing = _find_combo_by_content(actions)
    if existing:
        return existing, False
    name = preferred_name
    existing_data = load_combo(name)
    if existing_data is not None:
        if _combo_content_hash(existing_data.get("actions", [])) != _combo_content_hash(actions):
            if preset_name:
                name = f"{preferred_name}_{preset_name}"
            else:
                h = _combo_content_hash(actions)[:6]
                name = f"{preferred_name}_{h}"
    save_combo(name, {"name": name, "actions": list(actions)})
    return name, True


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


def resolve_characters(preset, preset_name=""):
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

    for ch in chars:
        inline_combos = ch.get("combos")
        if isinstance(inline_combos, list) and inline_combos:
            name, migrated = _migrate_combo_to_file(inline_combos, ch.get("name", "unknown"), preset_name)
            ch["combo"] = name
            ch["combos"] = inline_combos
            if migrated:
                logger.info("Migrated combo for '%s' → %s.json", ch.get("name", "?"), name)
        elif isinstance(ch.get("combo"), str) and ch["combo"]:
            data = load_combo(ch["combo"])
            if data:
                ch["combos"] = data.get("actions", [])
            else:
                ch["combos"] = []
        else:
            ch["combos"] = ch.get("combos", []) or []

        inline_fb = ch.get("fallback_combos")
        if isinstance(inline_fb, list) and inline_fb:
            fb_name, fb_migrated = _migrate_combo_to_file(inline_fb, ch.get("name", "unknown") + "_兜底", preset_name)
            ch["fallback_combo"] = fb_name
            ch["fallback_combos"] = inline_fb
            if fb_migrated:
                logger.info("Migrated fallback for '%s' → %s.json", ch.get("name", "?"), fb_name)
        elif isinstance(ch.get("fallback_combo"), str) and ch["fallback_combo"]:
            data = load_combo(ch["fallback_combo"])
            if data:
                ch["fallback_combos"] = data.get("actions", [])
            else:
                ch["fallback_combos"] = None
        else:
            ch["fallback_combos"] = ch.get("fallback_combos") or None

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
            for k in ("runs", "combo", "fallback_combo"):
                if k in ch:
                    val = ch[k]
                    if val is not None and val != "":
                        ref[k] = val
            for k in CHARACTER_PROFILE_FIELDS:
                if k in ch and ch[k] != profile.get(k):
                    ref[k] = ch[k]
            result.append(ref)
        else:
            out = dict(ch)
            out.pop("combos", None)
            out.pop("fallback_combos", None)
            result.append(out)
    return result


def migrate_preset_fallback(preset, preset_name=""):
    fb = preset.get("fallback_combos")
    if isinstance(fb, list) and fb:
        fb_name, migrated = _migrate_combo_to_file(fb, preset_name + "_兜底" if preset_name else "兜底", preset_name)
        preset["fallback_combo"] = fb_name
        if migrated:
            logger.info("Migrated preset fallback → %s.json", fb_name)
    elif isinstance(preset.get("fallback_combo"), str) and preset["fallback_combo"]:
        pass
    return preset
