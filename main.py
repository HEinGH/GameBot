import sys
import time
import json
import logging

from config.settings import Settings, ROOT_DIR, PRESETS_DIR
from config.settings import resolve_characters
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
                logger.warning("加载预设失败 %s: %s", f.name, e)
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
    logger.info("GameBot 已启动")
    logger.info("日志目录: %s", LOG_DIR)
    logger.info("预设目录: %s", PRESETS_DIR)
    logger.info("=" * 50)

    if not presets:
        logger.error("未找到预设文件: %s", PRESETS_DIR)
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
        logger.info("使用预设: %s", preset_name)

    if preset_name not in presets:
        logger.error("预设 '%s' 不存在", preset_name)
        sys.exit(1)

    preset = presets[preset_name]
    preset["characters"] = resolve_characters(preset, preset_name)
    total_chars = max(1, args.characters) if args.characters is not None else max(1, len(preset.get("characters", [])))
    if args.fps:
        cfg._data["fps_limit"] = args.fps

    window_mgr = None
    if args.background or args.window_title or args.secondary_monitor is not None:
        from utils.window_manager import WindowManager
        title = args.window_title or preset.get("window_title", "")
        window_mgr = WindowManager(title_keyword=title)
        if not window_mgr.find_window(retries=5):
            logger.warning("未找到游戏窗口，焦点监控已禁用")

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

    from input.controller import Controller, _SAFE_STEALTH_STATES
    controller = Controller(
        stealth=args.stealth,
        combo_randomness=cfg.combo_randomness,
        bezier_steps=cfg.mouse_bezier_steps,
        click_jitter=cfg.click_jitter_px,
        background_mode=args.background,
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

    _vdm = None
    if window_mgr and args.background:
        window_mgr.save_position()
        logger.info("后台模式已开启: 窗口将自动管理")
        from utils.virtual_display import VirtualDisplayManager
        _vdm = VirtualDisplayManager()
        if _vdm.is_installed():
            if _vdm.enable(timeout=10):
                vdd_idx = _vdm.get_monitor_index()
                if vdd_idx >= 0:
                    window_mgr.move_to_monitor(vdd_idx)
                    time.sleep(1.0)
                    dxcam_info = _vdm.get_dxcam_output_idx()
                    capture.stop()
                    time.sleep(0.3)
                    if isinstance(dxcam_info, tuple) and dxcam_info[0] >= 0:
                        capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit,
                                      device_idx=dxcam_info[0], output_idx=dxcam_info[1])
                    else:
                        capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit,
                                      monitor=vdd_idx)
                    logger.info("游戏已移至虚拟显示器 %d，截图已重定向", vdd_idx)
                else:
                    logger.warning("无法确定虚拟显示器索引")
            else:
                logger.warning("虚拟显示器启用失败")
        elif args.secondary_monitor is not None:
            window_mgr.move_to_monitor(args.secondary_monitor)
            logger.info("窗口已移至显示器 %d", args.secondary_monitor)

    fsm.transition("character_select", blackboard)
    logger.info("Bot已启动 预设=%s 角色数=%d", preset_name, total_chars)
    logger.info("按 Ctrl+C 停止")

    if window_mgr:
        logger.info("游戏窗口: %s | 焦点: %s | 后台: %s",
                    window_mgr.title, window_mgr.is_focused, args.background)

    try:
        while blackboard["running"]:
            if window_mgr:
                if window_mgr.is_minimized:
                    if args.background:
                        window_mgr.activate()
                        time.sleep(0.5)
                    else:
                        logger.info("游戏已最小化，等待恢复...")
                        while window_mgr.is_minimized and blackboard["running"]:
                            time.sleep(1.0)
                        logger.info("游戏已恢复，继续运行")
                        time.sleep(0.5)

                if not window_mgr.is_focused and not args.background:
                    logger.info("游戏窗口未聚焦，请点击游戏窗口或按 Ctrl+C")
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
                logger.warning("检测到卡死，进入恢复流程")
                watchdog.reset()
                fsm.transition("stuck_recovery", blackboard)
            fsm.update(blackboard)
            if args.stealth and fsm.current in _SAFE_STEALTH_STATES:
                controller.occasional_look_around()
            time.sleep(1.0 / max(cfg.fps_limit, 1))
    except KeyboardInterrupt:
        logger.info("用户请求停止")
    finally:
        controller.release_all()
        capture.stop()
        if window_mgr:
            window_mgr.close()
        if _vdm and _vdm.is_enabled():
            _vdm.disable()
        logger.info("Bot已停止")


if __name__ == "__main__":
    main()
