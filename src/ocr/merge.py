from models.box import Box


def merge_boxes(boxes: list[Box]) -> list[Box]:
    """
    Merge overlapping boxes likely belonging to the same character.
    """
    merged: list[Box] = []
    used: list[bool] = [False] * len(boxes)

    for i, a in enumerate(boxes):
        if used[i]:
            continue

        x, y, w, h = a.x, a.y, a.w, a.h

        for j, b in enumerate(boxes):
            if i == j or used[j]:
                continue

            overlap_y: int = min(y + h, b.y + b.h) - max(y, b.y)
            if overlap_y <= 0:
                continue

            overlap_ratio: float = overlap_y / min(h, b.h)

            if overlap_ratio > 0.6:
                nx: int = min(x, b.x)
                ny: int = min(y, b.y)
                nw: int = max(x + w, b.x + b.w) - nx
                nh: int = max(y + h, b.y + b.h) - ny

                x, y, w, h = nx, ny, nw, nh
                used[j] = True

        used[i] = True
        merged.append(Box(x=x, y=y, w=w, h=h))

    return merged
