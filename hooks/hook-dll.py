"""
PyInstaller runtime hook to fix DLL search paths on Windows.

Anaconda/conda puts its own Qt6 DLLs on PATH which shadow PySide6's
bundled DLLs, causing "DLL load failed" at import time.
Runtime hooks execute BEFORE the main script, ensuring DLL paths
are configured before any PySide6 imports.
"""
import os
import sys


def _fix_dll_paths():
    if not (getattr(sys, 'frozen', False) and sys.platform == 'win32'):
        return

    # Register bundled DLL directories so Windows finds them first
    base = sys._MEIPASS
    os.add_dll_directory(base)

    pyside6_dir = os.path.join(base, 'PySide6')
    if os.path.isdir(pyside6_dir):
        os.add_dll_directory(pyside6_dir)

    # Remove conda directories from PATH to prevent loading wrong Qt DLLs
    conda_indicators = ('anaconda', 'conda', 'miniconda')
    path_dirs = os.environ.get('PATH', '').split(os.pathsep)
    clean_dirs = [d for d in path_dirs
                  if not any(ind in d.lower() for ind in conda_indicators)]
    os.environ['PATH'] = os.pathsep.join(clean_dirs)


_fix_dll_paths()
