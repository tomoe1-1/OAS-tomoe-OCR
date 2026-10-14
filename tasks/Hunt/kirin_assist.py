"""Confirm only the Kirin assist warning, once, before entering battle."""
from pathlib import Path
from module.atom.image import RuleImage
from module.device.confirmation_checkbox import ensure_confirmation_option
from module.exception import TaskRecoveryFailed

ASSETS=Path(__file__).resolve().parent/'kirin'
QUESTION=RuleImage(roi_front=(440,283,400,60),roi_back=(430,273,420,80),
                   threshold=.95,method='Template matching',file=str(ASSETS/'kirin_assist_question.png'))
CONFIRM=RuleImage(roi_front=(669,415,167,48),roi_back=(659,405,187,68),
                  threshold=.95,method='Template matching',file=str(ASSETS/'kirin_assist_confirm.png'))

def kirin_assist_visible(task):
    return task.appear(QUESTION) and task.appear(CONFIRM)

def confirm_kirin_assist(task):
    device=task.device
    generation=device._escape_generation
    invitation=getattr(device,'_accepted_invitation_generation',0)
    frame=device.image_frame_id
    def fresh(predicate):
        def checked():
            nonlocal frame
            current=device.image_frame_id
            if current is None or current==frame:
                raise TaskRecoveryFailed('Kirin confirmation requires fresh frames')
            frame=current
            if device._escape_generation!=generation or getattr(device,'_accepted_invitation_generation',0)!=invitation:
                raise TaskRecoveryFailed('Kirin confirmation interrupted')
            return predicate()
        return checked
    task._ui_verify(fresh(lambda:kirin_assist_visible(task)),[],timeout=5)
    point=(752,439)
    ensure_confirmation_option(task,point,'KIRIN_ASSIST_CONFIRM')
    if device._escape_generation!=generation or getattr(device,'_accepted_invitation_generation',0)!=invitation or not kirin_assist_visible(task):
        raise TaskRecoveryFailed('Kirin assist dialog changed before confirmation')
    if device.click(*point,control_name='KIRIN_ASSIST_CONFIRM') is False:
        raise TaskRecoveryFailed('Kirin assist confirmation blocked')
    frame=device.image_frame_id
    task._ui_verify(fresh(lambda:not kirin_assist_visible(task)),['KIRIN_ASSIST_CONFIRM'],timeout=15)
    task._ui_verify(fresh(lambda:task.appear(task.I_PREPARE_HIGHLIGHT) or task.is_in_battle(False)),[],timeout=15)
