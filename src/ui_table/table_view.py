from pathlib import Path
from typing import cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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
        self.setWindowTitle("Image Labeling Table")
        self.resize(1000, 600)

        self.table_model = Table()
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
        self.table_widget.setCellWidget(row, Table.IMG_COL, image_cell)
        placeholder_item = QTableWidgetItem()
        placeholder_item.setFlags(placeholder_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
        self.table_widget.setItem(row, Table.IMG_COL, placeholder_item)

        char_edit = QLineEdit()
        char_edit.setFrame(False)
        char_edit.textChanged.connect(lambda text, r=row, c=Table.CHR_COL: self.table_model.set_cell(r, c, text))
        self.table_widget.setCellWidget(row, Table.CHR_COL, char_edit)

        comment_edit = QLineEdit()
        comment_edit.setFrame(False)
        comment_edit.textChanged.connect(lambda text, r=row, c=Table.CMT_COL: self.table_model.set_cell(r, c, text))
        self.table_widget.setCellWidget(row, Table.CMT_COL, comment_edit)

    def sync_table_view(self) -> None:
        self.table_widget.setRowCount(0)
        row_count = len(self.table_model)
        self.table_widget.setRowCount(row_count)

        for row in range(row_count):
            self._init_table_row(row)

            image_path = self.table_model.get_cell(row, Table.IMG_COL)
            image_cell = cast(ImageCellWidget, self.table_widget.cellWidget(row, Table.IMG_COL))
            image_cell.set_image(image_path)

            char_text = self.table_model.get_cell(row, Table.CHR_COL)
            char_edit = cast(QLineEdit, self.table_widget.cellWidget(row, Table.CHR_COL))
            char_edit.setText(char_text)

            comment_text = self.table_model.get_cell(row, Table.CMT_COL)
            comment_edit = cast(QLineEdit, self.table_widget.cellWidget(row, Table.CMT_COL))
            comment_edit.setText(comment_text)

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
