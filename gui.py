#!/usr/bin/env python3
"""GameBot GUI Launcher

Launch the graphical interface for GameBot configuration and control.
Usage:
    python gui.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logger

setup_logger()

if __name__ == "__main__":
    from gui.app import GameBotGUI
    app = GameBotGUI()
    app.run()
