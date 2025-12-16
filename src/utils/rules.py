from models.box import Box


def apply_rules(
    boxes: list[Box],
    w: tuple[int, int],
    h: tuple[int, int],
    ratio: tuple[float, float],
) -> None:
    """
    Apply width / height / aspect-ratio rules to boxes.
    Locked boxes are not modified.
    """
    for box in boxes:
        if box.locked:
            continue

        r: float = box.w / box.h if box.h > 0 else 0.0

        box.selected = w[0] <= box.w <= w[1] and h[0] <= box.h <= h[1] and ratio[0] <= r <= ratio[1]
