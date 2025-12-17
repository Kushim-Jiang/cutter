import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

REPO_ROOT = Path(__file__).parent.parent
sys.path.append(str(REPO_ROOT))

from ui.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
win = MainWindow()
win.show()
sys.exit(app.exec())
