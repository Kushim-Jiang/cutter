from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

ROW_HEIGHT = 30


class ImageCellWidget(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(ROW_HEIGHT, ROW_HEIGHT)
        self._path: Path | None = None

    def set_image(self, path: str | Path | None) -> None:
        self._path = Path(path) if path else None
        if self._path and self._path.exists():
            pix = QPixmap(str(self._path))
            if not pix.isNull():
                self.setPixmap(
                    pix.scaled(
                        QSize(ROW_HEIGHT, ROW_HEIGHT),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        else:
            self.clear()

    @property
    def path(self) -> Path | None:
        return self._path
