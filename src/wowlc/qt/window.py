"""Qt-based native window for NiceGUI application."""
import sys
import time
import urllib.request
import urllib.error

# Ensure platform is configured before Qt imports
if sys.platform.startswith('linux'):
    from .platform_setup import configure_qt_platform
    configure_qt_platform()

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl


class NiceGUIWindow(QMainWindow):
    """Main window that embeds NiceGUI web content."""

    def __init__(self, url: str = "http://localhost:8080", title: str = "Let Me LC That For You"):
        super().__init__()
        self.setWindowTitle(title)
        self.setMinimumSize(1200, 800)

        self.web_view = QWebEngineView()
        self.setCentralWidget(self.web_view)
        self.web_view.setUrl(QUrl(url))

    def closeEvent(self, event):
        event.accept()
        QApplication.quit()


def wait_for_server(url: str, timeout: float = 30.0) -> bool:
    """Wait for NiceGUI server to be ready."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.1)
    return False


def run_qt_window(port: int = 8080):
    """Start Qt application and show NiceGUI window."""
    import os

    # Qt environment setup for bundled apps
    if getattr(sys, 'frozen', False):
        os.environ['QTWEBENGINE_DISABLE_SANDBOX'] = '1'

    app = QApplication.instance() or QApplication(sys.argv)
    url = f"http://localhost:{port}"

    if not wait_for_server(url):
        raise RuntimeError("NiceGUI server failed to start")

    window = NiceGUIWindow(url=url)
    window.show()

    return app.exec()
