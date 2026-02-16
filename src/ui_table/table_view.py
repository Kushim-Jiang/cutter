from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QObject
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
    QVBoxLayout,
)

from models.table import Table as TableModel
from ui_table.image_cell import ROW_HEIGHT
from ui_table.table_edit import Editor as TableEditor
from ui_table.visible_rows import RowManager


class TextTableDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Labeling Table")
        self.resize(1000, 600)

        self.table_model = TableModel()
        self.table_widget = self._create_table_widget()
        self._inited_rows: set[int] = set()

        self.import_images_btn = QPushButton("Import Images")
        self.import_tsv_btn = QPushButton("Import TSV")
        self.export_tsv_btn = QPushButton("Export TSV")

        self.import_images_btn.clicked.connect(self.import_images)
        self.import_tsv_btn.clicked.connect(self.import_tsv)
        self.export_tsv_btn.clicked.connect(self.export_tsv)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.import_images_btn)
        btn_layout.addWidget(self.import_tsv_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.export_tsv_btn)

        main_layout = QVBoxLayout(self)
        main_layout.addLayout(btn_layout)
        main_layout.addWidget(self.table_widget)

    def _create_table_widget(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(4)
        # first: row number (no title), second: image placeholder (no title)
        table.setHorizontalHeaderLabels(["", "", "Characters", "Comments"])
        table.setRowCount(0)

        header = table.horizontalHeader()
        # row-number column narrow (col 0)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(0, 40)
        # image column fixed to thumbnail height (model IMG_COL + 1)
        header.setSectionResizeMode(TableModel.IMG + 1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(TableModel.IMG + 1, ROW_HEIGHT + 10)
        header.setSectionResizeMode(TableModel.CHR + 1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(TableModel.CMT + 1, QHeaderView.ResizeMode.Stretch)

        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # update visible rows when scrolling or resizing
        table.verticalScrollBar().valueChanged.connect(lambda _: self._ensure_visible_rows())
        table.viewport().installEventFilter(self)
        return table

    def _ensure_visible_rows(self) -> None:
        RowManager.ensure_visible_rows(self.table_widget, self.table_model, self._inited_rows, self)

    def _get_edit_widget_position(self, edit_widget: QLineEdit) -> tuple[int, int]:
        for row in range(self.table_widget.rowCount()):
            widget = self.table_widget.cellWidget(row, TableModel.CHR + 1)
            if widget == edit_widget:
                return row, TableModel.CHR
            widget = self.table_widget.cellWidget(row, TableModel.CMT + 1)
            if widget == edit_widget:
                return row, TableModel.CMT
        return -1, -1

    def sync_table_view(self) -> None:
        RowManager.sync_table_view(self.table_widget, self.table_model, self._inited_rows, ROW_HEIGHT)
        self._ensure_visible_rows()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj == self.table_widget.viewport() and event.type() == QEvent.Type.Resize:
            self._ensure_visible_rows()
            return super().eventFilter(obj, event)

        if isinstance(obj, QLineEdit) and event.type() == QEvent.Type.KeyPress:
            current_row, current_col = self._get_edit_widget_position(obj)
            if current_col not in [TableModel.CHR, TableModel.CMT]:
                return super().eventFilter(obj, event)

            event = cast(QKeyEvent, event)

            # helper: map model col -> widget col
            def model_to_widget_col(mcol: int) -> int:
                return mcol + 1

            # ctrl + b: paste
            if event.matches(QKeySequence.StandardKey.Bold):
                clipboard_text = QApplication.clipboard().text()
                TableEditor.paste_operation(self.table_model, current_row, current_col, clipboard_text)
                self.sync_table_view()
                obj.setFocus()
                obj.setCursorPosition(len(obj.text()))
                return True

            # ctrl + i: merge
            elif event.matches(QKeySequence.StandardKey.Italic):
                TableEditor.merge_operation(self.table_model, current_row, current_col)
                self.sync_table_view()
                new_edit = cast(QLineEdit, self.table_widget.cellWidget(current_row, model_to_widget_col(current_col)))
                if new_edit:
                    new_edit.setFocus()
                    new_edit.setCursorPosition(len(new_edit.text()))
                return True

            # ctrl + u: split
            elif event.matches(QKeySequence.StandardKey.Underline):
                cursor_pos = obj.cursorPosition()
                current_text = obj.text()
                TableEditor.split_operation(self.table_model, current_row, current_col, cursor_pos, current_text)
                self.sync_table_view()
                new_edit = cast(QLineEdit, self.table_widget.cellWidget(current_row, model_to_widget_col(current_col)))
                if new_edit:
                    new_edit.setFocus()
                    new_edit.setCursorPosition(0)
                return True

            # ctrl + s: save
            elif event.matches(QKeySequence.StandardKey.Save):
                self.export_tsv()
                return True

            # Shift + Up: swap this cell with the one above (same column)
            elif event.matches(QKeySequence.StandardKey.SelectPreviousLine):
                if current_row > 0:
                    self.table_model.swap_cells(current_row, current_col, current_row - 1, current_col)
                    self.sync_table_view()
                    target_row = current_row - 1
                    new_edit = cast(QLineEdit, self.table_widget.cellWidget(target_row, model_to_widget_col(current_col)))
                    if new_edit:
                        new_edit.setFocus()
                        new_edit.setCursorPosition(len(new_edit.text()))
                return True

            # Shift + Down: swap this cell with the one below (same column)
            elif event.matches(QKeySequence.StandardKey.SelectNextLine):
                if current_row < len(self.table_model) - 1:
                    self.table_model.swap_cells(current_row, current_col, current_row + 1, current_col)
                    self.sync_table_view()
                    target_row = current_row + 1
                    new_edit = cast(QLineEdit, self.table_widget.cellWidget(target_row, model_to_widget_col(current_col)))
                    if new_edit:
                        new_edit.setFocus()
                        new_edit.setCursorPosition(len(new_edit.text()))
                return True

        return super().eventFilter(obj, event)

    def import_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select Images", "", "Images (*.png *.jpg *.jpeg)")
        if files:
            self.table_model.import_images([Path(f) for f in files])
            self.sync_table_view()
            self.import_images_btn.setEnabled(False)

    def import_tsv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Select TSV File", "", "TSV (*.tsv)")
        if file_path:
            try:
                self.table_model.import_tsv(Path(file_path))
                self.sync_table_view()
                self.import_images_btn.setEnabled(False)
            except Exception as e:
                print(f"Import TSV failed: {e}")

    def export_tsv(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(self, "Export TSV", "", "TSV (*.tsv)")
        if file_path:
            if not file_path.endswith(".tsv"):
                file_path += ".tsv"
            try:
                self.table_model.export_tsv(Path(file_path))
            except Exception as e:
                print(f"Export TSV failed: {e}")
