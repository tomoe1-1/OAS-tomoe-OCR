"""Continue settlement after any result marker or either continuation prompt."""
import numpy as np

from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.GlobalGame.assets import GlobalGameAssets


CONTINUE_PROMPTS = frozenset(('点击屏幕继续', '点击空白区域继续'))
RESULT_MARKERS = (
    GeneralBattleAssets.I_WIN,
    GeneralBattleAssets.I_DE_WIN,
    GeneralBattleAssets.I_FALSE,
    GeneralBattleAssets.I_BATTLE_STATE_INFO,
)


def settlement_continue_click(task):
    """Return one continuation input for a recognized settlement screen."""
    image = task.device.image
    if image is None or len(image.shape) < 2:
        return None
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        return None
    # A result marker is sufficient; these pages need not display a caption.
    # This is only an input target, not a declaration of task success.
    appear = getattr(task, 'appear', None)
    if appear is not None and any(appear(rule) for rule in RESULT_MARKERS):
        # Use the original random side region, not the continuation caption.
        side = GlobalGameAssets.C_UI_REWARD
        x, y, w, h = side.roi_front
        if x < 0 or y < 0 or x + w > width or y + h > height:
            return None
        click = RuleClick(roi_front=side.roi_front, roi_back=side.roi_back,
                          name='SETTLEMENT_RESULT_CONTINUE')
        click.burst_count = 1
        return click
    top = height * 3 // 4
    roi = (0, top, width, height - top)
    rule = RuleOcr(roi=roi, area=roi, mode='Full', method='Default', keyword='',
                   name='settlement_continue_prompt')
    rule.score = 0.8
    matches = []
    for result in rule.detect_and_ocr(image):
        if ''.join(result.ocr_text.split()) not in CONTINUE_PROMPTS:
            continue
        box = np.asarray(result.box)
        if box.shape != (4, 2) or not np.isfinite(box).all():
            continue
        left, local_top = box.min(axis=0)
        right, local_bottom = box.max(axis=0)
        if not (0 <= left < right <= width and 0 <= local_top < local_bottom <= height - top):
            continue
        x = int((left + right) / 2)
        y = top + int((local_top + local_bottom) / 2)
        matches.append((x, y))
    if len(matches) != 1:
        return None
    x, y = matches[0]
    click = RuleClick(roi_front=(x, y, 1, 1), roi_back=(x, y, 1, 1),
                      name='SETTLEMENT_CONTINUE')
    click.burst_count = 1
    return click
