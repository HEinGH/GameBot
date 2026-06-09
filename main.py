import sys
import time
import json
import logging

from config.settings import Settings, ROOT_DIR, PRESETS_DIR
from utils.logger import setup_logger, setup_excepthook, LOG_DIR

logger = logging.getLogger(__name__)


def load_presets():
    presets = {}
    if PRESETS_DIR.exists():
        for f in sorted(PRESETS_DIR.glob("*.json")):
            try:
                with open(f, encoding="utf-8") as fp:
                    presets[f.stem] = json.load(fp)
            except Exception as e:
                logger.warning("Failed to load preset %s: %s", f.name, e)
    return presets


def list_presets(presets):
    print("\nAvailable presets:")
    for name in presets:
        chars = presets[name].get("characters", [])
        print(f"  {name}  ({len(chars)} characters)")


def main():
    setup_logger(level=logging.INFO)
    setup_excepthook()
    cfg = Settings()
    cfg.load()
    presets = load_presets()

    logger.info("=" * 50)
    logger.info("GameBot started")
    logger.info("Log directory: %s", LOG_DIR)
    logger.info("Presets dir:  %s", PRESETS_DIR)
    logger.info("=" * 50)

    if not presets:
        logger.error("No presets found in %s", PRESETS_DIR)
        sys.exit(1)

    list_presets(presets)

    import argparse
    parser = argparse.ArgumentParser(description="Game Bot - Automated Stamina Clearing")
    parser.add_argument("--preset", "-p", help="Preset name to use", default=None)
    parser.add_argument("--characters", "-c", type=int, help="Number of characters", default=None)
    parser.add_argument("--fps", type=int, help="Capture FPS limit", default=None)
    parser.add_argument("--list", action="store_true", help="List available presets")
    parser.add_argument("--background", action="store_true",
                        help="Background mode: auto-manage game window focus")
    parser.add_argument("--window-title", type=str, default="",
                        help="Game window title keyword for auto-detection")
    parser.add_argument("--secondary-monitor", type=int, default=None,
                        help="Move window to this monitor index in background mode")
    parser.add_argument("--stealth", action="store_true",
                        help="Stealth mode: behavioral anti-detection (randomized timing, human-like mouse, look-around)")
    parser.add_argument("--record-combo", type=str, default=None, metavar="NAME",
                        help="Record a macro: captures keyboard + mouse into a combo preset")
    args = parser.parse_args()

    if args.list:
        return

    if args.record_combo:
        from utils.macro_recorder import MacroRecorder
        recorder = MacroRecorder(output_dir=ROOT_DIR / "combos")
        recorder.record(name=args.record_combo)
        return

    preset_name = args.preset
    if not preset_name:
        preset_name = next(iter(presets))
        logger.info("Using preset: %s", preset_name)

    if preset_name not in presets:
        logger.error("Preset '%s' not found", preset_name)
        sys.exit(1)

    preset = presets[preset_name]
    total_chars = max(1, args.characters) if args.characters is not None else max(1, len(preset.get("characters", [])))
    if args.fps:
        cfg._data["fps_limit"] = args.fps

    window_mgr = None
    if args.background or args.window_title or args.secondary_monitor is not None:
        from utils.window_manager import WindowManager
        title = args.window_title or preset.get("window_title", "")
        window_mgr = WindowManager(title_keyword=title)
        if not window_mgr.find_window(retries=5):
            logger.warning("Game window not found. Focus monitoring disabled.")

    from core.blackboard import Blackboard
    from core.fsm import FSM

    blackboard = Blackboard()
    blackboard["preset_name"] = preset_name
    blackboard["preset"] = preset
    blackboard["total_characters"] = total_chars
    blackboard["current_character_index"] = 0
    blackboard["domain_run_count"] = 0
    blackboard["_window_mgr"] = window_mgr
    blackboard["_background_mode"] = args.background

    from capture.screen import ScreenCapture
    capture = ScreenCapture()
    if window_mgr and args.secondary_monitor is not None:
        capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit,
                      monitor=args.secondary_monitor)
    else:
        capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit)
    blackboard["_capture"] = capture

    from input.controller import Controller
    controller = Controller(
        stealth=args.stealth,
        combo_randomness=cfg.combo_randomness,
        bezier_steps=cfg.mouse_bezier_steps,
        click_jitter=cfg.click_jitter_px,
    )

    fsm = FSM()
    blackboard["_fsm"] = fsm

    from states.character_select import CharacterSelectState
    from states.town_nav import TownNavState
    from states.domain_loading import DomainLoadingState
    from states.domain_combat import DomainCombatState
    from states.dungeon_exit_nav import DungeonExitNavState
    from states.map_loading import MapLoadingState
    from states.town_exit import TownExitState
    from states.complete import CompleteState
    from states.stuck_recovery import StuckRecoveryState
    from states.npc_navigate import NPCNavigateState

    fsm.add("character_select", CharacterSelectState(controller))
    fsm.add("town_nav", TownNavState(controller))
    fsm.add("npc_navigate", NPCNavigateState(controller))
    fsm.add("domain_loading", DomainLoadingState())
    fsm.add("domain_combat", DomainCombatState(controller, cfg.combo_randomness))
    fsm.add("dungeon_exit_nav", DungeonExitNavState(controller))
    fsm.add("map_loading", MapLoadingState())
    fsm.add("town_exit", TownExitState(controller))
    fsm.add("complete", CompleteState(controller))
    fsm.add("stuck_recovery", StuckRecoveryState(controller))

    from core.watchdog import Watchdog
    watchdog = Watchdog(
        threshold_sec=cfg.stuck_threshold_sec,
        ssim_threshold=cfg.ssim_threshold,
    )
    blackboard["_watchdog"] = watchdog

    if window_mgr and args.background:
        window_mgr.save_position()
        logger.info("Background mode ON: window will be managed automatically")
        if args.secondary_monitor is not None:
            window_mgr.move_to_monitor(args.secondary_monitor)
            logger.info("Window moved to monitor %d", args.secondary_monitor)

    fsm.transition("character_select", blackboard)
    logger.info("Bot started. Preset=%s Characters=%d", preset_name, total_chars)
    logger.info("Press Ctrl+C to stop")

    if window_mgr:
        logger.info("Game window: %s | Focused: %s | Background: %s",
                    window_mgr.title, window_mgr.is_focused, args.background)

    try:
        while blackboard["running"]:
            if window_mgr:
                if window_mgr.is_minimized:
                    if args.background:
                        window_mgr.activate()
                        time.sleep(0.5)
                    else:
                        logger.info("Game minimized. Waiting for restore...")
                        while window_mgr.is_minimized and blackboard["running"]:
                            time.sleep(1.0)
                        logger.info("Game restored, resuming")
                        time.sleep(0.5)

                if not window_mgr.is_focused and not args.background:
                    logger.info("Game window not focused. Focus it or press Ctrl+C.")
                    while not window_mgr.is_focused and blackboard["running"]:
                        time.sleep(0.5)

                if args.background and not window_mgr.is_focused:
                    window_mgr.activate()
                    time.sleep(0.3)

            frame = capture.frame
            blackboard["current_frame"] = frame
            if frame is not None:
                watchdog.update(frame, blackboard)
            if blackboard["stuck"] and fsm.current != "stuck_recovery":
                logger.warning("Stuck detected, entering recovery")
                watchdog.reset()
                fsm.transition("stuck_recovery", blackboard)
            fsm.update(blackboard)
            time.sleep(1.0 / max(cfg.fps_limit, 1))
    except KeyboardInterrupt:
        logger.info("Stopping by user request")
    finally:
        controller.release_all()
        capture.stop()
        if window_mgr:
            window_mgr.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    main()
