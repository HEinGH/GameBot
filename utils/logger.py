import sys
import os
import logging
import traceback
from datetime import datetime
from pathlib import Path


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent.parent


LOG_DIR = get_app_dir() / "logs"
DEBUG_DIR = LOG_DIR / "debug"


class DailyRotatingFileHandler(logging.FileHandler):
    def __init__(self, pattern, **kwargs):
        self._pattern = str(pattern)
        self._current_date = None
        filename = self._make_filename()
        encoding = kwargs.pop("encoding", "utf-8")
        super().__init__(filename, encoding=encoding, **kwargs)

    def _make_filename(self):
        return self._pattern.format(date=datetime.now().strftime("%Y-%m-%d"))

    def emit(self, record):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            try:
                self.close()
            except Exception:
                pass
            self.baseFilename = os.path.abspath(self._make_filename())
            self.stream = self._open()
        super().emit(record)


def setup_logger(level=logging.DEBUG):
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
    sh.setLevel(logging.INFO)
    logger.addHandler(sh)

    regular_pattern = LOG_DIR / "game_bot_{date}.log"
    fh_regular = DailyRotatingFileHandler(regular_pattern)
    fh_regular.setFormatter(fmt)
    fh_regular.setLevel(logging.DEBUG)
    logger.addHandler(fh_regular)

    error_pattern = LOG_DIR / "game_bot_error_{date}.log"
    fh_error = DailyRotatingFileHandler(error_pattern)
    fh_error.setFormatter(fmt)
    fh_error.setLevel(logging.WARNING)
    logger.addHandler(fh_error)

    return logger


def setup_excepthook():
    crash_path = LOG_DIR / "game_bot_crash_{date}.log"

    def _crash_filename():
        return str(crash_path).replace("{date}", datetime.now().strftime("%Y-%m-%d"))

    def excepthook(exc_type, exc_value, exc_tb):
        lines = [
            "=" * 70,
            f"UNHANDLED EXCEPTION ({exc_type.__name__})",
            "=" * 70,
            f"Time: {datetime.now()}",
            f"Frozen: {getattr(sys, 'frozen', False)}",
            f"Exe: {sys.executable if getattr(sys, 'frozen', False) else __file__}",
            f"Args: {sys.argv}",
            "-" * 70,
        ]
        lines.extend(traceback.format_exception(exc_type, exc_value, exc_tb))
        lines.append("=" * 70)

        try:
            crash_file = _crash_filename()
            Path(crash_file).parent.mkdir(parents=True, exist_ok=True)
            with open(crash_file, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n\n")
        except Exception:
            pass

        print(f"\n!!! CRASH: {exc_type.__name__}: {exc_value}", file=sys.stderr)
        print(f"    Crash log written to: {_crash_filename()}", file=sys.stderr)

    sys.excepthook = excepthook
