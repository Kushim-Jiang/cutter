from __future__ import annotations

from pathlib import Path
from typing import Iterable, cast

from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from models.table import Table
from ui_table.image_cell import ROW_HEIGHT, ImageCellWidget


class TextTableDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Text Table")
        self.resize(900, 600)

        self.table = EditableTable(self)

        add_images_btn = QPushButton("Add Images")
        export_btn = QPushButton("Export TSV")

        add_images_btn.clicked.connect(self.add_images)
        export_btn.clicked.connect(self.export_tsv)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(add_images_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(export_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(btn_layout)
        layout.addWidget(self.table)

    def add_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select Images", "", "Images (*.png *.jpg *.jpeg)")
        if files:
            self.table.insert_images([Path(f) for f in files])

    def export_tsv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export TSV", "", "TSV (*.tsv)")
        if path:
            self.table.data_model.export_tsv(Path(path))


class EditableTable(QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.data_model = Table()

        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Image Path", "Image"])
        self.setRowCount(1)

        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self._init_row(0)
        self.installEventFilter(self)

    def _init_row(self, row: int) -> None:
        self.setRowHeight(row, ROW_HEIGHT)

        item = QTableWidgetItem("")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.setItem(row, Table.IMAGE_PATH_COL, item)

        img = ImageCellWidget(self)
        img.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCellWidget(row, Table.IMAGE_COL, img)

    # ----------------------------
    # UI sync helpers
    # ----------------------------

    def _ensure_text_column(self, col: int) -> None:
        if col >= self.columnCount():
            self.setColumnCount(col + 1)
            self.setHorizontalHeaderItem(col, QTableWidgetItem(f"Text {col - 1}"))

    def _set_text_cell(self, row: int, col: int, text: str = "") -> None:
        self._ensure_text_column(col)
        edit = QLineEdit(text)
        edit.setFrame(False)
        edit.installEventFilter(self)
        self.setCellWidget(row, col, edit)

    def sync_from_model(self) -> None:
        self.setRowCount(self.data_model.row_count())

        for r in range(self.data_model.row_count()):
            self._init_row(r)
            for c in range(self.data_model.column_count()):
                if c != Table.IMAGE_COL:
                    self._set_text_cell(r, c, self.data_model.get_text(r, c))

            image_cell = cast(ImageCellWidget, self.cellWidget(r, Table.IMAGE_COL))
            if image_cell:
                image_path = self.data_model.get_image_path(r)
                image_cell.set_image(Path(image_path))

    # picture batch insert

    def insert_images(self, paths: Iterable[Path]) -> None:
        paths = sorted(paths, key=lambda p: p.name)
        start_row = max(self.currentRow(), 0)

        for i, path in enumerate(paths):
            self.data_model.set_image(start_row + i, path)
        self.sync_from_model()

    # ----------------------------
    # keyboard behavior
    # ----------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(event, QKeyEvent):
            self.keyPressEvent(event)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        row, col = self.currentRow(), self.currentColumn()
        widget = cast(QLineEdit, self.cellWidget(row, col))

        # ctrl + V: paste multiline text
        if event.matches(QKeySequence.StandardKey.Paste) and col >= Table.TEXT_COL_START:
            self.data_model.paste_multiline(row, col, QApplication.clipboard().text())
            self.sync_from_model()
            return

        # Ctrl + Right: insert column
        if event.matches(QKeySequence.StandardKey.MoveToNextWord) and col >= Table.IMAGE_COL:
            self.data_model.insert_column(col + 1)
            self.sync_from_model()
            return

        # Ctrl + Left: remove column
        if event.matches(QKeySequence.StandardKey.MoveToPreviousWord) and col >= Table.TEXT_COL_START:
            self.data_model.remove_column(col)
            self.sync_from_model()
            return

        # Enter: split cell
        if event.matches(QKeySequence.StandardKey.InsertParagraphSeparator) and col >= Table.TEXT_COL_START:
            self.data_model.split_cell(row, col, widget.cursorPosition())
            self.sync_from_model()
            self.setCurrentCell(row + 1, col)
            return

        # Delete: merge with previous
        if event.matches(QKeySequence.StandardKey.Delete) and col >= Table.TEXT_COL_START:
            before = widget.text()
            widget.del_()
            if widget.text() == before:
                self.data_model.merge_with_next(row, col)
                self.sync_from_model()
            return

        super().keyPressEvent(event)
