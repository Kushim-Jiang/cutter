from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

TEXT_COL_START = 2
ROW_HEIGHT = 20


class ImageCellWidget(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(ROW_HEIGHT, ROW_HEIGHT)
        self._path: Path | None = None

    def set_image(self, path: Path | None) -> None:
        self._path = path
        if path and path.exists():
            pix = QPixmap(str(path))
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

        layout = QVBoxLayout()
        layout.addLayout(btn_layout)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def add_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select Images", "", "Images (*.png *.jpg *.jpeg)")
        if files:
            self.table.insert_images([Path(f) for f in files])

    def export_tsv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export TSV", "", "TSV (*.tsv)")
        if path:
            self.table.export_tsv(Path(path))


class EditableTable(QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

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
        self.setItem(row, 0, QTableWidgetItem(""))
        self.setCellWidget(row, 1, ImageCellWidget(self))

        for col in range(TEXT_COL_START, self.columnCount()):
            self._set_text_cell(row, col, "")

    def _ensure_text_column(self, col: int) -> None:
        if col >= self.columnCount():
            self.setColumnCount(col + 1)
            self.setHorizontalHeaderItem(col, QTableWidgetItem(f"Text {col - 1}"))

    def _set_text_cell(self, row: int, col: int, text: str = "") -> None:
        self._ensure_text_column(col)
        edit = QLineEdit(text)
        edit.setFrame(False)
        self.setCellWidget(row, col, edit)

    def eventFilter(self, obj: QTableWidget, event: QKeyEvent) -> bool:
        if event.type() == QKeyEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Paste):
                row, col = self.currentRow(), self.currentColumn()
                if col >= TEXT_COL_START:
                    self._paste_multiline(row, col)
                    return True
        return super().eventFilter(obj, event)

    # picture batch insert

    def insert_images(self, paths: Iterable[Path]) -> None:
        paths = sorted(paths, key=lambda p: p.name)
        start_row = max(self.currentRow(), 0)

        for i, path in enumerate(paths):
            row = start_row + i
            if row >= self.rowCount():
                self.insertRow(self.rowCount())
                self._init_row(self.rowCount() - 1)

            self.item(row, 0).setText(str(path))
            img_cell: ImageCellWidget = self.cellWidget(row, 1)
            img_cell.set_image(path)

    # ----------------------------
    # keyboard behavior
    # ----------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        row, col = self.currentRow(), self.currentColumn()
        widget = self.cellWidget(row, col)

        # Ctrl + V : paste multiline text
        if event.matches(QKeySequence.StandardKey.Paste) and col >= TEXT_COL_START:
            self._paste_multiline(row, col)
            return

        # Shift + Enter: insert column
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            if col >= 1:
                self.insert_text_column(col + 1)
            return

        # Shift + Delete: remove column
        if event.key() == Qt.Key.Key_Delete and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if col >= TEXT_COL_START:
                self.remove_text_column(col)
            return

        # Enter
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if col >= TEXT_COL_START:
                self._split_text_cell(row, col)
            return

        # Delete
        if event.key() == Qt.Key.Key_Delete and isinstance(widget, QLineEdit) and col >= TEXT_COL_START:
            self._delete_in_text_cell(row, col, widget)
            return

        super().keyPressEvent(event)

    # ----------------------------
    # text editing logic
    # ----------------------------

    def _split_text_cell(self, row: int, col: int) -> None:
        widget: QLineEdit = self.cellWidget(row, col)
        text = widget.text()
        cursor = widget.cursorPosition()

        before = text[:cursor]
        after = text[cursor:]
        widget.setText(before)

        # if current cell is the last cell in the column, and it has text, then we need to insert a new row
        last_row = self.rowCount() - 1
        last_widget = self.cellWidget(last_row, col)
        if isinstance(last_widget, QLineEdit) and last_widget.text():
            self.insertRow(self.rowCount())
            self._init_row(self.rowCount() - 1)

        # current column downshift (only current column)
        for r in range(self.rowCount() - 1, row + 1, -1):
            src = self.cellWidget(r - 1, col)
            if isinstance(src, QLineEdit):
                self._set_text_cell(r, col, src.text())

        self._set_text_cell(row + 1, col, after)
        self.setCurrentCell(row + 1, col)

    def _delete_in_text_cell(self, row: int, col: int, widget: QLineEdit) -> None:
        before_text = widget.text()
        cursor = widget.cursorPosition()

        widget.del_()  # let QLineEdit do the default delete
        after_text = widget.text()
        if after_text != before_text:  # if content changed, it's a normal delete, just end
            widget.setCursorPosition(cursor)
            return

        # if content not changed, it means cursor is at the end, try to merge with next cell
        if row >= self.rowCount() - 1:
            return
        next_widget = self.cellWidget(row + 1, col)
        if not isinstance(next_widget, QLineEdit):
            return
        merged = before_text + next_widget.text()
        widget.setText(merged)
        widget.setCursorPosition(len(before_text))

        # current column leftshift (only current column)
        for r in range(row + 1, self.rowCount() - 1):
            src = self.cellWidget(r + 1, col)
            if isinstance(src, QLineEdit):
                self._set_text_cell(r, col, src.text())

        # clear last cell if it's empty
        last_widget = self.cellWidget(self.rowCount() - 1, col)
        if isinstance(last_widget, QLineEdit):
            last_widget.setText("")

    def _paste_multiline(self, row: int, col: int) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return

        lines = text.splitlines()
        if not lines:
            return

        needed = row + len(lines)
        while self.rowCount() < needed:
            self.insertRow(self.rowCount())
            self._init_row(self.rowCount() - 1)

        for i, line in enumerate(lines):
            self._set_text_cell(row + i, col, line)

        self.setCurrentCell(row + len(lines) - 1, col)

    # ----------------------------
    # column operations
    # ----------------------------

    def insert_text_column(self, col: int) -> None:
        if col < TEXT_COL_START:
            return
        self.insertColumn(col)
        self.setHorizontalHeaderItem(col, QTableWidgetItem(f"Text {col - 1}"))
        for row in range(self.rowCount()):
            self._set_text_cell(row, col, "")

    def remove_text_column(self, col: int) -> None:
        if col < TEXT_COL_START:
            return
        self.removeColumn(col)

    # export the table content to a TSV file

    def export_tsv(self, path: Path) -> None:
        lines: list[str] = []

        for row in range(self.rowCount()):
            path_item = self.item(row, 0)
            if not path_item:
                continue

            fields = [path_item.text()]

            for col in range(TEXT_COL_START, self.columnCount()):
                widget = self.cellWidget(row, col)
                fields.append(widget.text() if isinstance(widget, QLineEdit) else "")

            if any(f.strip() for f in fields):
                lines.append("\t".join(fields))

        path.write_text("\n".join(lines), encoding="utf-8")
