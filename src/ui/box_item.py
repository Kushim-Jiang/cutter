from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSceneHoverEvent, QGraphicsSceneMouseEvent

from models.box import Box

HANDLE: int = 6


class BoxItem(QGraphicsRectItem):
    box: Box
    resizing: bool
    resize_dir: Optional[str]
    start_rect: QRectF
    start_pos: QRectF

    def __init__(self, box: Box) -> None:
        super().__init__(box.x, box.y, box.w, box.h)
        self.box = box
        self.resizing = False
        self.resize_dir = None

        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.update_style()

    def update_style(self) -> None:
        if self.box.locked:
            color = QColor(0, 120, 255)
        elif self.box.selected:
            color = QColor(0, 200, 0)
        else:
            color = QColor(200, 0, 0)

        self.setPen(QPen(color, 2))

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        r: QRectF = self.rect()
        p = event.pos()

        self.resize_dir = None

        if abs(p.x()) < HANDLE and abs(p.y()) < HANDLE:
            self.resize_dir = "tl"
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif abs(p.x() - r.width()) < HANDLE and abs(p.y()) < HANDLE:
            self.resize_dir = "tr"
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif abs(p.x()) < HANDLE and abs(p.y() - r.height()) < HANDLE:
            self.resize_dir = "bl"
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif abs(p.x() - r.width()) < HANDLE and abs(p.y() - r.height()) < HANDLE:
            self.resize_dir = "br"
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self.resize_dir:
            self.resizing = True
            self.start_rect = QRectF(self.rect())
            self.start_pos = event.pos()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self.resizing:
            dx: float = event.pos().x() - self.start_pos.x()
            dy: float = event.pos().y() - self.start_pos.y()
            r: QRectF = QRectF(self.start_rect)

            if "l" in self.resize_dir:
                r.setLeft(r.left() + dx)
            if "r" in self.resize_dir:
                r.setRight(r.right() + dx)
            if "t" in self.resize_dir:
                r.setTop(r.top() + dy)
            if "b" in self.resize_dir:
                r.setBottom(r.bottom() + dy)

            self.setRect(r.normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        r: QRectF = self.rect()
        self.box.x = int(r.x())
        self.box.y = int(r.y())
        self.box.w = int(r.width())
        self.box.h = int(r.height())
        self.resizing = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.box.locked = not self.box.locked
        self.update_style()
