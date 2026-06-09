import sys
import os
import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent.parent


LOG_DIR = get_app_dir() / "logs"
DEBUG_DIR = LOG_DIR / "debug"


def setup_logger(level=logging.INFO):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(level)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s:%(lineno)d %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fmt_compact = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt_compact)
    sh.setLevel(level)
    logger.addHandler(sh)

    regular_path = LOG_DIR / "game_bot.log"
    fh_regular = RotatingFileHandler(
        regular_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh_regular.setFormatter(fmt)
    fh_regular.setLevel(level)
    logger.addHandler(fh_regular)

    error_path = LOG_DIR / "game_bot_error.log"
    fh_error = RotatingFileHandler(
        error_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    fh_error.setFormatter(fmt)
    fh_error.setLevel(logging.WARNING)
    logger.addHandler(fh_error)

    return logger


def setup_excepthook():
    crash_path = LOG_DIR / "game_bot_crash.log"

    def excepthook(exc_type, exc_value, exc_tb):
        lines = [
            "=" * 70,
            f"UNHANDLED EXCEPTION ({exc_type.__name__})",
            "=" * 70,
            f"Time: {__import__('datetime').datetime.now()}",
            f"Frozen: {getattr(sys, 'frozen', False)}",
            f"Exe: {sys.executable if getattr(sys, 'frozen', False) else __file__}",
            f"Args: {sys.argv}",
            "-" * 70,
        ]
        lines.extend(traceback.format_exception(exc_type, exc_value, exc_tb))
        lines.append("=" * 70)

        try:
            crash_path.parent.mkdir(parents=True, exist_ok=True)
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n\n")
        except Exception:
            pass

        print(f"\n!!! CRASH: {exc_type.__name__}: {exc_value}", file=sys.stderr)
        print(f"    Crash log written to: {crash_path}", file=sys.stderr)
        print(f"    Full log: {LOG_DIR / 'game_bot.log'}", file=sys.stderr)

    sys.excepthook = excepthook
