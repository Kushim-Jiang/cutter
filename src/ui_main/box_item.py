from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSceneHoverEvent, QGraphicsSceneMouseEvent

from models.box import Box

HANDLE_SIZE: int = 6


class BoxItem(QGraphicsRectItem):
    box: Box
    resizing: bool
    resize_dir: Optional[str]
    start_rect: QRectF
    start_pos: QPointF

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
        color = QColor(0, 200, 0) if self.box.selected else QColor(200, 0, 0)
        self.setPen(QPen(color, 2))

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        pos = event.pos()
        rect = self.rect()
        cursor_shape = Qt.CursorShape.ArrowCursor
        resize_dir = None

        if abs(pos.x()) < HANDLE_SIZE and abs(pos.y()) < HANDLE_SIZE:
            resize_dir = "tl"
            cursor_shape = Qt.CursorShape.SizeFDiagCursor
        elif abs(pos.x() - rect.width()) < HANDLE_SIZE and abs(pos.y()) < HANDLE_SIZE:
            resize_dir = "tr"
            cursor_shape = Qt.CursorShape.SizeBDiagCursor
        elif abs(pos.x()) < HANDLE_SIZE and abs(pos.y() - rect.height()) < HANDLE_SIZE:
            resize_dir = "bl"
            cursor_shape = Qt.CursorShape.SizeBDiagCursor
        elif abs(pos.x() - rect.width()) < HANDLE_SIZE and abs(pos.y() - rect.height()) < HANDLE_SIZE:
            resize_dir = "br"
            cursor_shape = Qt.CursorShape.SizeFDiagCursor

        if resize_dir != self.resize_dir:
            self.resize_dir = resize_dir
            self.setCursor(cursor_shape)

        super().hoverMoveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self.resize_dir:
            self.resizing = True
            self.start_rect = QRectF(self.rect())
            self.start_pos = event.pos()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self.resizing and self.resize_dir:
            delta = event.pos() - self.start_pos
            r = QRectF(self.start_rect)

            if "l" in self.resize_dir:
                r.setLeft(r.left() + delta.x())
            if "r" in self.resize_dir:
                r.setRight(r.right() + delta.x())
            if "t" in self.resize_dir:
                r.setTop(r.top() + delta.y())
            if "b" in self.resize_dir:
                r.setBottom(r.bottom() + delta.y())

            self.setRect(r.normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        r = self.rect()
        self.box.x = int(r.x())
        self.box.y = int(r.y())
        self.box.w = int(r.width())
        self.box.h = int(r.height())
        self.resizing = False
        super().mouseReleaseEvent(event)


def sort_reading_order(box_items: list[BoxItem], image_width: int, column_count: int) -> list[BoxItem]:

    def sort_single_column(items: list[BoxItem], line_tol: int = 10) -> list[BoxItem]:
        entries = [(item, item.box.x, item.box.y + item.box.h) for item in items]
        entries.sort(key=lambda e: e[2])

        lines: list[list[tuple[BoxItem, int, int]]] = []
        for entry in entries:
            box_item, x, y = entry
            placed = False
            for line in lines:
                _, _, ly = line[0]
                if abs(y - ly) <= line_tol:
                    line.append(entry)
                    placed = True
                    break
            if not placed:
                lines.append([entry])

        result: list[BoxItem] = []
        for line in lines:
            line.sort(key=lambda e: e[1])
            result.extend(box for box, _, _ in line)

        return result

    col_width = image_width / column_count
    columns: list[list[BoxItem]] = [[] for _ in range(column_count)]

    for b in box_items:
        center_x = b.box.x + b.box.w / 2
        col_idx = min(int(center_x // col_width), column_count - 1)
        columns[col_idx].append(b)

    ordered: list[BoxItem] = []
    for col_boxes in columns:
        ordered.extend(sort_single_column(col_boxes))
    return ordered
