import logging

import cv2
import numpy as np

from config.settings import TEMPLATES_DIR

logger = logging.getLogger(__name__)


_missing_cache = set()
_hgram_cache = {}
_color_registry = {}
_flip_registry = set()


def _compute_hgram(img):
    hgram = []
    for i in range(3):
        hist = cv2.calcHist([img], [i], None, [64], [0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        hgram.append(hist)
    return hgram


def _compare_hgram(h1, h2):
    corr = 0.0
    for i in range(3):
        corr += cv2.compareHist(h1[i], h2[i], cv2.HISTCMP_CORREL)
    return corr / 3.0


def find_template(
    frame,
    template_name,
    threshold=0.8,
    scale_range=(0.7, 1.35),
    scale_steps=7,
    roi=None,
    color_threshold=0.0,
    auto_update=False,
):
    if frame is None:
        return None
    if scale_steps < 1:
        scale_steps = 1
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        if template_name not in _missing_cache:
            _missing_cache.add(template_name)
            logger.warning("模板文件未找到: %s", template_path)
        return None
    data = np.fromfile(str(template_path), dtype=np.uint8)
    template = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if template is None:
        return None
    t_h, t_w = template.shape[:2]
    small = t_w * t_h < 3000
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    if small:
        kernel = np.ones((3, 3), np.uint8)
        gray_template = cv2.erode(gray_template, kernel, iterations=1)
    if roi:
        x1, y1, x2, y2 = roi
        gray_frame = gray_frame[y1:y2, x1:x2]
        offset_x, offset_y = x1, y1
    else:
        offset_x, offset_y = 0, 0
    f_h, f_w = gray_frame.shape
    scale_down = 1.0
    if f_w > 1920:
        scale_down = 1920.0 / f_w
        new_w = int(f_w * scale_down)
        new_h = int(f_h * scale_down)
        gray_frame = cv2.resize(gray_frame, (new_w, new_h))
        f_h, f_w = gray_frame.shape
        offset_x = int(offset_x * scale_down)
        offset_y = int(offset_y * scale_down)
    best_val = -1
    best_rect = None
    scales = list(np.linspace(scale_range[0], scale_range[1], scale_steps))
    if 1.0 not in [round(s, 4) for s in scales]:
        scales = [1.0] + scales
    for scale in scales:
        scaled_w = int(t_w * scale)
        scaled_h = int(t_h * scale)
        if scaled_w > f_w or scaled_h > f_h:
            continue
        scaled = cv2.resize(gray_template, (scaled_w, scaled_h))
        result = cv2.matchTemplate(gray_frame, scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best_val:
            best_val = max_val
            best_rect = (max_loc[0] + offset_x, max_loc[1] + offset_y,
                         max_loc[0] + scaled_w + offset_x, max_loc[1] + scaled_h + offset_y)
    if best_val >= threshold:
        if scale_down != 1.0:
            best_rect = tuple(int(v / scale_down) for v in best_rect)
        if template_name in _flip_registry:
            rx1, ry1, rx2, ry2 = best_rect
            rx1, ry1 = max(0, rx1), max(0, ry1)
            rx2, ry2 = min(frame.shape[1], rx2), min(frame.shape[0], ry2)
            if rx2 > rx1 and ry2 > ry1:
                roi_gray = cv2.cvtColor(frame[ry1:ry2, rx1:rx2], cv2.COLOR_BGR2GRAY)
                resized_roi = cv2.resize(roi_gray, (t_w, t_h))
                ncc_orig = cv2.matchTemplate(resized_roi, gray_template, cv2.TM_CCOEFF_NORMED)[0][0]
                ncc_flip = cv2.matchTemplate(resized_roi, cv2.flip(gray_template, 0), cv2.TM_CCOEFF_NORMED)[0][0]
                if ncc_flip > ncc_orig:
                    logger.debug("模板 '%s' 翻转拒绝: 原始NCC=%.3f 翻转NCC=%.3f",
                                 template_name, ncc_orig, ncc_flip)
                    return None
        ct = color_threshold or _color_registry.get(template_name, 0.0)
        if ct > 0:
            rx1, ry1, rx2, ry2 = best_rect
            rx1, ry1 = max(0, rx1), max(0, ry1)
            rx2, ry2 = min(frame.shape[1], rx2), min(frame.shape[0], ry2)
            if rx2 > rx1 and ry2 > ry1:
                if template_name not in _hgram_cache:
                    _hgram_cache[template_name] = _compute_hgram(template)
                roi = frame[ry1:ry2, rx1:rx2]
                if roi.shape[0] != t_h or roi.shape[1] != t_w:
                    roi = cv2.resize(roi, (t_w, t_h))
                roi_hgram = _compute_hgram(roi)
                color_corr = _compare_hgram(_hgram_cache[template_name], roi_hgram)
                if color_corr < ct:
                    logger.debug("模板 '%s' 形状匹配置信度=%.3f, 颜色相关度=%.3f < %.2f, 已拒绝",
                                 template_name, best_val, color_corr, ct)
                    return None
        cx = (best_rect[0] + best_rect[2]) // 2
        cy = (best_rect[1] + best_rect[3]) // 2
        if auto_update and t_w * t_h < 3000 and best_val > 0.75:
            try:
                rx1, ry1, rx2, ry2 = best_rect
                rx1, ry1 = max(0, rx1), max(0, ry1)
                rx2, ry2 = min(frame.shape[1], rx2), min(frame.shape[0], ry2)
                if rx2 > rx1 and ry2 > ry1:
                    updated = frame[ry1:ry2, rx1:rx2]
                    _, buf = cv2.imencode(".png", updated)
                    with open(str(template_path), "wb") as f:
                        f.write(buf)
                    logger.debug("模板自动更新: '%s'", template_name)
            except Exception:
                pass
        logger.info("\u8bc6\u56fe\u6210\u529f: %s \u4f4d\u7f6e(%d,%d) \u7f6e\u4fe1\u5ea6=%.3f", template_name, cx, cy, best_val)
        return {
            "center": (cx, cy),
            "bbox": best_rect,
            "confidence": best_val,
        }
    if best_val > threshold * 0.5:
        logger.debug("模板 '%s' 最高置信度=%.3f (阈值=%.2f)", template_name, best_val, threshold)
    return None
