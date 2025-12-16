from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from models.box import Box
from ui.box_item import BoxItem


class ImageView(QGraphicsView):
    box_items: list[BoxItem]
    drawing: bool
    start: Optional[QPointF]
    temp: Optional[object]

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.box_items = []
        self.drawing = False
        self.start = None
        self.temp = None

    def load_image(self, path: Path) -> None:
        self.scene().clear()
        self.box_items.clear()

        pix: QPixmap = QPixmap(str(path))
        self.scene().addPixmap(pix)
        self.setSceneRect(pix.rect())

    def load_boxes(self, boxes: list[Box]) -> None:
        for box in boxes:
            item = BoxItem(box)
            self.scene().addItem(item)
            self.box_items.append(item)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.drawing = True
            self.start = self.mapToScene(event.pos())
            self.temp = self.scene().addRect(QRectF())
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.drawing and self.start and self.temp:
            cur: QPointF = self.mapToScene(event.pos())
            self.temp.setRect(QRectF(self.start, cur).normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.drawing and self.start and self.temp:
            r: QRectF = self.temp.rect()
            self.scene().removeItem(self.temp)

            box = Box(
                x=int(r.x()),
                y=int(r.y()),
                w=int(r.width()),
                h=int(r.height()),
                source="manual",
            )

            item = BoxItem(box)
            self.scene().addItem(item)
            self.box_items.append(item)

            self.drawing = False
            self.start = None
            self.temp = None
        else:
            super().mouseReleaseEvent(event)
