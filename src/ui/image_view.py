from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QKeyEvent, QKeySequence, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QRubberBand

from models.box import Box
from ui.box_item import BoxItem

ZOOM_FACTOR = 1.2


class ImageView(QGraphicsView):
    box_items: list[BoxItem]

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

        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._origin_scene: Optional[QPoint] = None
        self._select_mode = False
        self._zoom = 1.0

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def load_image(self, path: Path) -> None:
        self.scene().clear()
        self.box_items.clear()

        pixmap = QPixmap(str(path))
        self.scene().addPixmap(pixmap)
        self.setSceneRect(pixmap.rect())

    def load_boxes(self, boxes: list[Box]) -> None:
        for box in boxes:
            item = BoxItem(box)
            self.scene().addItem(item)
            self.box_items.append(item)

    def delete_selected_boxes(self) -> None:
        for item in self.scene().selectedItems():
            if isinstance(item, BoxItem):
                self.scene().removeItem(item)
                if item in self.box_items:
                    self.box_items.remove(item)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._select_mode = True
            self._origin_scene = self.mapToScene(event.pos())
            origin_view_pos = event.pos()
            self._rubber.setGeometry(QRect(origin_view_pos, QSize()))
            self._rubber.show()
            self.sel_str.emit(f"Select: ({int(self._origin_scene.x())}, {int(self._origin_scene.y())}), Size: (0, 0)")
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        scene_pos = self.mapToScene(event.pos())
        self.pos_str.emit(f"Pos: ({int(scene_pos.x())}, {int(scene_pos.y())})")

        if self._select_mode and self._origin_scene is not None:
            x0, y0 = self._origin_scene.x(), self._origin_scene.y()
            x1, y1 = scene_pos.x(), scene_pos.y()

            rect_view = QRect(self.mapFromScene(self._origin_scene), event.pos()).normalized()
            self._rubber.setGeometry(rect_view)

            width, height = abs(x1 - x0), abs(y1 - y0)
            self.sel_str.emit(f"Select: ({int(min(x0, x1))}, {int(min(y0, y1))}), Size: ({int(width)}, {int(height)})")
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.sel_str.emit("Select: (-, -), Size: (-, -)")

        if self._select_mode and self._origin_scene is not None:
            x0, y0 = self._origin_scene.x(), self._origin_scene.y()
            end_scene_point = self.mapToScene(event.pos()).toPoint()
            x1, y1 = end_scene_point.x(), end_scene_point.y()
            width, height = abs(x1 - x0), abs(y1 - y0)

            is_click = width < 5 and height < 5
            if is_click:
                self._handle_click(end_scene_point)

            self._rubber.hide()
            rect_view = QRect(self.mapFromScene(self._origin_scene), event.pos()).normalized()
            scene_rect = self.mapToScene(rect_view).boundingRect().toRect()

            modifiers = event.modifiers()
            if event.button() == Qt.MouseButton.LeftButton:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    # Shift + drag to select boxes
                    self.selection_finished.emit(scene_rect)
                else:
                    # normal drag to draw box
                    for item in self.scene().items(scene_rect):
                        if isinstance(item, BoxItem):
                            item.setSelected(True)

            self._origin_scene = None
            self._select_mode = False
            return
        super().mouseReleaseEvent(event)

    def _handle_click(self, scene_pos: QPoint) -> None:
        for item in self.box_items:
            item.setSelected(False)

        candidates: list[tuple[float, BoxItem]] = []
        for item in self.scene().items(scene_pos):
            if isinstance(item, BoxItem):
                rect = item.sceneBoundingRect()
                if rect.contains(scene_pos):
                    area = rect.width() * rect.height()
                    candidates.append((area, item))
        if candidates:
            _, smallest = min(candidates, key=lambda pair: pair[0])
            smallest.setSelected(not smallest.isSelected())

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        modifiers = event.modifiers()

        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Shift + wheel to scroll horizontally
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
            return

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            # Ctrl + wheel to zoom in/out
            factor = ZOOM_FACTOR if delta > 0 else 1 / ZOOM_FACTOR
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
        if event.matches(QKeySequence.StandardKey.Find):
            self.fit_to_view()
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.detect.emit()
            return

        super().keyPressEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRect) -> None:
        painter.fillRect(rect, QBrush(Qt.GlobalColor.lightGray))
        super().drawBackground(painter, rect)

    def fit_to_view(self) -> None:
        scene_rect = self.sceneRect()
        self.resetTransform()
        self.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()
        self.zoom_changed.emit(f"Zoom: {(self._zoom * 100):.2f}%")
