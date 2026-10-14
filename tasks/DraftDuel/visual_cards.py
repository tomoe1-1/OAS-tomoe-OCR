"""按已命名的画像寻找无名上阵卡，不把栏位序号当成式神身份。"""
import cv2
import numpy as np


def locate_card(image, portraits, threshold=0.6, margin=0.07):
    height, width = image.shape[:2]
    left, top = int(width * 0.16), int(height * 0.69)
    right, bottom = int(width * 0.72), int(height * 0.94)
    screen = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_RGB2GRAY)
    candidates = []
    responses = []
    for portrait in portraits:
        if portrait is None or portrait.size == 0:
            continue
        source = cv2.cvtColor(portrait, cv2.COLOR_RGB2GRAY)
        original_saturation = float(cv2.cvtColor(portrait, cv2.COLOR_RGB2HSV)[:, :, 1].mean())
        if source.std() < 12:
            continue
        for scale in np.arange(0.7, 2.51, 0.1):
            template = cv2.resize(source, None, fx=float(scale), fy=float(scale))
            th, tw = template.shape
            if th >= screen.shape[0] or tw >= screen.shape[1]:
                continue
            response = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(response)
            candidates.append((float(score), left + point[0] + tw // 2,
                               top + point[1] + th // 2, tw, th, original_saturation))
            responses.append((response, tw))
    if not candidates:
        return None
    score, x, y, tw, th, original_saturation = max(candidates)
    if score < threshold:
        return None
    second = 0.0
    for response, template_width in responses:
        centers = left + np.arange(response.shape[1]) + template_width // 2
        other = np.abs(centers - x) > max(60, width * 0.055)
        if other.any():
            second = max(second, float(response[:, other].max()))
    if score - second < margin:
        return None
    patch = image[max(0, y-th//2):y+th//2, max(0, x-tw//2):x+tw//2]
    saturation = float(cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)[:, :, 1].mean())
    grey = saturation < original_saturation * 0.5 and original_saturation - saturation > 20
    return {'point': (x, y), 'score': score, 'margin': score - second,
            'size': (tw, th), 'grey': grey}


def portrait_above_name(image, box):
    """OCR 名字上方的头像作为参照，位置由文字框推导。"""
    box = np.asarray(box)
    x = int((box[:, 0].min() + box[:, 0].max()) / 2)
    y = int(box[:, 1].min())
    # 头像与名字之间的间距随截图尺寸同比缩放。
    scale = image.shape[0] / 720
    half = round(21 * scale)
    top, bottom = max(0, y - round(64 * scale)), max(0, y - round(27 * scale))
    return image[top:bottom, max(0, x-half):min(image.shape[1], x+half)].copy()


def full_portrait_above_name(image, box):
    box = np.asarray(box)
    x = int((box[:, 0].min() + box[:, 0].max()) / 2)
    y = int(box[:, 1].min())
    scale = image.shape[0] / 720
    half = round(22 * scale)
    return image[max(0, y-round(58*scale)):max(0, y-round(9*scale)),
                 max(0, x-half):min(image.shape[1], x+half)].copy()


def face_above_name(image, box):
    """保留脸部中央，避开选中叉号覆盖的头像上缘。"""
    box = np.asarray(box)
    x = int((box[:, 0].min() + box[:, 0].max()) / 2)
    y = int(box[:, 1].min())
    scale = image.shape[0] / 720
    half = round(15 * scale)
    return image[max(0, y-round(45*scale)):max(0, y-round(15*scale)),
                 max(0, x-half):min(image.shape[1], x+half)].copy()


def card_drag_start(image, location):
    """按卡面主体，避开上缘的叉号、稀有度和御魂图标。"""
    x, y = location['point']
    height = image.shape[0]
    return int(x), int(min(max(y + 45 * height / 720, height * 0.81), height * 0.87))


def confirmed_draft_portrait(image, text_boxes, number):
    """下一轮出现时，从“我的”已选头像读取本次新增画像。"""
    height, width = image.shape[:2]
    digits = [item for item in text_boxes if item.ocr_text.strip() == '999999'
              and width * 0.33 < item.box[:, 0].mean() < width * 0.7
              and item.box[:, 1].mean() < height * 0.16]
    digits.sort(key=lambda item: item.box[:, 0].mean())
    if len(digits) != number:
        return None
    box = digits[number - 1].box
    x, y = int(box[:, 0].mean()), int(box[:, 1].min())
    scale = height / 720
    half = round(25 * scale)
    return image[max(0, y-round(58*scale)):y-round(8*scale),
                 max(0, x-half):min(width, x+half)].copy()
