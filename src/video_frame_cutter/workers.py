import logging
from threading import Event

from PySide6.QtCore import QThread, Signal

from .video import Cancelled


class Job(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = Event()

    def run(self):
        try:
            value = self.function(self.cancel, self.progress.emit)
            if not self.cancel.is_set():
                self.result.emit(value)
        except Cancelled:
            pass
        except Exception as error:
            logging.getLogger(__name__).exception("Background operation failed")
            self.failed.emit(f"{type(error).__name__}: {error}")
