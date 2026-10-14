"""Advance identified Delegation NPC dialogue without replaying business inputs."""
from pathlib import Path
from time import monotonic
import cv2
import numpy as np
from module.atom.image import RuleImage
from module.exception import TaskRecoveryFailed

ASSETS = Path(__file__).resolve().parent / 'd'
SKIP = RuleImage(roi_front=(654,514,89,41), roi_back=(654,514,89,41), threshold=.94,
                 method='Template matching', file=str(ASSETS / 'dialogue_skip.png'))
PANEL = RuleImage(roi_front=(300,573,140,26), roi_back=(300,573,140,26), threshold=.94,
                  method='Template matching', file=str(ASSETS / 'dialogue_panel.png'))

def dialogue_visible(task):
    image = task.device.image
    return (isinstance(image, np.ndarray) and image.shape == (720,1280,3)
            and task.appear(SKIP) and task.appear(PANEL))

def _text(image):
    return cv2.cvtColor(image[610:695,280:890], cv2.COLOR_RGB2GRAY).copy()

def _changed(a,b):
    return np.mean(np.abs(a.astype(np.int16)-b.astype(np.int16)) > 20) > .002

def advance_delegation_dialogue(task):
    """Two stable fresh observations per caption, one bottom input per transition."""
    if not dialogue_visible(task):
        if task.appear(SKIP) or task.appear(PANEL):
            raise TaskRecoveryFailed('Delegation dialogue identity is incomplete')
        return False
    device = task.device
    generation = device._escape_generation
    invitation = getattr(device, '_accepted_invitation_generation', 0)
    guard = getattr(device, '_task_flow_guard', None)
    frame = device.image_frame_id
    deadline = monotonic() + device.performance.timeout(360)
    captures = 0
    def finished():
        if task.appear(SKIP) or task.appear(PANEL):
            return False
        return any(task.appear(marker) for marker in (
            task.I_REWARDS_MIN, task.I_D_START, task.I_D_BACK,
            task.I_REWARDS_GET, task.I_REWARDS_DONE, task.I_REWARDS_FALSE,
            task.I_D_CONFIRM))
    def observe(predicate):
        def check():
            nonlocal frame,captures
            captures += 1
            if (captures > 128 or monotonic() >= deadline
                    or device.image_frame_id is None or device.image_frame_id == frame
                    or device._escape_generation != generation
                    or getattr(device,'_accepted_invitation_generation',0) != invitation
                    or getattr(device,'_task_flow_guard',None) is not guard):
                raise TaskRecoveryFailed('Delegation dialogue observation interrupted or exhausted')
            frame = device.image_frame_id
            return predicate()
        return check
    for _ in range(16):
        previous = None
        def stable_caption():
            nonlocal previous
            if not dialogue_visible(task):
                return finished()
            current = _text(device.image)
            stable = previous is not None and not _changed(previous,current)
            previous = current
            return stable
        task._ui_verify(observe(stable_caption), [], timeout=12)
        if not dialogue_visible(task):
            return True
        before = _text(device.image)
        choice = next((rule for rule in (task.I_CHAT_1,task.I_CHAT_2)
                       if task.appear(rule)), None)
        if choice is not None:
            roi = tuple(choice.roi_front)
            task._ui_verify(observe(lambda: dialogue_visible(task)
                and task.appear(choice) and tuple(choice.roi_front)==roi), [], timeout=12)
            before = _text(device.image)
            x,y,w,h=roi
            point=(x+w//2,y+h//2)
            name='DELEGATION_DIALOGUE_CHOICE:'+choice.name
        else:
            point=(450,643)
            name='DELEGATION_DIALOGUE_ADVANCE'
        pending = getattr(device, '_delegation_dialogue_pending', None)
        if pending is not None and not _changed(pending, before):
            raise TaskRecoveryFailed('Delegation dialogue caption already received an unverified input')
        device._delegation_dialogue_pending = before.copy()
        if device.click(*point,control_name=name) is False:
            raise TaskRecoveryFailed('Delegation dialogue input was blocked')
        frame = device.image_frame_id
        # An unchanged caption never receives a second input. Shared receipt
        # protection remains intact; only verified text/foreground progress counts.
        task._ui_verify(observe(lambda: finished() or (dialogue_visible(task)
                               and (_changed(before,_text(device.image))
                                    or (choice is not None and not task.appear(choice))))),
                        [name], timeout=12)
        device._delegation_dialogue_pending = None
        if finished():
            task._ui_verify(observe(finished), [], timeout=12)
            return True
    raise TaskRecoveryFailed('Delegation dialogue exceeded sixteen transitions')
