"""Locate the gift room's daily label and verify its current selection from RGB frames.

These helpers only inspect pixels. The caller owns screenshots, one input, and
verification on fresh frames; no coordinates from an earlier frame are retained.
"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from module.atom.ocr import RuleOcr


_STORE = Path(__file__).resolve().parent / 'store'


@lru_cache(maxsize=3)
def _template(filename):
    image = cv2.imdecode(np.frombuffer((_STORE / filename).read_bytes(), np.uint8),
                         cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'Cannot read store template: {filename}')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _rgb_frame(image):
    return (isinstance(image, np.ndarray) and image.ndim == 3
            and image.shape[2] == 3 and image.dtype == np.uint8
            and image.shape[0] > 0 and image.shape[1] > 0)


def _unique_match(image, template, *, edges=False):
    if isinstance(template, tuple):
        templates = template
    else:
        templates = (template,)
    template = templates[0]
    height, width = template.shape[:2]
    if not _rgb_frame(image) or image.shape[0] < height or image.shape[1] < width:
        return None
    rgb = np.maximum.reduce([cv2.matchTemplate(image, item, cv2.TM_CCOEFF_NORMED)
                             for item in templates])
    # Edges retain the upright glyphs when the selection changes their paper
    # background to pink. Color alone is used for the independent gift icon.
    scores = rgb / .90
    if edges:
        def outlines(frame):
            return cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), 50, 150)
        outlined = outlines(image)
        edge = np.maximum.reduce([cv2.matchTemplate(outlined, outlines(item),
                                  cv2.TM_CCOEFF_NORMED) for item in templates])
        scores = np.maximum(scores, edge / .86)
        # Pink selection paper changes antialiasing at the glyph boundary. The
        # dark upright strokes themselves remain, so also compare their mask.
        def ink(frame):
            return (cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY) < 85).astype(np.uint8) * 255
        inked = ink(image)
        stroke = np.maximum.reduce([cv2.matchTemplate(inked, ink(item),
                                    cv2.TM_CCOEFF_NORMED) for item in templates])
        scores = np.maximum(scores, stroke / .90)
    scores = np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)
    _, maximum, _, point = cv2.minMaxLoc(scores)
    if maximum < 1.0:
        return None
    x, y = point
    # Nearby correlation peaks describe the same glyphs. A second independent
    # label is ambiguous: never merge two labels into a midpoint click.
    scores[max(0, y - height // 2):y + height // 2 + 1,
           max(0, x - width // 2):x + width // 2 + 1] = 0
    if cv2.minMaxLoc(scores)[1] >= 1.0:
        return None
    return x + width // 2, y + height // 2


def find_daily_tab(image) -> tuple[int, int] | None:
    """Find a unique complete 日常 label anywhere in this frame, at its actual center."""
    return _unique_match(image, (_template('store_daily.png'),
                                _template('store_daily_selected.png')), edges=True)


def daily_tab_selected(image) -> bool:
    """Relocate the label on this frame, then inspect the pink selection around it."""
    pos = find_daily_tab(image)
    if pos is None or not gift_room_visible(image):
        return False
    x, y = pos
    crop = image[max(0, y - 40):min(image.shape[0], y + 40),
                 max(0, x - 40):min(image.shape[1], x + 40)]
    red, green, blue = np.moveaxis(crop.astype(np.int16), -1, 0)
    pink = (red > 180) & (blue - green > 8) & (red - green > 15)
    return float(np.mean(pink)) > .01


def gift_room_visible(image) -> bool:
    """Require the bottom gift-room icon and its selected golden surrounding frame.

    Every shop keeps the bottom icon visible. Its pixels alone cannot distinguish
    an open gift room from the sundry shop. Both sides of the selected icon have
    a stronger golden glow; these strips exclude the icon template itself.
    """
    if not _rgb_frame(image):
        return False
    bottom = int(image.shape[0] * .82)
    pos = _unique_match(image[bottom:], _template('store_room_gift.png'))
    if pos is None:
        return False
    x, y = pos[0], pos[1] + bottom
    if x < 67 or x + 67 > image.shape[1] or y < 43 or y + 22 > image.shape[0]:
        return False
    surround = image[y - 43:y + 22, x - 67:x + 67].astype(np.int16)
    red, green, blue = np.moveaxis(surround, -1, 0)
    gold = (red > 175) & (green > 105) & (red - blue > 40) & (green - blue > 15)
    return float(np.mean(gold[:, :33])) > .12 and float(np.mean(gold[:, -33:])) > .12


def daily_gift_claim_center(image, gift_roi) -> tuple[int, int] | None:
    """Confirm the matched card's 每日礼包 title and locate its own 领取 text."""
    if not _rgb_frame(image):
        return None
    x, y, width, height = map(int, gift_roi)
    if width <= 0 or height <= 0:
        return None

    def text_center(top, keyword):
        left, top = max(0, x - 25), max(0, top)
        right, bottom = min(image.shape[1], x + width + 25), min(image.shape[0], top + 65)
        if right <= left or bottom <= top:
            return None
        roi = (left, top, right - left, bottom - top)
        rule = RuleOcr(roi=roi, area=roi, mode='Full', method='Default', keyword='',
                       name='store_daily_gift_' + keyword)
        rule.score = .8
        matches = []
        for result in rule.detect_and_ocr(image):
            if ''.join(result.ocr_text.split()) != keyword or result.score < .8:
                continue
            box = np.asarray(result.box)
            if box.shape != (4, 2) or not np.isfinite(box).all():
                continue
            a, b = box.min(axis=0)
            c, d = box.max(axis=0)
            if 0 <= a < c <= roi[2] and 0 <= b < d <= roi[3]:
                matches.append((left + int((a + c) / 2), top + int((b + d) / 2)))
        return matches[0] if len(matches) == 1 else None

    if text_center(y - 65, '每日礼包') is None:
        return None
    return text_center(y + height + 5, '领取')


def daily_gift_claimed_dismiss(image) -> tuple[int, int] | None:
    """Require the daily-gift title, claimed stamp and FREE label in one card.

    Paid lucky gifts in adjacent columns do not prove the free sign was claimed.
    Return a blank point outside the panel, away from bottom shop controls.
    """
    title = _unique_match(image, _template('store_daily_receipt_title.png'))
    if title is None:
        return None
    x, y = title
    left, top = x - 90, y + 155
    if left < 0 or top < 0 or left + 190 > image.shape[1] or top + 245 > image.shape[0]:
        return None
    card = image[top:top + 245, left:left + 190]
    stamp = _unique_match(card[:120], _template('store_daily_receipt_claimed.png'))
    free = _unique_match(card[145:], _template('store_daily_receipt_free.png'))
    if stamp is None or free is None:
        return None
    return max(10, x - 230), min(image.shape[0] - 100, y + 455)
