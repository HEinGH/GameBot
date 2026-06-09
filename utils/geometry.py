import random


def screen_ratio_to_abs(rx, ry, screen_w, screen_h):
    return int(rx * screen_w), int(ry * screen_h)


def abs_to_screen_ratio(x, y, screen_w, screen_h):
    return x / screen_w, y / screen_h


def random_point_in_bbox(bbox, jitter=5):
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    cx += random.randint(-jitter, jitter)
    cy += random.randint(-jitter, jitter)
    return cx, cy


def get_screen_size():
    import ctypes
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
