"""Thread-safe native folder picker.

Qt widgets may only be used from the Qt main thread, but the NiceGUI server
(and its event handlers) run on a daemon thread. This module bridges the two:
`pick_folder()` can be called from any worker thread; a queued signal delivers
the request to the Qt main thread, where the QFileDialog is legal, and the
result is passed back through a queue.
"""
import queue
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import QApplication, QFileDialog


class _FolderPickerBridge(QObject):
    pick_requested = Signal(str, str, object)  # title, start_dir, result queue

    def __init__(self):
        super().__init__()
        # Explicit QueuedConnection: emitters live on non-Qt threads.
        self.pick_requested.connect(self._on_pick, Qt.ConnectionType.QueuedConnection)

    @Slot(str, str, object)
    def _on_pick(self, title: str, start_dir: str, result_q) -> None:
        parent = QApplication.activeWindow()
        folder = QFileDialog.getExistingDirectory(
            parent, title, start_dir, QFileDialog.Option.ShowDirsOnly)
        result_q.put(folder)


_bridge: Optional[_FolderPickerBridge] = None


def install_folder_picker() -> None:
    """Create the bridge. Must be called on the Qt main thread after QApplication exists."""
    global _bridge
    if _bridge is None:
        _bridge = _FolderPickerBridge()


def is_available() -> bool:
    """Whether the native picker can be shown (bridge installed, Qt running)."""
    return _bridge is not None and QApplication.instance() is not None


def pick_folder(title: str, start_dir: str = "") -> Optional[str]:
    """Open a native folder picker and block until the user responds.

    Call from a worker thread (e.g. via NiceGUI's run.io_bound), never from
    the Qt main thread — it would deadlock waiting on its own event loop.

    Returns the selected folder, or None if cancelled or if no Qt window is
    available (e.g. browsing the server directly during development).
    """
    if _bridge is None or QApplication.instance() is None:
        return None
    result_q: queue.Queue = queue.Queue(maxsize=1)
    _bridge.pick_requested.emit(title, start_dir, result_q)
    return result_q.get() or None
