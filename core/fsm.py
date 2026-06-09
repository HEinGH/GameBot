import time
import logging

logger = logging.getLogger(__name__)


STATE_CN = {
    "character_select": "\u89d2\u8272\u9009\u62e9",
    "town_nav": "\u57ce\u9547\u5bfc\u822a",
    "npc_navigate": "NPC\u5bfb\u8def",
    "domain_loading": "\u526f\u672c\u52a0\u8f7d\u4e2d",
    "domain_combat": "\u526f\u672c\u6218\u6597",
    "dungeon_exit_nav": "\u526f\u672c\u51fa\u53e3\u5bfb\u8def",
    "map_loading": "\u5730\u56fe\u52a0\u8f7d\u4e2d",
    "town_exit": "\u57ce\u9547\u9000\u51fa",
    "complete": "\u5168\u90e8\u5b8c\u6210",
    "stuck_recovery": "\u5361\u6b7b\u6062\u590d",
}

def _cn(name):
    return STATE_CN.get(name, name)


class BaseState:
    def enter(self, blackboard):
        blackboard["state_start_time"] = time.time()

    def update(self, blackboard):
        raise NotImplementedError

    def exit(self, blackboard):
        pass


class FSM:
    def __init__(self):
        self._states = {}
        self._current = None
        self._previous = None

    def add(self, name, state):
        if not isinstance(state, BaseState):
            raise TypeError(f"State must be a BaseState instance, got {type(state)}")
        self._states[name] = state

    def transition(self, name, blackboard):
        if name not in self._states:
            raise KeyError(f"State '{name}' not found. Available: {list(self._states.keys())}")
        if self._current is not None:
            logger.info("\u72b6\u6001\u5207\u6362: %s \u2192 %s", _cn(self._current), _cn(name))
            self._states[self._current].exit(blackboard)
        else:
            logger.info("\u521d\u59cb\u72b6\u6001: %s", _cn(name))
        self._previous = self._current
        self._current = name
        self._states[self._current].enter(blackboard)

    def update(self, blackboard):
        if self._current is None:
            return
        self._states[self._current].update(blackboard)

    @property
    def current(self):
        return self._current

    @property
    def current_state(self):
        if self._current is None:
            return None
        return self._states[self._current]
