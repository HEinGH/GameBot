"""One-time migration: extract characters from presets into config/characters/."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import PRESETS_DIR, CHARACTERS_DIR, CHARACTER_PROFILE_FIELDS
from config.settings import save_character_profile, serialize_characters

CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)


def collect_all_characters():
    profiles = {}
    for preset_path in sorted(PRESETS_DIR.glob("*.json")):
        with open(preset_path, encoding="utf-8") as f:
            data = json.load(f)
        for ch in data.get("characters", []):
            name = ch.get("name", "")
            if not name:
                continue
            existing = profiles.get(name)
            if existing is None:
                profiles[name] = dict(ch)
            else:
                for k in CHARACTER_PROFILE_FIELDS:
                    if not existing.get(k) and ch.get(k):
                        existing[k] = ch[k]
    return profiles


def main():
    profiles = collect_all_characters()
    print(f"Found {len(profiles)} unique characters across presets:")
    for name, prof in profiles.items():
        fields = {k: prof.get(k) for k in CHARACTER_PROFILE_FIELDS}
        filled = sum(1 for v in fields.values() if v)
        print(f"  {name}: {filled}/4 template fields")

    for name, prof in profiles.items():
        profile_data = {"name": name}
        for k in CHARACTER_PROFILE_FIELDS:
            if prof.get(k):
                profile_data[k] = prof[k]
        save_character_profile(name, profile_data)
        print(f"Created config/characters/{name}.json")

    for preset_path in sorted(PRESETS_DIR.glob("*.json")):
        with open(preset_path, encoding="utf-8") as f:
            data = json.load(f)
        chars = data.get("characters", [])
        profile_map = {ch.get("name"): ch for ch in chars}
        new_chars = serialize_characters(chars)
        data["characters"] = new_chars
        with open(preset_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Rewrote {preset_path.name} with {len(new_chars)} character references")

    print("\nMigration complete. Character library created, presets updated to reference format.")


if __name__ == "__main__":
    main()
