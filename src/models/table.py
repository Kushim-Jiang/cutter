from __future__ import annotations

from pathlib import Path


class Table:
    IMAGE_PATH_COL = 0
    IMAGE_COL = 1
    TEXT_COL_START = 2

    def __init__(self) -> None:
        self.cells: list[list[str]] = []

    # ----------------------------
    # structure helpers
    # ----------------------------

    def row_count(self) -> int:
        return len(self.cells)

    def column_count(self) -> int:
        if not self.cells:
            return Table.TEXT_COL_START
        return len(self.cells[0])

    def set_rows(self, count: int) -> None:
        """Resize row count."""
        self.cells = self.cells[:count]
        while len(self.cells) < count:
            self.cells.append(["", ""])  # path + image placeholder

    def set_columns(self, count: int) -> None:
        """Resize column count."""
        for i, row in enumerate(self.cells):
            self.cells[i] = row[:count]
            while len(self.cells[i]) < count:
                self.cells[i].append("")

    def ensure_cell(self, row: int, col: int) -> None:
        self.set_rows(row + 1)
        self.set_columns(col + 1)

    # ----------------------------
    # image operations
    # ----------------------------

    def set_image(self, row: int, path: Path) -> None:
        self.set_rows(row + 1)
        self.cells[row][Table.IMAGE_PATH_COL] = str(path)

    def get_image_path(self, row: int) -> str:
        if row >= self.row_count():
            return ""
        return self.cells[row][Table.IMAGE_PATH_COL]

    # ----------------------------
    # text access
    # ----------------------------

    def get_text(self, row: int, col: int) -> str:
        if row >= self.row_count():
            return ""
        if col >= len(self.cells[row]):
            return ""
        return self.cells[row][col]

    def set_text(self, row: int, col: int, text: str) -> None:
        self.ensure_cell(row, col)
        self.cells[row][col] = text

    # ----------------------------
    # editing logic
    # ----------------------------

    def split_cell(self, row: int, col: int, cursor: int) -> None:
        """Split a cell into current row + next row."""
        self.ensure_cell(row, col)

        text = self.cells[row][col]
        before = text[:cursor]
        after = text[cursor:]

        self.cells[row][col] = before

        # always ensure next row exists
        self.set_rows(self.row_count() + 1)
        self.set_columns(col + 1)

        # shift down
        for r in range(self.row_count() - 1, row + 1, -1):
            self.cells[r][col] = self.cells[r - 1][col]

        self.cells[row + 1][col] = after

    def merge_with_next(self, row: int, col: int) -> None:
        if row >= self.row_count() - 1:
            return

        self.ensure_cell(row + 1, col)

        self.cells[row][col] += self.cells[row + 1][col]

        for r in range(row + 1, self.row_count() - 1):
            self.cells[r][col] = self.cells[r + 1][col]

        self.cells[-1][col] = ""

    def paste_multiline(self, row: int, col: int, text: str) -> None:
        lines = text.splitlines()
        self.set_rows(row + len(lines))
        self.set_columns(col + 1)

        for i, line in enumerate(lines):
            self.cells[row + i][col] = line

    # ----------------------------
    # column operations
    # ----------------------------

    def insert_column(self, col: int) -> None:
        self.set_columns(col)
        for row in self.cells:
            row.insert(col, "")

    def remove_column(self, col: int) -> None:
        """Remove column but protect structure columns."""
        if col < Table.TEXT_COL_START:
            return

        for row in self.cells:
            if len(row) > col:
                row.pop(col)

    # ----------------------------
    # export
    # ----------------------------

    def export_tsv(self, path: Path) -> None:
        lines: list[str] = []

        for row in self.cells:
            if any(cell.strip() for cell in row):
                lines.append("\t".join(row))

        path.write_text("\n".join(lines), encoding="utf-8")
