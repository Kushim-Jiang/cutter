from models.table import Table


class Editor:
    LINE_SEPARATOR = "*"

    @staticmethod
    def paste_operation(table: Table, start_row: int, col: int, paste_text: str) -> None:
        if not paste_text:
            return

        parts = paste_text.split()
        if not parts:
            return

        total_rows = len(table)
        max_row = total_rows - 1

        for i, part in enumerate(parts):
            current_row = start_row + i
            if current_row < max_row:
                table.set_cell(current_row, col, part)
            elif current_row == max_row:
                remaining_parts = parts[i:]
                merged_text = Editor.LINE_SEPARATOR.join(remaining_parts)
                table.set_cell(current_row, col, merged_text)
                break

    @staticmethod
    def merge_operation(table: Table, current_row: int, col: int) -> None:
        total_rows = len(table)
        if total_rows < 2 or current_row >= total_rows - 1:
            return

        last_row = total_rows - 1
        penultimate_row = last_row - 1
        is_penultimate_row = current_row == penultimate_row

        last_row_text = table.get_cell(last_row, col)
        split_first = ""
        split_rest = ""

        if Editor.LINE_SEPARATOR in last_row_text:
            split_parts = last_row_text.split(Editor.LINE_SEPARATOR)
            split_first = split_parts[0]
            split_rest = Editor.LINE_SEPARATOR.join(split_parts[1:]) if len(split_parts) > 1 else ""
        else:
            split_first = last_row_text
            split_rest = ""

        if is_penultimate_row:
            current_text = table.get_cell(current_row, col)
            merged_text = current_text + split_first
            table.set_cell(current_row, col, merged_text)
            table.set_cell(last_row, col, split_rest)
        else:
            current_text = table.get_cell(current_row, col)
            next_text = table.get_cell(current_row + 1, col)
            merged_text = current_text + next_text
            table.set_cell(current_row, col, merged_text)

            for row in range(current_row + 1, penultimate_row):
                table.set_cell(row, col, table.get_cell(row + 1, col))

            table.set_cell(penultimate_row, col, split_first)
            table.set_cell(last_row, col, split_rest)

    @staticmethod
    def split_operation(table: Table, current_row: int, col: int, cursor_pos: int, current_text: str) -> None:
        total_rows = len(table)
        if current_row >= total_rows or cursor_pos >= len(current_text):
            return

        text_before_cursor = current_text[:cursor_pos]
        text_after_cursor = current_text[cursor_pos:]

        table.set_cell(current_row, col, text_before_cursor)

        last_row_text = table.get_cell(total_rows - 1, col)
        has_last_row_content = bool(last_row_text.strip())

        for row in range(total_rows - 1, current_row + 1, -1):
            table.set_cell(row, col, table.get_cell(row - 1, col))

        table.set_cell(current_row + 1, col, text_after_cursor)

        if has_last_row_content:
            new_last_text = table.get_cell(total_rows - 1, col)
            merged_last_text = new_last_text + Editor.LINE_SEPARATOR + last_row_text
            table.set_cell(total_rows - 1, col, merged_last_text)
