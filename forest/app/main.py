"""Start the Forest Studio desktop window."""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
UI = APP_DIR / "ui" / "index.html"
ICON = APP_DIR / "icons" / "foreststudio-xp.ico"


def _set_windows_app_id() -> None:
    """Give the process its own taskbar identity so Windows shows our icon, not Python's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ForestStudio.XP")
    except (AttributeError, OSError):
        pass


def main() -> None:
    import webview

    from forest.app.api import Api

    _set_windows_app_id()
    webview.create_window(
        "ForestStudio XP", url=str(UI), js_api=Api(),
        width=1480, height=920, min_size=(1100, 700), background_color="#11161b",
    )
    webview.start(icon=str(ICON) if ICON.is_file() else None)


if __name__ == "__main__":
    main()
