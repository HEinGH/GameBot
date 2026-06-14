import logging
from pathlib import Path

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
):
    if frame is None:
        return None
    if scale_steps < 1:
        scale_steps = 1
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        if template_name not in _missing_cache:
            _missing_cache.add(template_name)
            logger.warning("Template not found: %s", template_path)
        return None
    data = np.fromfile(str(template_path), dtype=np.uint8)
    template = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if template is None:
        return None
    t_h, t_w = template.shape[:2]
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
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
                    logger.debug("Template '%s' reject flip: ncc_orig=%.3f ncc_flip=%.3f",
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
                    logger.debug("Template '%s' shape match conf=%.3f, color corr=%.3f < %.2f, rejected",
                                 template_name, best_val, color_corr, ct)
                    return None
        cx = (best_rect[0] + best_rect[2]) // 2
        cy = (best_rect[1] + best_rect[3]) // 2
        logger.info("\u8bc6\u56fe\u6210\u529f: %s \u4f4d\u7f6e(%d,%d) \u7f6e\u4fe1\u5ea6=%.3f", template_name, cx, cy, best_val)
        return {
            "center": (cx, cy),
            "bbox": best_rect,
            "confidence": best_val,
        }
    if best_val > threshold * 0.5:
        logger.debug("Template '%s' best_conf=%.3f (threshold=%.2f)", template_name, best_val, threshold)
    return None


def find_all_templates(frame, template_name, threshold=0.8, scale_range=(0.5, 1.5), scale_steps=11, max_results=5):
    if frame is None:
        return []
    if scale_steps < 1:
        scale_steps = 1
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        if template_name not in _missing_cache:
            _missing_cache.add(template_name)
            logger.warning("Template not found: %s", template_path)
        return []
    data = np.fromfile(str(template_path), dtype=np.uint8)
    template = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if template is None:
        return []
    t_h, t_w = template.shape[:2]
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    results = []
    f_h, f_w = gray_frame.shape
    for scale in np.linspace(scale_range[0], scale_range[1], scale_steps):
        scaled_w = int(t_w * scale)
        scaled_h = int(t_h * scale)
        if scaled_w > f_w or scaled_h > f_h:
            continue
        scaled = cv2.resize(gray_template, (scaled_w, scaled_h))
        result = cv2.matchTemplate(gray_frame, scaled, cv2.TM_CCOEFF_NORMED)
        locs = np.where(result >= threshold)
        for pt in zip(*locs[::-1]):
            cx = pt[0] + scaled_w // 2
            cy = pt[1] + scaled_h // 2
            results.append({
                "center": (cx, cy),
                "bbox": (pt[0], pt[1], pt[0] + scaled_w, pt[1] + scaled_h),
                "confidence": float(result[pt[1], pt[0]]),
            })
    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results[:max_results]
