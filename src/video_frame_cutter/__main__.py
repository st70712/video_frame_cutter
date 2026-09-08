import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description="Video frame annotation and export")
    parser.add_argument("video", nargs="?")
    arguments = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Video Frame Cutter")
    app.setOrganizationName("Video Frame Cutter")
    app.setStyle("Fusion")
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
    window = MainWindow()
    window.show()
    if arguments.video:
        QTimer.singleShot(0, lambda: window.load_video(arguments.video))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
