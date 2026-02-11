"""
Let Me LC That For You - GUI Entry Point

This entry point launches the NiceGUI configuration interface for
managing WoW loot council decisions via LLM APIs.
"""

import sys
import os

# PyInstaller with console=False sets sys.stdout/stderr to None on Windows.
# Redirect to devnull to prevent crashes in libraries (e.g., uvicorn) that
# call stream.isatty() without null-checking.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

import multiprocessing

# On Linux, set multiprocessing to use 'spawn' instead of 'fork'
# This is required because Qt doesn't work correctly after fork() -
# the child process inherits corrupted Qt state from the parent.
# Must be done BEFORE freeze_support() and any other multiprocessing calls.
if sys.platform.startswith('linux'):
    multiprocessing.set_start_method('spawn', force=True)

# Required for multiprocessing to work with PyInstaller on Windows
# Must be called at the very beginning before any other code runs
multiprocessing.freeze_support()

# Configure Qt platform BEFORE any Qt imports (must happen early)
if sys.platform.startswith('linux'):
    from wowlc.qt.platform_setup import configure_qt_platform
    configure_qt_platform()

import logging
from pathlib import Path


def _show_splash():
    """Show a splash screen during app loading (bundled exe only)."""
    if not getattr(sys, 'frozen', False):
        return None
    try:
        import tkinter as tk
        base = sys._MEIPASS
        image_path = os.path.join(base, 'assets', 'med_logo.png')
        if not os.path.exists(image_path):
            return None

        splash = tk.Tk()
        splash.overrideredirect(True)
        splash.attributes('-topmost', True)
        # Drop topmost after a brief moment so it doesn't cover other apps
        splash.after(1000, lambda: splash.attributes('-topmost', False))

        img = tk.PhotoImage(file=image_path)
        splash._splash_img = img  # prevent garbage collection

        label = tk.Label(splash, image=img, borderwidth=0)
        label.pack()

        # Center on screen
        splash.update_idletasks()
        w = splash.winfo_width()
        h = splash.winfo_height()
        x = (splash.winfo_screenwidth() - w) // 2
        y = (splash.winfo_screenheight() - h) // 2
        splash.geometry(f'+{x}+{y}')

        splash.update()
        return splash
    except Exception:
        return None


def setup_logging():
    """Configure logging to file for debugging."""
    try:
        # Use PathManager for consistent log directory across platforms
        from wowlc.core.paths import get_path_manager
        paths = get_path_manager()
        log_dir = paths.get_log_dir()
        log_file = log_dir / "launcher.log"
    except Exception:
        # Fallback to temp directory if PathManager fails
        import tempfile
        log_file = Path(tempfile.gettempdir()) / "letmelcthatforyou_launcher.log"

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, mode='w', encoding='utf-8'),
            logging.StreamHandler(sys.stderr)
        ]
    )

    # Suppress verbose LiteLLM logging (only show warnings and errors)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)

    return log_file


def main():
    """Main entry point - launches the GUI."""
    splash = _show_splash()
    log_file = None
    try:
        log_file = setup_logging()
        logging.info("=== Let Me LC That For You Startup ===")
        logging.info(f"Log file: {log_file}")
        logging.info(f"Python: {sys.version}")
        logging.info(f"Frozen: {getattr(sys, 'frozen', False)}")
        logging.info(f"Arguments: {sys.argv}")

        # Launch GUI
        logging.info("→ Starting GUI mode")
        from wowlc.services.gui import run_gui
        run_gui(splash=splash)

    except Exception as e:
        # Dismiss splash so error dialog is visible
        if splash:
            try:
                splash.destroy()
            except Exception:
                pass
            splash = None
        logging.exception("FATAL ERROR during startup")
        # Try to show error dialog if possible
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Startup Error",
                f"letmelcthatforyou failed to start.\n\n"
                f"Error: {str(e)}\n\n"
                f"Check log file:\n{log_file}"
            )
        except:
            pass
        raise


if __name__ == "__main__":
    main()
