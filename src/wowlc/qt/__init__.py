"""Qt-based GUI components for cross-platform native window support."""
import sys

# Configure platform before exposing Qt functionality
if sys.platform.startswith('linux'):
    from .platform_setup import configure_qt_platform
    configure_qt_platform()

from .window import NiceGUIWindow, run_qt_window
from .auth_webview import authenticate_with_qt

__all__ = ['NiceGUIWindow', 'run_qt_window', 'authenticate_with_qt']
