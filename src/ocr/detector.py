from pathlib import Path
from typing import Optional, Tuple, List
from models.box import Box

from PIL import Image, ImageFilter
import numpy as np


def adaptive_threshold(image: Image.Image, block_size: int = 15, c: int = 10) -> Image.Image:
    img_np = np.array(image)
    mean = Image.fromarray(img_np).filter(ImageFilter.BoxBlur(block_size // 2))
    mean_np = np.array(mean)
    thresh_np = (img_np < (mean_np - c)).astype(np.uint8) * 255
    return Image.fromarray(thresh_np).convert("1")


def detect_text_regions(image_path: Path) -> List[Box]:
    image = Image.open(image_path).convert("L")
    bw = image.point(lambda x: 0 if x < 128 else 255, "1")
    bw_np = np.array(bw, dtype=np.uint8)  # 0 or 255

    height, width = bw_np.shape

    visited = np.zeros((height, width), dtype=bool)
    boxes = []

    def neighbors(x: int, y: int):
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                yield nx, ny

    def bfs(start_x: int, start_y: int):
        queue = [(start_x, start_y)]
        visited[start_y, start_x] = True

        min_x, max_x = start_x, start_x
        min_y, max_y = start_y, start_y

        while queue:
            cx, cy = queue.pop(0)
            for nx, ny in neighbors(cx, cy):
                if bw_np[ny, nx] == 0 and not visited[ny, nx]:
                    visited[ny, nx] = True
                    queue.append((nx, ny))
                    min_x = min(min_x, nx)
                    max_x = max(max_x, nx)
                    min_y = min(min_y, ny)
                    max_y = max(max_y, ny)

        return min_x, min_y, max_x, max_y

    def check_white_border_fast(x0, y0, x1, y1, border_width=5):
        # 检查边界范围是否越界
        if x0 - border_width < 0 or y0 - border_width < 0 or x1 + border_width >= width or y1 + border_width >= height:
            return False

        # 上边界
        if np.any(bw_np[y0 - border_width : y0, x0 - border_width : x1 + border_width + 1] == 0):
            return False
        # 下边界
        if np.any(bw_np[y1 + 1 : y1 + border_width + 1, x0 - border_width : x1 + border_width + 1] == 0):
            return False
        # 左边界
        if np.any(bw_np[y0 : y1 + 1, x0 - border_width : x0] == 0):
            return False
        # 右边界
        if np.any(bw_np[y0 : y1 + 1, x1 + 1 : x1 + border_width + 1] == 0):
            return False

        return True

    border_width = 10

    for y in range(height):
        for x in range(width):
            if bw_np[y, x] == 0 and not visited[y, x]:
                min_x, min_y, max_x, max_y = bfs(x, y)

                # 尝试一次性扩展矩形，直到外围 10 像素是白色
                expanded = True
                while expanded:
                    expanded = False

                    # 计算尝试扩展后的坐标（注意不能越界）
                    new_min_x = max(min_x - border_width, 0)
                    new_max_x = min(max_x + border_width, width - 1)
                    new_min_y = max(min_y - border_width, 0)
                    new_max_y = min(max_y + border_width, height - 1)

                    if new_min_x < min_x:
                        if not check_white_border_fast(new_min_x, min_y, max_x, max_y, border_width):
                            min_x = new_min_x
                            expanded = True
                    if new_max_x > max_x:
                        if not check_white_border_fast(min_x, min_y, new_max_x, max_y, border_width):
                            max_x = new_max_x
                            expanded = True
                    if new_min_y < min_y:
                        if not check_white_border_fast(min_x, new_min_y, max_x, max_y, border_width):
                            min_y = new_min_y
                            expanded = True
                    if new_max_y > max_y:
                        if not check_white_border_fast(min_x, min_y, max_x, new_max_y, border_width):
                            max_y = new_max_y
                            expanded = True

                    # 这里最多扩展一次，或者也可以直接赋值扩展完成

                w = max_x - min_x + 1
                h = max_y - min_y + 1

                if w > 5 and h > 5:
                    boxes.append(Box(min_x, min_y, w, h))

    return boxes


def filter_boxes(
    boxes: List[Box],
    w_range: Optional[Tuple[int, int]] = None,
    h_range: Optional[Tuple[int, int]] = None,
    ratio_range: Optional[Tuple[float, float]] = None,
) -> List[Box]:
    filtered = []
    for box in boxes:
        keep = True
        if w_range is not None:
            keep = keep and (w_range[0] <= box.w <= w_range[1])
        if h_range is not None:
            keep = keep and (h_range[0] <= box.h <= h_range[1])
        if ratio_range is not None and box.h != 0:
            ratio = box.w / box.h
            keep = keep and (ratio_range[0] <= ratio <= ratio_range[1])
        if keep:
            filtered.append(box)
    return filtered
