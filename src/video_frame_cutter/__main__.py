import argparse
import sys
from importlib.metadata import version
from importlib.resources import files

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow

APPLICATION_NAME = "Video Frame Cutter"


def parse_arguments(arguments=None):
    parser = argparse.ArgumentParser(description="Video frame annotation and export")
    parser.add_argument("video", nargs="?")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(arguments)


def configure_application(app):
    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationDisplayName(APPLICATION_NAME)
    app.setApplicationVersion(version("video-frame-cutter"))
    app.setOrganizationName(APPLICATION_NAME)
    app.setWindowIcon(QIcon(str(files("video_frame_cutter") / "resources" / "app-icon.png")))
    app.setStyle("Fusion")
    if sys.platform == "win32":
        app.setFont(QFont("Microsoft JhengHei UI", 10))
    app.setStyleSheet("""
        QMainWindow, QDialog { background: #f7f8f8; }
        QWidget { color: #222b29; }
        QToolBar { border: 0; spacing: 6px; padding: 6px; background: #e9eeec; }
        QPushButton { padding: 5px 9px; }
        QListWidget { background: #ffffff; border: 1px solid #d6dedb; }
        QListWidget::item:selected { background: #d4eee6; color: #174e43; }
        QProgressBar::chunk { background: #228673; }
        QToolTip { color: #ffffff; background: #293a34; border: 0; }
    """)


def main(arguments=None):
    arguments = parse_arguments(arguments)
    app = QApplication(sys.argv[:1])
    configure_application(app)
    window = MainWindow()
    window.show()
    if arguments.smoke_test:
        QTimer.singleShot(0, app.quit)
    elif arguments.video:
        QTimer.singleShot(0, lambda: window.load_video(arguments.video))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
