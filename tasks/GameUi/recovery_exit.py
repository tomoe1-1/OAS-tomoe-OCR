"""Locate recovery exits on the current screenshot, with single-click checks."""
from pathlib import Path
import cv2
import numpy as np


ASSETS = Path(__file__).resolve().parent / 'res'


def exit_candidates(image):
    """Every exit visible right now, strongest first.

    Split out from ``exit_point`` so a caller can prefer a modal's own close
    over a stale page-level back arrow sitting behind it.
    """
    if not isinstance(image, np.ndarray) or image.ndim != 3:
        return []
    height, width = image.shape[:2]
    # Exit icons sit in the top UI band, but not always: a modal such as the
    # SoulsTidy feeding dialog closes at y≈231 on a 720p screen, which the old
    # height//3 (240) band cut off — measured 0.581 there versus 0.959 once the
    # band reaches 280. The ambiguity rule in ``exit_point`` is what actually
    # guards against mistaking battle-card crosses for a close button.
    band = image[:max(1, min(height, height * 45 // 100))]
    source = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
    candidates = []
    for name in ('back_arrow', 'close_cross'):
        path = ASSETS / ('recovery_' + name + '.png')
        template = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE) if path.exists() else None
        if template is None:
            continue
        for scale in (.75, 1., 1.25):
            scaled = cv2.resize(template, None, fx=scale, fy=scale)
            th, tw = scaled.shape
            if th > source.shape[0] or tw > width:
                continue
            result = cv2.matchTemplate(source, scaled, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if score >= .88:
                x, y = location
                candidates.append((score, x + tw // 2, y + th // 2, name))
    candidates.sort(reverse=True)
    return candidates


def exit_point(image):
    candidates = exit_candidates(image)
    if not candidates:
        return None
    best = candidates[0]
    # Multiple distinct matching exits are ambiguous: let normal navigation decide.
    if any(abs(item[1] - best[1]) > 25 or abs(item[2] - best[2]) > 25
           for item in candidates[1:]):
        return None
    return best[1], best[2], best[3]


def modal_close_point(image):
    """The close icon of an open modal, ignoring any back arrow behind it.

    A modal covers the page it was opened from, so that page's exit stays
    visible while being useless. Measured 2026-10-11 on the SoulsTidy feeding
    dialog: back_arrow scored 1.000 at (47,40) and clicking it changed nothing,
    while the dialog's own close cross scored 0.959 at (951,231). Recovery spent
    its whole budget clicking the arrow behind the dialog.
    """
    closes = [item for item in exit_candidates(image) if item[3] == 'close_cross']
    if not closes:
        return None
    _, x, y, _ = closes[0]
    # Two distinct crosses: we cannot tell which one owns the modal.
    if any(abs(item[1] - x) > 25 or abs(item[2] - y) > 25 for item in closes[1:]):
        return None
    return x, y


def click_exit(task):
    close = modal_close_point(task.device.image)
    if close is not None:
        x, y = close
        kind = 'close_cross'
    else:
        point = exit_point(task.device.image)
        if point is None:
            return False
        x, y, kind = point
    before = cv2.resize(cv2.cvtColor(task.device.image, cv2.COLOR_RGB2GRAY), (64, 36))
    if task.device.click(x, y, control_name='RECOVERY_EXIT_' + kind.upper()) is False:
        return False
    # Observe after this single input. The caller still verifies the home page.
    changed = []
    for _ in range(2):
        task.screenshot()
        after = cv2.resize(cv2.cvtColor(task.device.image, cv2.COLOR_RGB2GRAY), (64, 36))
        changed.append(float(np.mean(np.abs(before.astype(np.int16) - after.astype(np.int16)))) > 12)
    return all(changed)
