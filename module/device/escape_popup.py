"""Recognize only the supplied '确认退出战斗吗？' dialog, including its buttons."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


def game_exit_cancel_point(frame):
    """Recognize the Android "退出游戏" modal and return its Cancel button.

    Require the white centered panel and both teal action labels.  Matching
    the dimmed game page alone would mistake its buttons for usable controls.
    """
    if frame is None or getattr(frame, 'ndim', None) != 3 or frame.shape[2] < 3:
        return None
    height, width = frame.shape[:2]
    if height < 400 or width < 700:
        return None

    panel = frame[int(height * .35):int(height * .65),
                  int(width * .26):int(width * .75), :3]
    if float(np.mean(np.all(panel >= 245, axis=2))) < .88:
        return None

    def teal_count(x1, x2):
        area = frame[int(height * .58):int(height * .64),
                     int(width * x1):int(width * x2), :3].astype(np.int16)
        red, green, blue = area[:, :, 0], area[:, :, 1], area[:, :, 2]
        return int(np.count_nonzero((red < 90) & (green > red + 35)
                                    & (blue > red + 35) & (green > 90)))

    if teal_count(.60, .65) < 100 or teal_count(.68, .73) < 100:
        return None
    if not game_exit_dialog_present(frame) or not game_exit_actions_present(frame):
        return None
    return round(width * .628), round(height * .613)


@lru_cache(maxsize=1)
def _templates():
    path = Path(__file__).parent / 'recovery_assets' / 'exit_battle.png'
    original = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if original is None:
        raise RuntimeError('Unable to load ESC battle exit dialog template')
    templates = []
    for scale in np.arange(0.75, 2.26, 0.05):
        image = cv2.resize(original, None, fx=float(scale), fy=float(scale), interpolation=cv2.INTER_LINEAR)
        h, w = image.shape
        title_box = (int(w * 40 / 251), int(h * 40 / 161), int(w * 212 / 251), int(h * 82 / 161))
        button_box = (int(w * 14 / 251), int(h * 100 / 161), int(w * 237 / 251), int(h * 150 / 161))
        x1, y1, x2, y2 = title_box
        templates.append((image[y1:y2, x1:x2], image, title_box, button_box))
    return templates


def battle_exit_confirm_point(frame):
    if frame is None or frame.ndim != 3:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    for title, dialog, title_box, button_box in _templates():
        if title.shape[0] > gray.shape[0] or title.shape[1] > gray.shape[1]:
            continue
        result = cv2.matchTemplate(gray, title, cv2.TM_CCOEFF_NORMED)
        _, score, _, position = cv2.minMaxLoc(result)
        if score < 0.90:
            continue
        left, top = position[0] - title_box[0], position[1] - title_box[1]
        height, width = dialog.shape
        if left < 0 or top < 0 or left + width > gray.shape[1] or top + height > gray.shape[0]:
            continue
        x1, y1, x2, y2 = button_box
        candidate = gray[top+y1:top+y2, left+x1:left+x2]
        buttons = dialog[y1:y2, x1:x2]
        if cv2.matchTemplate(candidate, buttons, cv2.TM_CCOEFF_NORMED)[0, 0] >= 0.87:
            return left + round(width * 190 / 251), top + round(height * 126 / 161)
    return None


def game_exit_white_panel(frame):
    """A remaining native panel is not evidence of successful dismissal."""
    if frame is None or getattr(frame, 'ndim', None) != 3 or frame.shape[2] < 3:
        return False
    height, width = frame.shape[:2]
    if height < 400 or width < 700:
        return False
    panel = frame[int(height*.35):int(height*.65), int(width*.26):int(width*.75), :3]
    return bool(panel.size and np.mean(np.all(panel >= 245, axis=2)) >= .88)


@lru_cache(maxsize=4)
def _game_exit_text_template(name):
    path = Path(__file__).parent / 'recovery_assets' / name
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError('Unable to load Android exit-game text template')
    return image


def game_exit_dialog_present(frame):
    """Require the exact exit-game title and question, not just a white modal."""
    if not game_exit_white_panel(frame):
        return False
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_RGB2GRAY)
    for name, bounds in (
            ('android_exit_title.png', (398, 262, 146, 59)),
            ('android_exit_question.png', (340, 319, 211, 49))):
        x, y, w, h = bounds
        sx, sy = width/1280, height/720
        crop = gray[round(y*sy):round((y+h)*sy), round(x*sx):round((x+w)*sx)]
        template = _game_exit_text_template(name)
        template = cv2.resize(template, (max(1,round(template.shape[1]*sx)),
                                        max(1,round(template.shape[0]*sy))))
        if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
            return False
        if float(cv2.minMaxLoc(cv2.matchTemplate(crop,template,cv2.TM_CCOEFF_NORMED))[1]) < .94:
            return False
    return True


def game_exit_actions_present(frame):
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_RGB2GRAY)
    for name, bounds in (
            ('android_exit_cancel.png', (778, 424, 53, 33)),
            ('android_exit_confirm.png', (873, 424, 54, 33))):
        x, y, w, h = bounds
        sx, sy = width/1280, height/720
        crop = gray[round(y*sy):round((y+h)*sy), round(x*sx):round((x+w)*sx)]
        template = _game_exit_text_template(name)
        template = cv2.resize(template, (max(1,round(template.shape[1]*sx)),
                                        max(1,round(template.shape[0]*sy))))
        if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
            return False
        if float(cv2.minMaxLoc(cv2.matchTemplate(crop,template,cv2.TM_CCOEFF_NORMED))[1]) < .94:
            return False
    return True
