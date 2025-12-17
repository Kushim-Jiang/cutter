from typing import Optional

import cv2
import numpy as np

MAX_ROTATE_DEG: float = 20.0


def estimate_skew_angle(image: np.ndarray) -> Optional[float]:
    """
    Estimate skew angle of the image in degrees.
    Returns None if angle is unreliable.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=100,
        minLineLength=50,
        maxLineGap=10,
    )

    if lines is None:
        return None

    angles: list[float] = []

    for line in lines:
        x1, y1, x2, y2 = line[0]
        dx = x2 - x1
        dy = y2 - y1

        if dx == 0:
            angle = 90.0
        else:
            angle = np.degrees(np.arctan2(dy, dx))

        # Only keep lines close to horizontal or vertical
        if abs(angle) < 45:
            angles.append(angle)
        elif abs(abs(angle) - 90) < 45:
            angles.append(angle - 90 if angle > 0 else angle + 90)

    if not angles:
        return None

    median_angle = float(np.median(angles))

    if abs(median_angle) > MAX_ROTATE_DEG:
        return None

    return median_angle


def rotate_image(
    image: np.ndarray,
    angle_deg: float,
) -> np.ndarray:
    """
    Rotate image around center with border replication.
    """
    h, w = image.shape[:2]
    center = (w // 2, h // 2)

    m = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    rotated = cv2.warpAffine(
        image,
        m,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated


def auto_deskew(image: np.ndarray) -> np.ndarray:
    """
    Automatically deskew image within ±20 degrees.
    """
    angle = estimate_skew_angle(image)
    if angle is None:
        return image

    return rotate_image(image, -angle)
