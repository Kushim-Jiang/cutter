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
        if self.box.selected:
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


def sort_reading_order(box_items: list[BoxItem], image_width: int) -> list[BoxItem]:

    def sort_single_column(box_items: list[BoxItem], *, line_tol: int = 10) -> list[BoxItem]:
        items = [(b, b.box.x, b.box.y + b.box.h) for b in box_items]
        items.sort(key=lambda t: t[2])

        lines: list[list[tuple[BoxItem, int, int]]] = []
        for box_item, x, y in items:
            placed = False
            for line in lines:
                _, _, ly = line[0]
                if abs(y - ly) <= line_tol:
                    line.append((box_item, x, y))
                    placed = True
                    break
            if not placed:
                lines.append([(box_item, x, y)])

        result: list[BoxItem] = []
        for line in lines:
            line.sort(key=lambda t: t[1])
            result.extend(b for b, _, _ in line)

        return result

    mid_x = image_width / 2
    left = [b for b in box_items if b.box.x + b.box.w / 2 < mid_x]
    right = [b for b in box_items if b.box.x + b.box.w / 2 >= mid_x]

    ordered = []
    ordered.extend(sort_single_column(left))
    ordered.extend(sort_single_column(right))
    return ordered
