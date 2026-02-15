from pathlib import Path


class Table:
    IMG_COL = 0
    CHR_COL = 1
    CMT_COL = 2

    def __init__(self) -> None:
        # fixed 3 columns: [image, character, comment]
        self.cells: list[list[str]] = []

    def __len__(self) -> int:
        return len(self.cells)

    def clear(self) -> None:
        self.cells = []

    def add_row(self, image: str = "", character: str = "", comment: str = "") -> None:
        self.cells.append([image, character, comment])

    def set_row(self, row: int, image: str = "", character: str = "", comment: str = "") -> None:
        if 0 <= row < self.__len__():
            self.cells[row] = [image, character, comment]
        else:
            self.add_row(image, character, comment)

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
            self.add_row(image=str(path))

    def import_tsv(self, tsv_path: Path) -> None:
        self.clear()
        if not tsv_path.exists():
            raise FileNotFoundError(f"tsv file not found: {tsv_path}")

        with open(tsv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:
            parts = line.strip().split("\t")
            image = parts[self.IMG_COL] if len(parts) > self.IMG_COL else ""
            character = parts[self.CHR_COL] if len(parts) > self.CHR_COL else ""
            comment = parts[self.CMT_COL] if len(parts) > self.CMT_COL else ""
            self.add_row(image, character, comment)

    def export_tsv(self, export_path: Path) -> None:
        lines = []
        for row in self.cells:
            processed_cells = []
            for cell in row:
                processed = cell.replace("\t", ">").replace("\n", ">>")
                processed_cells.append(processed)
            lines.append("\t".join(processed_cells))
        export_path.write_text("\n".join(lines), encoding="utf-8")
