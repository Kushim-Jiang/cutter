from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QObject, Qt
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

from models.table import Table as TableModel
from ui_table.image_cell import ROW_HEIGHT, ImageCellWidget
from ui_table.table_edit import Editor as TableEditor


class TextTableDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Labeling Table")
        self.resize(1000, 600)

        self.table_model = TableModel()
        self.table_widget = self._create_table_widget()

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
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Images", "Characters", "Comments"])
        table.setRowCount(0)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, ROW_HEIGHT + 10)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        return table

    def _init_table_row(self, row: int) -> None:
        self.table_widget.setRowHeight(row, ROW_HEIGHT)

        image_cell = ImageCellWidget(self.table_widget)
        image_cell.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table_widget.setCellWidget(row, TableModel.IMG_COL, image_cell)
        placeholder_item = QTableWidgetItem()
        placeholder_item.setFlags(placeholder_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
        self.table_widget.setItem(row, TableModel.IMG_COL, placeholder_item)

        char_edit = QLineEdit()
        char_edit.setFrame(False)
        char_edit.setStyleSheet("font-size: 22px;")
        char_edit.installEventFilter(self)
        char_edit.textChanged.connect(lambda text, r=row, c=TableModel.CHR_COL: self.table_model.set_cell(r, c, text))
        self.table_widget.setCellWidget(row, TableModel.CHR_COL, char_edit)

        comment_edit = QLineEdit()
        comment_edit.setFrame(False)
        comment_edit.installEventFilter(self)
        comment_edit.textChanged.connect(lambda text, r=row, c=TableModel.CMT_COL: self.table_model.set_cell(r, c, text))
        self.table_widget.setCellWidget(row, TableModel.CMT_COL, comment_edit)

    def sync_table_view(self) -> None:
        self.table_widget.setRowCount(0)
        row_count = len(self.table_model)
        self.table_widget.setRowCount(row_count)

        for row in range(row_count):
            self._init_table_row(row)

            image_path = self.table_model.get_cell(row, TableModel.IMG_COL)
            image_cell = cast(ImageCellWidget, self.table_widget.cellWidget(row, TableModel.IMG_COL))
            image_cell.set_image(image_path)

            char_text = self.table_model.get_cell(row, TableModel.CHR_COL)
            char_edit = cast(QLineEdit, self.table_widget.cellWidget(row, TableModel.CHR_COL))
            char_edit.setText(char_text)

            comment_text = self.table_model.get_cell(row, TableModel.CMT_COL)
            comment_edit = cast(QLineEdit, self.table_widget.cellWidget(row, TableModel.CMT_COL))
            comment_edit.setText(comment_text)

    def _get_edit_widget_position(self, edit_widget: QLineEdit) -> tuple[int, int]:
        for row in range(self.table_widget.rowCount()):
            for col in [TableModel.CHR_COL, TableModel.CMT_COL]:
                widget = self.table_widget.cellWidget(row, col)
                if widget == edit_widget:
                    return row, col
        return -1, -1

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(obj, QLineEdit) and event.type() == QEvent.Type.KeyPress:
            current_row, current_col = self._get_edit_widget_position(obj)
            if current_col not in [TableModel.CHR_COL, TableModel.CMT_COL]:
                return super().eventFilter(obj, event)

            event = cast(QKeyEvent, event)

            # ctrl + v: paste
            if event.matches(QKeySequence.StandardKey.Paste):
                clipboard_text = QApplication.clipboard().text()
                TableEditor.paste_operation(self.table_model, current_row, current_col, clipboard_text)
                self.sync_table_view()
                obj.setFocus()
                obj.setCursorPosition(len(obj.text()))
                return True

            # ctrl + x: merge
            elif event.matches(QKeySequence.StandardKey.Cut):
                TableEditor.merge_operation(self.table_model, current_row, current_col)
                self.sync_table_view()
                new_edit = cast(QLineEdit, self.table_widget.cellWidget(current_row, current_col))
                if new_edit:
                    new_edit.setFocus()
                    new_edit.setCursorPosition(len(new_edit.text()))
                return True

            # ctrl + c: split
            elif event.matches(QKeySequence.StandardKey.Copy):
                cursor_pos = obj.cursorPosition()
                current_text = obj.text()
                TableEditor.split_operation(self.table_model, current_row, current_col, cursor_pos, current_text)
                self.sync_table_view()
                new_edit = cast(QLineEdit, self.table_widget.cellWidget(current_row, current_col))
                if new_edit:
                    new_edit.setFocus()
                    new_edit.setCursorPosition(0)
                return True

            # ctrl + s: save
            elif event.matches(QKeySequence.StandardKey.Save):
                self.export_tsv()
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
