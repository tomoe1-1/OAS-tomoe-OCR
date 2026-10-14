"""Current version activity: identity, bounded non-consuming entry navigation."""
from functools import lru_cache
from pathlib import Path
import cv2
import numpy as np
from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import TaskRecoveryFailed
from module.device.confirmation_checkbox import ensure_confirmation_option
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.courtyard import courtyard_visible

ASSETS = Path(__file__).resolve().parent / 'page'
HUB_ANCHORS = (('hub_title',(151,16,135,42)), ('hub_shop',(1038,646,102,39)))
TRIAL_ANCHORS = (('trial_title',(143,15,145,43)), ('trial_info',(23,119,293,87)),
                 ('trial_team',(962,598,97,51)))
OVERVIEW_ANCHORS = (('overview_title',(149,17,139,39)),)
VERSION_ANCHORS = (('version_banner',(1063,95,129,164)),)

@lru_cache(maxsize=9)
def _template(name):
    return cv2.cvtColor(cv2.imdecode(np.frombuffer((ASSETS / ('version_' + name + '.png')).read_bytes(),
                                                 np.uint8),1),cv2.COLOR_BGR2RGB)

def visible(image, anchors):
    if not isinstance(image,np.ndarray) or image.shape != (720,1280,3): return False
    for name,(x,y,w,h) in anchors:
        a=image[y:y+h,x:x+w]; b=_template(name)
        if cv2.matchTemplate(a,b,cv2.TM_CCOEFF_NORMED)[0,0] < .94: return False
        if np.abs(a.astype(np.int16)-b.astype(np.int16)).mean() > 18: return False
    return True

def hub_visible(image): return visible(image,HUB_ANCHORS)
def trial_visible(image): return visible(image,TRIAL_ANCHORS)
def overview_visible(image): return visible(image,OVERVIEW_ANCHORS)
def version_overview_visible(image): return overview_visible(image) and visible(image,VERSION_ANCHORS)
def is_hub(task): return hub_visible(task.device.image)
def is_trial(task): return trial_visible(task.device.image)

I_LIST_ENTRY = RuleImage(roi_front=(1184,146,39,39),roi_back=(1164,111,81,430),
                        threshold=.94,method='Template matching',
                        file=str(ASSETS / 'version_courtyard_entry.png'))
I_LIST_ENTRY_RECENT = RuleImage(roi_front=(1184,146,39,39),roi_back=(1164,111,81,430),
    threshold=.95,method='Template matching',file=str(ASSETS / 'version_courtyard_recent.png'))
O_VERSION_ROW = RuleOcr(roi=(48,85,145,579),area=(48,85,145,579),mode='Full',method='Default',
                       keyword='版本活动',name='VERSION_ACTIVITY_ROW')
C_GOTO = RuleClick((1080,643,132,28),(1080,643,132,28),name='VERSION_GOTO_ACTIVITY')
C_MAP = RuleClick((834,475,45,27),(834,475,45,27),name='VERSION_GOTO_MAP')
C_RUINS = RuleClick((253,153,23,24),(253,153,23,24),name='VERSION_RUINS_ENTRY')
C_TRIAL = RuleClick((253,246,23,24),(253,246,23,24),name='VERSION_TRIAL_ENTRY')
C_CHALLENGE = RuleClick((1159,610,30,27),(1159,610,30,27),name='VERSION_CHALLENGE_ONCE')

def _click_once(task, control):
    control.burst_count=1
    x,y=control.coord()
    ensure_confirmation_option(task,(x,y),control.name)
    if task.device.click(x=x,y=y,control_name=control.name) is False:
        raise TaskRecoveryFailed('Version activity input was blocked: '+control.name)

def _scan(task,predicate,lookup,start,end):
    # Three total scan swipes: preserve the shared repeated-input guard and history.
    for direction in (None,1,-1,-1):
        task._check_navigation_budget()
        task._ui_verify(predicate,[],timeout=3)
        if direction is not None:
            p1,p2=(start,end) if direction==1 else (end,start)
            if task.device.swipe(p1,p2) is False:
                raise TaskRecoveryFailed('Version activity scan input was blocked')
            task._ui_verify(predicate,[],timeout=3)
        found=lookup()
        if found is not None:
            roi=tuple(found.roi_front)
            confirmed=[]
            def same_target():
                if not predicate(): return False
                current=lookup()
                if current is None: return False
                new_roi=tuple(current.roi_front)
                if new_roi[2:] != roi[2:] or any(abs(a-b)>2 for a,b in zip(new_roi[:2],roi[:2])): return False
                confirmed[:] = [current]
                return True
            if task._ui_verify(same_target,[],timeout=3,fail=False):
                return confirmed[0]
    raise TaskRecoveryFailed('Version activity entry not found within bounded scan')

def enter_version_hub(task):
    image=task.device.image
    if not overview_visible(image):
        def main(): return courtyard_visible(task)
        def entry():
            for control in (I_LIST_ENTRY,I_LIST_ENTRY_RECENT):
                if task.appear(control): return control
            return None
        control=_scan(task,main,entry,(1203,471),(1203,235))
        _click_once(task,control)
        task._ui_verify(lambda:overview_visible(task.device.image),[control],timeout=15)
    def row():
        x,y,w,h=O_VERSION_ROW.ocr_full(task.device.image)
        if not w or not h or y < 85 or y+h > 664: return None
        return RuleClick((x,y,w,h),(x,y,w,h),name='VERSION_ACTIVITY_ROW_SELECT')
    if version_overview_visible(task.device.image):
        task._ui_verify(lambda:version_overview_visible(task.device.image),[],timeout=10)
    else:
        control=_scan(task,lambda:overview_visible(task.device.image),row,(133,579),(133,203))
        _click_once(task,control)
        task._ui_verify(lambda:version_overview_visible(task.device.image),[control],timeout=10)
    _click_once(task,C_GOTO)
    task._ui_verify(lambda:hub_visible(task.device.image),[C_GOTO],timeout=30)
    return True
