"""Dismiss the daily fortune card before the courtyard collection receipt."""
from pathlib import Path
from functools import lru_cache
from time import monotonic
import cv2
import numpy as np
from module.exception import TaskRecoveryFailed

_ASSETS=Path(__file__).resolve().parent/'dt'

@lru_cache(maxsize=8)
def _template(name):
    bgr=cv2.imdecode(np.frombuffer((_ASSETS/name).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)

def courtyard_claim_popup(image):
    if not isinstance(image,np.ndarray) or image.ndim!=3 or abs(image.shape[1]-1280)>3 or abs(image.shape[0]-720)>3:
        return None
    def matches(name,roi):
        x,y,w,h=roi
        return cv2.minMaxLoc(cv2.matchTemplate(image[y:y+h,x:x+w],_template(name),
                                              cv2.TM_CCOEFF_NORMED))[1]>=.92
    # Only the dedicated daily six-star reward selector may use this default.
    # Require its full title and all three buttons; re-locate the left button.
    if matches('courtyard_soul_choice_title.png',(415,100,440,80)):
        positions = []
        button = _template('courtyard_soul_choice_button.png')
        for x in (180,550,920):
            scores = cv2.matchTemplate(image[420:500,x:x+200],button,cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(scores)
            if score < .92:
                break
            positions.append((x+location[0]+button.shape[1]//2,
                              420+location[1]+button.shape[0]//2))
        if len(positions) == 3:
            return 'soul_choice', positions[0]
    # Expanded 2026 courtyard selector: retain strict title and all buttons.
    if matches('courtyard_soul_choice_large_title.png',(425,110,440,80)):
        positions = []
        button = _template('courtyard_soul_choice_large_button.png')
        for x in (180,550,920):
            scores = cv2.matchTemplate(image[550:640,x:x+200],button,cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(scores)
            if score < .92:
                break
            positions.append((x+location[0]+button.shape[1]//2,
                              550+location[1]+button.shape[0]//2))
        if len(positions) == 3:
            return 'soul_choice', positions[0]
    # Check the foreground card first: the collection receipt is behind it.
    if (matches('courtyard_sign_total.png',(450,475,150,70))
            and matches('courtyard_sign_explain.png',(835,425,130,105))
            and matches('courtyard_sign_close.png',(825,65,100,90))):
        return 'sign', (871,108)
    # The title identifies the receipt regardless of reward rows or scrolling.
    if matches('courtyard_claim_success.png',(275,10,235,85)):
        return 'receipt', (640,695)
    return None

def close_courtyard_claim_popups(task, on_success=None):
    """One input per identified layer, two fresh observations for each state."""
    generation=task.device._escape_generation
    deadline=monotonic()+task.device.performance.timeout(12)
    hard_deadline=monotonic()+task.device.performance.timeout(360)
    previous=None;stable=0;clicked=set();handled=False
    invitation=getattr(task.device, '_accepted_invitation_generation', 0)
    frame=getattr(task.device, 'image_frame_id', None)
    for _ in range(32):
        if monotonic()>=min(deadline,hard_deadline):
            break
        task.screenshot()
        fresh=getattr(task.device, 'image_frame_id', None)
        if frame is not None and (fresh is None or fresh == frame):
            raise TaskRecoveryFailed('Courtyard claim requires fresh screenshots')
        frame=fresh
        if (task.device._escape_generation!=generation
                or getattr(task.device, '_accepted_invitation_generation', 0)!=invitation):
            raise TaskRecoveryFailed('Courtyard claim popup interrupted by recovery')
        current=courtyard_claim_popup(task.device.image)
        kind=current[0] if current else None
        state=current if kind=='soul_choice' else kind
        stable=stable+1 if state==previous else 1
        previous=state
        if stable<2:
            continue
        if current is None:
            # A claim caller is awaiting the receipt, not just absent popups.
            # Reward animations (including after the fortune card) may span
            # several fresh frames before the success title becomes visible.
            if on_success is not None and 'receipt' not in clicked:
                continue
            if handled:
                task.device.confirm_control_progress(['COURTYARD_SIGN_CLOSE','COURTYARD_RECEIPT_CLOSE','COURTYARD_SOUL_SELECT'])
            return handled
        if kind in clicked:
            # Allow the animation to settle, but never replay a close input.
            continue
        if kind == 'soul_choice' and on_success is None:
            raise TaskRecoveryFailed('Courtyard soul choice requires an active claim')
        if kind == 'receipt' and on_success is not None:
            on_success()
        control='COURTYARD_SOUL_SELECT' if kind=='soul_choice' else 'COURTYARD_'+kind.upper()+'_CLOSE'
        if task.device.click(*current[1],control_name=control) is False:
            raise TaskRecoveryFailed('Courtyard claim popup close was not dispatched')
        clicked.add(kind);handled=True;previous=None;stable=0
        # Control may rest after dispatch; give the next layer its observation budget.
        # Three one-shot layers, 32 captures and the hard deadline remain bounded.
        deadline=monotonic()+task.device.performance.timeout(12)
    raise TaskRecoveryFailed('Courtyard claim popup did not close after one input per layer')


def courtyard_claim_succeeded(image):
    """The user's 领取成功 title alone confirms this courtyard claim."""
    if not isinstance(image,np.ndarray) or image.ndim!=3 or abs(image.shape[1]-1280)>3 or abs(image.shape[0]-720)>3:
        return False
    crop=image[10:95,275:510]
    return cv2.minMaxLoc(cv2.matchTemplate(crop,_template('courtyard_claim_success.png'),
                                          cv2.TM_CCOEFF_NORMED))[1]>=.92
