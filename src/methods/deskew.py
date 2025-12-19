import cv2
import numpy as np

ANGLE_LIMIT = 45


def rotate(image: np.ndarray, angle: float) -> np.ndarray:
    h, w = image.shape[:2]
    center = (w / 2.0, h / 2.0)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def normalize_angle(angle: float) -> float:
    if angle < -ANGLE_LIMIT:
        angle += 90
    elif angle > ANGLE_LIMIT:
        angle -= 90
    return angle


def get_angle(rectangles: list[tuple[float, tuple[tuple[float, float], tuple[float, float], float]]]) -> float:
    angles = sorted([normalize_angle(rect[-1]) for _, rect in rectangles])
    trim_count = int(len(angles) * 0.5)
    trimmed_angles = angles[trim_count:-trim_count]
    return sum(trimmed_angles) / len(trimmed_angles) if trimmed_angles else 0.0


def auto_deskew(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        return image

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 and image.shape[2] == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image

    rectangles = []
    for contour in contours:
        if cv2.contourArea(contour) < 10:
            continue
        rectangle = cv2.minAreaRect(contour)
        width, height = rectangle[1]
        if width == 0 or height == 0:
            continue
        rectangles.append((width / height, rectangle))
    if len(rectangles) < 2:
        return image

    rectangles.sort(key=lambda x: x[0])
    angle = get_angle(rectangles)
    return rotate(image, angle)
