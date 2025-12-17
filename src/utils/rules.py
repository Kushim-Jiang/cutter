from models.box import Box


def apply_rules(boxes: list[Box], w: tuple[int, int], h: tuple[int, int]) -> None:
    """
    Apply width / height rules to boxes.
    Locked boxes are not modified.
    """
    for box in boxes:
        if box.locked:
            continue
        box.selected = w[0] <= box.w <= w[1] and h[0] <= box.h <= h[1]
