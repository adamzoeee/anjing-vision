"""尺度标定：A4 纸（0.21 x 0.297m）为参照物换算全局尺度；门高先验兜底。

相机模型: pixel_len = focal * physical_len / distance  →  distance = focal * physical / pixel。
标定物到相机的距离由 SFM 位姿给出，故可从像素尺寸反推每单位长度对应的真实米数。
"""
import numpy as np

A4_SHORT = 0.210  # m
A4_LONG = 0.297   # m
DOOR_STANDARD_HEIGHT = 2.0  # m


def compute_scale_from_pixel(pixel_len: float, physical_len: float, distance: float, focal: float) -> float:
    """由已知物理长度物体在成像中的像素长度，求尺度因子 (米/单位)。

    distance 为相机到物体距离（相机单位）。返回米/单位。
    """
    if pixel_len <= 0 or distance <= 0 or focal <= 0:
        raise ValueError("pixel_len/distance/focal 必须为正")
    depth_units = focal * physical_len / pixel_len   # 物体到相机的深度（相机单位）
    return distance / depth_units                    # 米/单位


def scale_from_door_prior(door_height_units: float, standard_height: float = DOOR_STANDARD_HEIGHT) -> float:
    """门高先验兜底：标准门高 2.0m / 点云测得的门高(单位)。"""
    if door_height_units <= 0:
        raise ValueError("door_height_units 必须为正")
    return standard_height / door_height_units


def detect_a4_in_image(rgb: np.ndarray, mask: np.ndarray | None = None) -> tuple[float, float] | None:
    """在图片中检测 A4 纸（白纸矩形），返回 (长边像素, 短边像素)；找不到返回 None。

    实现：转灰度 → 自适应阈值 → 轮廓 → 多边形逼近找 4 点凸四边形 →
    按宽高比接近 sqrt(2) 且面积足够大者判为 A4，取其外接矩形长宽（像素）。
    若传入 mask（SAM 分割出的"纸/文档"区域），仅在该区域内检测。
    """
    import cv2

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if mask is not None:
        gray = cv2.bitwise_and(gray, gray, mask=mask.astype(np.uint8) * 255)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for cnt in contours:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) not in (4, 5, 6):
            continue
        rect = cv2.minAreaRect(cnt)
        (w, h) = rect[1]
        if min(w, h) < 30:
            continue
        ratio = max(w, h) / min(w, h)
        if 1.2 < ratio < 1.8:  # A4 = sqrt(2) ≈ 1.414
            if best is None or (w * h) > best[0] * best[1]:
                best = (max(w, h), min(w, h))
    return best
