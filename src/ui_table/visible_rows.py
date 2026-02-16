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
                # widget columns are model columns shifted by +1 (col 0 is row-number)
                img_col = TableModel.IMG + 1
                chr_col = TableModel.CHR + 1
                cmt_col = TableModel.CMT + 1

                # update contents in case model changed
                widget = table_widget.cellWidget(row, img_col)
                if widget:
                    image_path = table_model.get_cell(row, TableModel.IMG)
                    cast(ImageCellWidget, widget).set_image(image_path)
                char_w = table_widget.cellWidget(row, chr_col)
                if char_w:
                    cast(QLineEdit, char_w).setText(table_model.get_cell(row, TableModel.CHR))
                cmt_w = table_widget.cellWidget(row, cmt_col)
                if cmt_w:
                    cast(QLineEdit, cmt_w).setText(table_model.get_cell(row, TableModel.CMT))
                continue

            # replace simple items with live widgets
            # remove existing items first
            # remove existing items first (leave row-number at col 0)
            for col in [TableModel.IMG + 1, TableModel.CHR + 1, TableModel.CMT + 1]:
                item = table_widget.item(row, col)
                if item:
                    table_widget.takeItem(row, col)

            # initialize widgets for this row
            cls.init_table_row(table_widget, table_model, row, event_owner, ROW_HEIGHT)

            # populate data
            image_path = table_model.get_cell(row, TableModel.IMG)
            image_cell = cast(ImageCellWidget, table_widget.cellWidget(row, TableModel.IMG + 1))
            image_cell.set_image(image_path)

            char_text = table_model.get_cell(row, TableModel.CHR)
            char_edit = cast(QLineEdit, table_widget.cellWidget(row, TableModel.CHR + 1))
            char_edit.setText(char_text)

            comment_text = table_model.get_cell(row, TableModel.CMT)
            comment_edit = cast(QLineEdit, table_widget.cellWidget(row, TableModel.CMT + 1))
            comment_edit.setText(comment_text)

            inited_rows.add(row)

        # remove widgets for rows outside visible range to free memory
        to_remove = [r for r in list(inited_rows) if r < start or r > end]
        for r in to_remove:
            for col in [TableModel.IMG + 1, TableModel.CHR + 1, TableModel.CMT + 1]:
                w = table_widget.cellWidget(r, col)
                if w:
                    table_widget.removeCellWidget(r, col)
                    w.deleteLater()

            # put back items to display current text
            img_placeholder = QTableWidgetItem()
            img_placeholder.setFlags(img_placeholder.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
            table_widget.setItem(r, TableModel.IMG + 1, img_placeholder)
            table_widget.setItem(r, TableModel.CHR + 1, QTableWidgetItem(table_model.get_cell(r, TableModel.CHR)))
            table_widget.setItem(r, TableModel.CMT + 1, QTableWidgetItem(table_model.get_cell(r, TableModel.CMT)))

            inited_rows.discard(r)

    @classmethod
    def init_table_row(
        cls, table: QTableWidget, table_model: TableModel, row: int, event_filter_owner: QObject, row_height: int = ROW_HEIGHT
    ) -> None:
        table.setRowHeight(row, row_height)

        # row-number item (no header/title) at column 0
        row_item = QTableWidgetItem(str(row + 1))
        row_item.setFlags(row_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
        table.setItem(row, 0, row_item)

        # image widget
        image_cell = ImageCellWidget(table)
        image_cell.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setCellWidget(row, TableModel.IMG + 1, image_cell)
        placeholder_item = QTableWidgetItem()
        placeholder_item.setFlags(placeholder_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
        table.setItem(row, TableModel.IMG + 1, placeholder_item)

        # character editor
        char_edit = QLineEdit()
        char_edit.setFrame(False)
        char_edit.setStyleSheet("font-size: 22px;")
        char_edit.installEventFilter(event_filter_owner)
        char_edit.textChanged.connect(lambda text, r=row, c=TableModel.CHR: table_model.set_cell(r, c, text))
        table.setCellWidget(row, TableModel.CHR + 1, char_edit)

        # comment editor
        comment_edit = QLineEdit()
        comment_edit.setFrame(False)
        comment_edit.installEventFilter(event_filter_owner)
        comment_edit.textChanged.connect(lambda text, r=row, c=TableModel.CMT: table_model.set_cell(r, c, text))
        table.setCellWidget(row, TableModel.CMT + 1, comment_edit)

    @staticmethod
    def sync_table_view(table: QTableWidget, table_model: TableModel, inited_rows: set[int], row_height: int = ROW_HEIGHT) -> None:
        inited_rows.clear()
        table.setRowCount(0)
        row_count = len(table_model)
        table.setRowCount(row_count)

        for row in range(row_count):
            table.setRowHeight(row, row_height)

            # row-number
            row_item = QTableWidgetItem(str(row + 1))
            row_item.setFlags(row_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
            table.setItem(row, 0, row_item)

            # image placeholder (non-editable)
            placeholder_item = QTableWidgetItem()
            placeholder_item.setFlags(placeholder_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
            table.setItem(row, TableModel.IMG + 1, placeholder_item)

            # simple text items for non-visible rows; widgets will replace these when visible
            char_text = table_model.get_cell(row, TableModel.CHR)
            char_item = QTableWidgetItem(char_text)
            table.setItem(row, TableModel.CHR + 1, char_item)

            comment_text = table_model.get_cell(row, TableModel.CMT)
            comment_item = QTableWidgetItem(comment_text)
            table.setItem(row, TableModel.CMT + 1, comment_item)

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
