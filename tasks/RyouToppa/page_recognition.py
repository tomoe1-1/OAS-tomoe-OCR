"""寮突破页面的复合识别：已选阴阳寮页签、进度和突破记录。"""

import re

import cv2
import numpy as np

from module.atom.ocr import RuleOcr
from tasks.RyouToppa.assets import RyouToppaAssets


GUILD_TAB = RuleOcr(
    roi=(1170, 355, 100, 145), area=(1170, 355, 100, 145),
    mode='Single', method='Default', keyword='', name='ryou_guild_tab')
BREAKTHROUGH_PROGRESS = RuleOcr(
    roi=(130, 365, 250, 65), area=(130, 365, 250, 65),
    mode='Single', method='Default', keyword='', name='ryou_breakthrough_progress')


def is_ryou_toppa_context(task) -> bool:
    """确认阴阳寮页签选中和突破记录，允许左侧进度被完成文字替换。"""
    image = task.device.image
    if image is None or image.shape[0] < 710 or image.shape[1] < 1270:
        return False
    if not task.appear(RyouToppaAssets.I_TOPPA_RECORD):
        return False
    tab = str(GUILD_TAB.ocr(image) or '')
    if '阴阳寮' not in tab:
        return False
    hsv = cv2.cvtColor(image[375:486, 1192:1208], cv2.COLOR_RGB2HSV)
    bright_border = np.count_nonzero((hsv[:, :, 2] > 160) & (hsv[:, :, 1] < 125))
    return bright_border >= 100


def is_ryou_toppa_page(task) -> bool:
    """同帧确认右侧高亮、左侧百分比、左下突破记录。"""
    if not is_ryou_toppa_context(task):
        return False
    progress = str(BREAKTHROUGH_PROGRESS.ocr(task.device.image) or '')
    return bool(re.search(r'\d{1,3}(?:\.\d+)?\s*%', progress))
