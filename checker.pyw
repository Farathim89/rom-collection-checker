"""ROM Collection Checker - what you have, what's missing and what's a mess in your game collection."""
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.config import load_settings
from app.portable import app_dir
from app.ui import theme
from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ROM Collection Checker")
    app.setStyle("Fusion")
    base = getattr(sys, "_MEIPASS", None) or str(app_dir())
    app.setWindowIcon(QIcon(f"{base}/assets/icon.png"))
    theme.apply(load_settings().theme, app)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
