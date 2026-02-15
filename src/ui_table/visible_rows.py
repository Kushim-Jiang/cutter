from typing import Tuple, cast

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QLineEdit, QTableWidget, QTableWidgetItem

from models.table import Table as TableModel
from ui_table.image_cell import ROW_HEIGHT, ImageCellWidget


class RowManager:

    @classmethod
    def ensure_visible_rows(cls, table_widget: QTableWidget, table_model: TableModel, inited_rows: set[int], event_owner: QObject) -> None:
        start, end = cls.visible_row_range(table_widget)
        if end < start:
            return

        # create widgets for rows in visible range
        for row in range(start, end + 1):
            if row in inited_rows:
                # update contents in case model changed
                widget = table_widget.cellWidget(row, TableModel.IMG_COL)
                if widget:
                    image_path = table_model.get_cell(row, TableModel.IMG_COL)
                    cast(ImageCellWidget, widget).set_image(image_path)
                char_w = table_widget.cellWidget(row, TableModel.CHR_COL)
                if char_w:
                    cast(QLineEdit, char_w).setText(table_model.get_cell(row, TableModel.CHR_COL))
                cmt_w = table_widget.cellWidget(row, TableModel.CMT_COL)
                if cmt_w:
                    cast(QLineEdit, cmt_w).setText(table_model.get_cell(row, TableModel.CMT_COL))
                continue

            # replace simple items with live widgets
            # remove existing items first
            for col in [TableModel.IMG_COL, TableModel.CHR_COL, TableModel.CMT_COL]:
                item = table_widget.item(row, col)
                if item:
                    table_widget.takeItem(row, col)

            # initialize widgets for this row
            cls.init_table_row(table_widget, table_model, row, event_owner, ROW_HEIGHT)

            # populate data
            image_path = table_model.get_cell(row, TableModel.IMG_COL)
            image_cell = cast(ImageCellWidget, table_widget.cellWidget(row, TableModel.IMG_COL))
            image_cell.set_image(image_path)

            char_text = table_model.get_cell(row, TableModel.CHR_COL)
            char_edit = cast(QLineEdit, table_widget.cellWidget(row, TableModel.CHR_COL))
            char_edit.setText(char_text)

            comment_text = table_model.get_cell(row, TableModel.CMT_COL)
            comment_edit = cast(QLineEdit, table_widget.cellWidget(row, TableModel.CMT_COL))
            comment_edit.setText(comment_text)

            inited_rows.add(row)

        # remove widgets for rows outside visible range to free memory
        to_remove = [r for r in list(inited_rows) if r < start or r > end]
        for r in to_remove:
            for col in [TableModel.IMG_COL, TableModel.CHR_COL, TableModel.CMT_COL]:
                w = table_widget.cellWidget(r, col)
                if w:
                    table_widget.removeCellWidget(r, col)
                    w.deleteLater()

            # put back items to display current text
            img_placeholder = QTableWidgetItem()
            img_placeholder.setFlags(img_placeholder.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
            table_widget.setItem(r, TableModel.IMG_COL, img_placeholder)
            table_widget.setItem(r, TableModel.CHR_COL, QTableWidgetItem(table_model.get_cell(r, TableModel.CHR_COL)))
            table_widget.setItem(r, TableModel.CMT_COL, QTableWidgetItem(table_model.get_cell(r, TableModel.CMT_COL)))

            inited_rows.discard(r)

    @classmethod
    def init_table_row(
        cls, table: QTableWidget, table_model: TableModel, row: int, event_filter_owner: QObject, row_height: int = ROW_HEIGHT
    ) -> None:
        table.setRowHeight(row, row_height)

        # image widget
        image_cell = ImageCellWidget(table)
        image_cell.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setCellWidget(row, TableModel.IMG_COL, image_cell)
        placeholder_item = QTableWidgetItem()
        placeholder_item.setFlags(placeholder_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
        table.setItem(row, TableModel.IMG_COL, placeholder_item)

        # character editor
        char_edit = QLineEdit()
        char_edit.setFrame(False)
        char_edit.setStyleSheet("font-size: 22px;")
        char_edit.installEventFilter(event_filter_owner)
        char_edit.textChanged.connect(lambda text, r=row, c=TableModel.CHR_COL: table_model.set_cell(r, c, text))
        table.setCellWidget(row, TableModel.CHR_COL, char_edit)

        # comment editor
        comment_edit = QLineEdit()
        comment_edit.setFrame(False)
        comment_edit.installEventFilter(event_filter_owner)
        comment_edit.textChanged.connect(lambda text, r=row, c=TableModel.CMT_COL: table_model.set_cell(r, c, text))
        table.setCellWidget(row, TableModel.CMT_COL, comment_edit)

    @staticmethod
    def sync_table_view(table: QTableWidget, table_model: TableModel, inited_rows: set[int], row_height: int = ROW_HEIGHT) -> None:
        inited_rows.clear()
        table.setRowCount(0)
        row_count = len(table_model)
        table.setRowCount(row_count)

        for row in range(row_count):
            table.setRowHeight(row, row_height)

            # image placeholder (non-editable)
            placeholder_item = QTableWidgetItem()
            placeholder_item.setFlags(placeholder_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
            table.setItem(row, TableModel.IMG_COL, placeholder_item)

            # simple text items for non-visible rows; widgets will replace these when visible
            char_text = table_model.get_cell(row, TableModel.CHR_COL)
            char_item = QTableWidgetItem(char_text)
            table.setItem(row, TableModel.CHR_COL, char_item)

            comment_text = table_model.get_cell(row, TableModel.CMT_COL)
            comment_item = QTableWidgetItem(comment_text)
            table.setItem(row, TableModel.CMT_COL, comment_item)

    @classmethod
    def visible_row_range(cls, table: QTableWidget) -> Tuple[int, int]:
        if table.rowCount() == 0:
            return 0, -1

        top = table.rowAt(0)
        if top == -1:
            top = 0

        viewport_h = table.viewport().height()
        bottom = table.rowAt(viewport_h - 1)
        if bottom == -1:
            bottom = max(0, table.rowCount() - 1)

        return top, bottom
