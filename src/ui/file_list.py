from pathlib import Path
from typing import Iterable

from PySide6.QtWidgets import QListWidget


class FileList(QListWidget):
    def load_files(self, paths: Iterable[Path]) -> None:
        self.clear()
        for p in paths:
            self.addItem(str(p))
