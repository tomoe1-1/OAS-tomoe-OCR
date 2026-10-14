"""Strict identity for the current activity map; navigation exposes only back."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from module.exception import TaskRecoveryFailed
from module.atom.image import RuleImage

ASSETS = Path(__file__).resolve().parent / 'page'
I_ACTIVITY_MAP_BACK = RuleImage(roi_front=(31, 26, 24, 22), roi_back=(31, 26, 24, 22),
                              threshold=.95, method='Template matching',
                              file=str(ASSETS / 'activity_kochou_back_click.png'))
ANCHORS = (('title', (585, 0, 145, 53)),
           ('back', (16, 12, 55, 53)),
           ('modes', (74, 151, 94, 29)))

SUB_ANCHORS = (('sub_title', (142, 15, 141, 42)),
               ('sub_info', (25, 116, 290, 94)),
               ('sub_preset', (906, 600, 86, 32)))

@lru_cache(maxsize=8)
def _template(name):
    filename = 'version_map_modes.png' if name == 'modes' else ('version_map_trial_label.png' if name == 'trial_label' else 'activity_kochou_' + name + '.png')
    data = cv2.imdecode(np.frombuffer((ASSETS / filename).read_bytes(),
                                     np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(data, cv2.COLOR_BGR2RGB)

def activity_map_home_visible(image):
    return _anchors_visible(image, ANCHORS[:2]) and activity_mode_rows(image) is not None

def activity_mode_rows(image):
    if not isinstance(image, np.ndarray) or image.shape != (720,1280,3):
        return None
    rows={}
    for mode,name in (('ruins','modes'),('trial','trial_label')):
        template=_template(name)
        region=image[120:430,65:210]
        scores=cv2.matchTemplate(region,template,cv2.TM_CCOEFF_NORMED)
        _,score,_,(x,y)=cv2.minMaxLoc(scores)
        if score < .95:
            return None
        peaks=scores.copy();peaks[max(0,y-4):y+5,max(0,x-4):x+5]=0
        if peaks.max() >= .95:
            return None
        h,w=template.shape[:2]
        if np.abs(region[y:y+h,x:x+w].astype(np.int16)-template.astype(np.int16)).mean()>15:
            return None
        rows[mode]=(65+x+w//2,120+y+h//2)
    if rows['trial'][1]-rows['ruins'][1] < 50:
        return None
    return rows

def enter_activity_mode(task,mode):
    if mode not in ('ruins','trial'):
        raise ValueError('Unknown activity mode')
    generation=task.device._escape_generation
    invitation=getattr(task.device,'_accepted_invitation_generation',0)
    frame=task.device.image_frame_id
    previous=None
    def stable():
        nonlocal frame,previous
        current=task.device.image_frame_id
        if current is None or current==frame:
            raise TaskRecoveryFailed('Activity mode requires fresh frames')
        frame=current
        if task.device._escape_generation!=generation or getattr(task.device,'_accepted_invitation_generation',0)!=invitation:
            raise TaskRecoveryFailed('Activity mode navigation interrupted')
        if not activity_map_home_visible(task.device.image):
            previous=None
            return False
        rows=activity_mode_rows(task.device.image)
        matched=rows==previous
        previous=rows
        return matched
    task._ui_verify(stable,[],timeout=5)
    if not activity_map_home_visible(task.device.image) or activity_mode_rows(task.device.image)!=previous:
        raise TaskRecoveryFailed('Activity mode row changed before click')
    # The established row arrow stays at x=265; only its label-derived y moves.
    if task.device.click(265,previous[mode][1],control_name='VERSION_'+mode.upper()+'_ENTRY') is False:
        raise TaskRecoveryFailed('Activity mode input blocked')
    def target():
        nonlocal frame
        current=task.device.image_frame_id
        if current is None or current==frame:
            raise TaskRecoveryFailed('Activity target requires fresh frames')
        frame=current
        if task.device._escape_generation!=generation or getattr(task.device,'_accepted_invitation_generation',0)!=invitation:
            raise TaskRecoveryFailed('Activity target navigation interrupted')
        if mode=='ruins':
            return activity_stage_visible(task.device.image)
        from tasks.GameUi.version_activity_ui import trial_visible
        return trial_visible(task.device.image)
    frame=task.device.image_frame_id
    task._ui_verify(target,['VERSION_'+mode.upper()+'_ENTRY'],timeout=15)
    return True

def enter_activity_ruins(task):
    return enter_activity_mode(task,'ruins')

def enter_activity_trial(task):
    return enter_activity_mode(task,'trial')

def _anchors_visible(image, anchors):
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return False
    for name, (x, y, w, h) in anchors:
        region = image[y:y+h, x:x+w]
        template = _template(name)
        score = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)[0, 0]
        # Correlation alone also accepts a dimmed modal background.
        difference = np.abs(region.astype(np.int16) - template.astype(np.int16)).mean()
        if score < .95 or difference > 15:
            return False
    return True

def is_activity_map_home(task):
    return activity_map_home_visible(task.device.image)

def activity_stage_visible(image):
    return _anchors_visible(image, SUB_ANCHORS)

def is_activity_stage(task):
    return activity_stage_visible(task.device.image)
