"""Точка входа Video Converter."""
import ctypes
import os
import sys

import wx

from vc.frame import VideoConverter
from vc.single_instance import INSTANCE_NAME, send_paths_to_running_instance

# --- HiDPI (Windows only) ---
if sys.platform.startswith("win"):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        pass


def main():
    app = wx.App()
    cli_paths = [os.path.abspath(p) for p in sys.argv[1:] if os.path.isfile(p)]
    # Объект должен жить всё время работы приложения — он удерживает мьютекс.
    app.instance_checker = wx.SingleInstanceChecker(INSTANCE_NAME)
    if app.instance_checker.IsAnotherRunning() and send_paths_to_running_instance(cli_paths):
        sys.exit(0)
    top = VideoConverter()
    top.start_instance_server()
    if cli_paths:
        wx.CallAfter(top.add_files, cli_paths)
    app.MainLoop()


if __name__ == "__main__":
    main()
