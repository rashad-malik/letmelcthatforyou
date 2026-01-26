"""Qt-based TMB authentication with cookie capture."""
import sys

# Ensure platform is configured before Qt imports
if sys.platform.startswith('linux'):
    from .platform_setup import configure_qt_platform
    configure_qt_platform()

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtCore import QUrl, Signal, QObject, QTimer, QEventLoop

THATSMYBIS_BASE_URL = "https://thatsmybis.com"


class CookieCapture(QObject):
    """Capture cookies from QWebEngineCookieStore."""

    def __init__(self, cookie_store):
        super().__init__()
        self.cookie_store = cookie_store
        self.captured_cookies = []
        self.cookie_store.cookieAdded.connect(self._on_cookie_added)

    def _on_cookie_added(self, cookie):
        domain = cookie.domain()
        if "thatsmybis" in domain:
            cookie_dict = {
                "name": cookie.name().data().decode('utf-8'),
                "value": cookie.value().data().decode('utf-8'),
                "domain": domain,
                "path": cookie.path(),
                "expires": cookie.expirationDate().toSecsSinceEpoch() if cookie.expirationDate().isValid() else -1,
            }
            # Update existing or add new
            for i, c in enumerate(self.captured_cookies):
                if c["name"] == cookie_dict["name"] and c["domain"] == cookie_dict["domain"]:
                    self.captured_cookies[i] = cookie_dict
                    return
            self.captured_cookies.append(cookie_dict)

    def get_tmb_cookies(self):
        return [c for c in self.captured_cookies if "thatsmybis" in c.get("domain", "")]


class TMBAuthWindow(QMainWindow):
    """Authentication window for ThatsmyBIS."""

    auth_complete = Signal(list)
    auth_failed = Signal(str)

    def __init__(self, timeout_seconds: int = 300):
        super().__init__()
        self.setWindowTitle("ThatsmyBIS Login - Discord Authentication")
        self.setMinimumSize(1000, 700)

        self.timeout_seconds = timeout_seconds
        self._auth_done = False

        self.profile = QWebEngineProfile.defaultProfile()
        self.cookie_capture = CookieCapture(self.profile.cookieStore())

        self.page = QWebEnginePage(self.profile, self)
        self.web_view = QWebEngineView()
        self.web_view.setPage(self.page)
        self.setCentralWidget(self.web_view)

        self.web_view.urlChanged.connect(self._on_url_changed)
        self.web_view.setUrl(QUrl(f"{THATSMYBIS_BASE_URL}/login"))

        # Timeout timer
        self.timeout_timer = QTimer(self)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self._on_timeout)
        self.timeout_timer.start(self.timeout_seconds * 1000)

    def _on_url_changed(self, url: QUrl):
        url_str = url.toString()
        if (THATSMYBIS_BASE_URL in url_str
            and "/login" not in url_str
            and "/oauth" not in url_str):
            QTimer.singleShot(500, self._finalize_auth)

    def _finalize_auth(self):
        if self._auth_done:
            return
        self._auth_done = True

        cookies = self.cookie_capture.get_tmb_cookies()
        if cookies:
            self.auth_complete.emit(cookies)
        else:
            self.auth_failed.emit("No ThatsmyBIS cookies captured")
        self.close()

    def _on_timeout(self):
        if not self._auth_done:
            self._auth_done = True
            self.auth_failed.emit(f"Authentication timed out after {self.timeout_seconds} seconds")
            self.close()

    def closeEvent(self, event):
        if not self._auth_done:
            self._auth_done = True
            self.auth_failed.emit("Authentication cancelled")
        event.accept()


def authenticate_with_qt(timeout_seconds: int = 300) -> list[dict]:
    """Authenticate using Qt WebEngine with cookie capture."""
    app = QApplication.instance()
    created_app = False
    if app is None:
        app = QApplication(sys.argv)
        created_app = True

    result = {"cookies": None, "error": None}

    def on_success(cookies):
        result["cookies"] = cookies

    def on_failure(error):
        result["error"] = error

    window = TMBAuthWindow(timeout_seconds=timeout_seconds)
    window.auth_complete.connect(on_success)
    window.auth_failed.connect(on_failure)
    window.show()

    if created_app:
        app.exec()
    else:
        loop = QEventLoop()
        window.destroyed.connect(loop.quit)
        loop.exec()

    if result["error"]:
        raise Exception(result["error"])
    if not result["cookies"]:
        raise Exception("No cookies captured")

    return result["cookies"]
