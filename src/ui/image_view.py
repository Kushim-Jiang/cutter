from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QRubberBand

from models.box import Box
from ui.box_item import BoxItem


class ImageView(QGraphicsView):
    box_items: list[BoxItem]
    drawing: bool
    start: Optional[QPointF]
    temp: Optional[object]
    selection_finished = Signal(QRect)
    pos_str = Signal(str)
    sel_str = Signal(str)
    zoom_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.box_items = []
        self.drawing = False
        self.start = None
        self.temp = None

        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._origin: Optional[QPoint] = None

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

    def delete_selected_boxes(self) -> None:
        for item in self.scene().selectedItems():
            if isinstance(item, BoxItem):
                self.scene().removeItem(item)
                self.box_items.remove(item)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = self.mapToScene(event.pos())
        self.sel_str.emit(f"Sel: ({int(pos.x())}, {int(pos.y())})")
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.pos()
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = self.mapToScene(event.pos())
        self.pos_str.emit(f"Pos: ({int(pos.x())}, {int(pos.y())})")
        if self._origin:
            rect = QRect(self._origin, event.pos()).normalized()
            self._rubber.setGeometry(rect)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.sel_str.emit("Sel: (-, -)")
        if self._origin:
            self._rubber.hide()
            rect = QRect(self._origin, event.pos()).normalized()
            scene_rect = self.mapToScene(rect).boundingRect().toRect()
            self.selection_finished.emit(scene_rect)
            self._origin = None
        super().mouseReleaseEvent(event)

