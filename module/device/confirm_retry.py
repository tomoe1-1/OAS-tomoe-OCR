"""Explicit, bounded retry for audited non-consuming modal controls."""
import cv2

from module.device.click_verification import ClickResponseUnchanged, changed
from module.exception import TaskRecoveryFailed
from module.logger import logger
from module.device.confirmation_checkbox import ensure_confirmation_option


def context_signature(image):
    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), (96, 54))


def click_confirm_once(task, match, controls, *, dismiss_anywhere=False):
    """match must identify this modal's button; disappearance is the destination.

    Only audited confirmation/dismiss callers may use this. Never use for a
    purchase, reward claim, battle start, or other consuming action.

    Returns True when the modal is gone, whether this call dismissed it or it
    left on its own. `False` is reserved for a refused input, so callers never
    read a vanished modal as a blocked one.
    """
    device = task.device
    generation = device._escape_generation
    task.screenshot()
    target = match()
    if device._escape_generation != generation:
        raise TaskRecoveryFailed('Confirmation interrupted by recovery')
    if target is None:
        # Disappearance is the destination. The modal may close between the
        # caller's own check and this fresh frame; that is success, not a
        # blocked input.
        logger.info('Confirmation already gone before input; treat as dismissed')
        return True
    original_receipt = getattr(device, '_verified_click', None)
    if (not dismiss_anywhere and original_receipt
            and original_receipt.get('name') in {control.name for control in controls}
            and (not original_receipt.get('changed') or original_receipt.get('confirm_retry_used'))):
        # Preserve the original confirm receipt and its existing retry budget.
        pass
    elif not dismiss_anywhere:
        ensure_confirmation_option(task, target.coord(), target.name)
        target = match()
        if target is None:
            raise TaskRecoveryFailed('Confirmation changed after reminder checkbox')
    context = context_signature(device.image)
    name = target.name
    x, y = target.coord()
    receipt = getattr(device, '_verified_click', None)
    # Re-entry after an exhausted retry must not issue a new first click.
    aliases = {control.name for control in controls}
    if receipt and receipt['name'] in aliases and receipt.get('confirm_retry_used'):
        raise ClickResponseUnchanged(f'Confirmation retry already used: {name}')
    if (receipt and receipt['name'] in aliases and receipt['name'] != name
            and not receipt['changed']):
        raise ClickResponseUnchanged(f'Confirmation recognizer changed after unverified input: {name}')
    pending_receipt = bool(receipt and receipt['name'] == name and not receipt['changed'])
    if pending_receipt and dismiss_anywhere:
        x, y = receipt['x'], receipt['y']
    if not pending_receipt:
        # Re-entry with an unconfirmed receipt observes it rather than sending
        # another "first" input (even if random coordinates moved far away).
        if device.click(x, y, control_name=name) is False:
            return False
    if device._escape_generation != generation:
        raise TaskRecoveryFailed('Confirmation interrupted after input')
    if task._ui_verify(lambda: match() is None, controls, timeout=2, fail=False):
        return True
    if device._escape_generation != generation:
        raise TaskRecoveryFailed('Confirmation interrupted while waiting')
    receipt = getattr(device, '_verified_click', None)
    if (not receipt or receipt['name'] != name or receipt['changed']
            or receipt.get('confirm_retry_used')):
        raise TaskRecoveryFailed(f'Confirmation still present; retry not eligible: {name}')
    last_frame = getattr(device, 'image_frame_id', None)
    # Two distinct captures must show the same target in the same context.
    for _ in range(2):
        task.screenshot()
        frame = getattr(device, 'image_frame_id', None)
        if (frame is None or frame == last_frame
                or device._escape_generation != generation):
            raise TaskRecoveryFailed(f'Confirmation retry lacks fresh frames: {name}')
        last_frame = frame
        target = match()
        if target is None:
            return task._ui_verify(lambda: match() is None, controls)
        # Reward dismiss is an audited full-screen tap, not a located button.
        # Keep its original point only after matching the same modal/context.
        nx, ny = (x, y) if dismiss_anywhere else target.coord()
        if (target.name != name or abs(nx-x) >= 95 or abs(ny-y) >= 65
                or abs(nx-receipt['x']) >= 95 or abs(ny-receipt['y']) >= 65
                or changed(context, context_signature(device.image))):
            raise TaskRecoveryFailed(f'Confirmation context changed; retry cancelled: {name}')
    if device._escape_generation != generation:
        raise TaskRecoveryFailed('Confirmation retry interrupted by recovery')
    permit = dict(receipt=receipt, generation=generation, used=False)
    device._confirm_retry_permit = permit
    try:
        logger.info(f'Reidentified unchanged confirmation; retry once: {name}')
        if device.click(nx, ny, control_name=name) is False:
            return False
        if not permit['used']:
            raise TaskRecoveryFailed('Confirmation input did not consume its retry permit')
    finally:
        device._confirm_retry_permit = None
    return task._ui_verify(lambda: match() is None, controls)
