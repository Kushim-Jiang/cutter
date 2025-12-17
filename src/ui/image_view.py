from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent, QPixmap, QWheelEvent
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
    save = Signal()
    detect = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.box_items = []
        self.drawing = False
        self.start = None
        self.temp = None

        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._origin: Optional[QPoint] = None
        self._select_mode = False
        self._zoom = 1.0
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

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
        if (event.button() == Qt.MouseButton.LeftButton) or (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._select_mode = True
            self._origin = event.pos()
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = self.mapToScene(event.pos())
        self.pos_str.emit(f"Pos: ({int(pos.x())}, {int(pos.y())})")
        if self._select_mode and self._origin:
            rect = QRect(self._origin, event.pos()).normalized()
            self._rubber.setGeometry(rect)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.sel_str.emit("Sel: (-, -)")
        if self._select_mode and self._origin:
            self._rubber.hide()
            rect = QRect(self._origin, event.pos()).normalized()
            scene_rect = self.mapToScene(rect).boundingRect().toRect()

            # shift + click and move to select
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                for item in self.scene().items(scene_rect):
                    if isinstance(item, BoxItem):
                        item.setSelected(True)
            # click and move to draw
            elif event.button() == Qt.MouseButton.LeftButton:
                self.selection_finished.emit(scene_rect)

            self._origin = None
            self._select_mode = False
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()

        # shift + wheel to scroll horizontally
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
            return
        # ctrl + wheel to zoom
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if delta > 0 else 1 / 1.2
            self.scale(factor, factor)
            self._zoom *= factor
            self.zoom_changed.emit(f"Zoom: {(self._zoom * 100):.2f}%")
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.SelectAll):
            for item in self.scene().items():
                if isinstance(item, BoxItem):
                    item.setSelected(True)
            return
        if event.matches(QKeySequence.StandardKey.Save):
            self.save.emit()
            return
        if event.key() == Qt.Key.Key_Enter:
            self.detect.emit()
            return
        super().keyPressEvent(event)
