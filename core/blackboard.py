import threading


class Blackboard:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "running": True,
            "paused": False,
            "current_frame": None,
            "frame_timestamp": 0.0,
            "stamina_remaining": 999,
            "current_character_index": 0,
            "total_characters": 1,
            "preset_name": None,
            "preset": None,
            "stuck": False,
            "stuck_count": 0,
            "combat_phase": "idle",
            "domain_run_count": 0,
            "state_start_time": 0.0,
        }

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._data[key] = value

    def update(self, mapping):
        with self._lock:
            self._data.update(mapping)

    def keys(self):
        with self._lock:
            return list(self._data.keys())

    def elapsed_in_state(self):
        import time
        return time.time() - self["state_start_time"]
