from pathlib import Path


class Table:
    # model column indices (0-based): image, character, comment
    IMG = 0
    CHR = 1
    CMT = 2

    def __init__(self) -> None:
        # fixed 3 columns: [image, character, comment]
        self.cells: list[list[str]] = []

    def __len__(self) -> int:
        return len(self.cells)

    def clear(self) -> None:
        self.cells = []

    def append_row(self, image: str = "", character: str = "", comment: str = "") -> None:
        self.cells.append([image, character, comment])

    def set_row(self, row: int, image: str = "", character: str = "", comment: str = "") -> None:
        if 0 <= row < self.__len__():
            self.cells[row] = [image, character, comment]
        else:
            self.append_row(image, character, comment)

    def get_cell(self, row: int, col: int) -> str:
        if 0 <= row < self.__len__() and 0 <= col < 3:
            return self.cells[row][col]
        return ""

    def set_cell(self, row: int, col: int, value: str) -> None:
        if 0 <= row < self.__len__() and 0 <= col < 3:
            self.cells[row][col] = value

    def import_images(self, paths: list[Path]) -> None:
        self.clear()
        sorted_paths = sorted(paths, key=lambda p: p.name)
        for path in sorted_paths:
            self.append_row(image=str(path))

    def import_tsv(self, tsv_path: Path) -> None:
        self.clear()
        if not tsv_path.exists():
            raise FileNotFoundError(f"tsv file not found: {tsv_path}")

        with open(tsv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:
            parts = line.strip().split("\t")
            image = parts[self.IMG] if len(parts) > self.IMG else ""
            character = parts[self.CHR] if len(parts) > self.CHR else ""
            comment = parts[self.CMT] if len(parts) > self.CMT else ""
            self.append_row(image, character, comment)

    def export_tsv(self, export_path: Path) -> None:
        lines = []
        for row in self.cells:
            processed_cells = []
            for cell in row:
                processed = cell.replace("\t", ">").replace("\n", ">>")
                processed_cells.append(processed)
            lines.append("\t".join(processed_cells))
        export_path.write_text("\n".join(lines), encoding="utf-8")

    def swap_rows(self, a: int, b: int) -> None:
        """Swap two rows in-place if both indices are valid."""
        if a == b:
            return
        if 0 <= a < self.__len__() and 0 <= b < self.__len__():
            self.cells[a], self.cells[b] = self.cells[b], self.cells[a]

    def swap_cells(self, row_a: int, col_a: int, row_b: int, col_b: int) -> None:
        """Swap contents of two cells. Model columns are 0-based (IMG=0, CHR=1, CMT=2)."""
        if row_a == row_b and col_a == col_b:
            return

        if not (0 <= row_a < self.__len__() and 0 <= row_b < self.__len__()):
            return

        if not (0 <= col_a < 3 and 0 <= col_b < 3):
            return

        self.cells[row_a][col_a], self.cells[row_b][col_b] = self.cells[row_b][col_b], self.cells[row_a][col_a]
