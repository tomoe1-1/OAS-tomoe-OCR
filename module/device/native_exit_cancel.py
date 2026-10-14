"""Bounded, device-scoped cancellation of the Android exit-game modal only."""
from time import monotonic
import cv2
import numpy as np

from module.device.escape_popup import game_exit_cancel_point, game_exit_white_panel, game_exit_dialog_present
from module.exception import TaskRecoveryFailed
from module.logger import logger


def _generation(device):
    return (getattr(device, '_escape_generation', 0),
            getattr(device, '_accepted_invitation_generation', 0))


def _signature(image):
    height, width = image.shape[:2]
    panel = image[round(height*.33):round(height*.67), round(width*.25):round(width*.76), :3]
    return cv2.resize(cv2.cvtColor(panel, cv2.COLOR_RGB2GRAY), (96, 48))


def _same_dialog(image, signature):
    delta = np.abs(_signature(image).astype(np.int16)-signature.astype(np.int16))
    return float(np.mean(delta > 25)) <= .02


def cancel_game_exit_dialog(device, *, deadline=None):
    """Cancel, verify two fresh absent frames; retry once only on the same modal.

    Native Android UI is targeted with the selected device's ADB input channel,
    rather than a game's window-message surface. No exit/confirm input exists.
    Input allowance and deadline survive re-entry while a result is uncertain.
    """
    point = game_exit_cancel_point(getattr(device, 'image', None))
    if point is None:
        if game_exit_dialog_present(getattr(device, 'image', None)):
            raise TaskRecoveryFailed('Exit-game dialog identity visible but Cancel cannot be safely located')
        return False
    if getattr(device, '_native_exit_dialog_handling', False):
        raise TaskRecoveryFailed('Native exit cancellation is already in progress')
    generation = _generation(device)
    performance = getattr(device, 'performance', None)
    timeout = performance.timeout(12) if performance is not None else 12
    local_deadline = monotonic()+timeout
    if deadline is not None:
        local_deadline = min(local_deadline, deadline)
    receipt = getattr(device, '_native_exit_cancel_receipt', None)
    if receipt is None or receipt['generation'] != generation:
        receipt = dict(generation=generation, point=point, shape=device.image.shape,
                       signature=_signature(device.image), attempts=0, frames=0,
                       deadline=local_deadline, uncertain=False)
        device._native_exit_cancel_receipt = receipt
    else:
        receipt['deadline'] = min(receipt['deadline'], local_deadline)
    if receipt['uncertain']:
        raise TaskRecoveryFailed('Native exit cancel input result is uncertain; refusing replay')
    original_handling = getattr(device, '_native_exit_dialog_handling', False)
    device._native_exit_dialog_handling = True
    last_frame = getattr(device, 'image_frame_id', None)

    def guard():
        if _generation(device) != generation:
            raise TaskRecoveryFailed('Native exit cancellation interrupted by recovery or invitation')
        if monotonic() >= receipt['deadline'] or receipt['frames'] >= 64:
            raise TaskRecoveryFailed('Native exit cancellation exhausted its time/frame budget')

    def capture():
        nonlocal last_frame
        guard()
        device.sleep(.15)
        device.screenshot()
        receipt['frames'] += 1
        frame = getattr(device, 'image_frame_id', None)
        if frame is None or frame == last_frame:
            raise TaskRecoveryFailed('Native exit cancellation needs distinct new frames')
        last_frame = frame
        guard()

    def reidentify():
        for _ in range(2):
            capture()
            current = game_exit_cancel_point(device.image)
            if (current is None or device.image.shape != receipt['shape']
                    or abs(current[0]-receipt['point'][0]) > 3
                    or abs(current[1]-receipt['point'][1]) > 3
                    or not _same_dialog(device.image, receipt['signature'])):
                raise TaskRecoveryFailed('Native exit dialog changed; cancel input withheld')
        guard()

    def closed():
        # A partially rendered/native white modal is not an absent dialog.
        return game_exit_cancel_point(device.image) is None and not game_exit_white_panel(device.image)

    try:
        while receipt['attempts'] < 2:
            reidentify()
            flow = getattr(device, '_task_flow_guard', None)
            if flow is not None:
                flow.check()
            guard()
            remaining = receipt['deadline']-monotonic()
            verify_budget = performance.timeout(2) if performance is not None else 2
            if remaining < verify_budget+.25:
                raise TaskRecoveryFailed('Native exit cancellation has no verification budget left')
            receipt['attempts'] += 1
            receipt['uncertain'] = True
            x,y = receipt['point']
            try:
                result = device.adb_shell(['input', 'tap', str(x), str(y)], timeout=min(5,remaining-verify_budget))
            except Exception as error:
                raise TaskRecoveryFailed('Unable to dispatch native Android Cancel input') from error
            if result is False or result is not None and str(result).strip():
                raise TaskRecoveryFailed(f'Native Android Cancel input reported an error: {result}')
            receipt['uncertain'] = False
            logger.info(f'Android exit-game Cancel dispatched via selected-device ADB: ({x}, {y}); attempt {receipt["attempts"]}/2')
            guard()
            observe_deadline = min(receipt['deadline'], monotonic()+(performance.timeout(2) if performance else 2))
            stable = 0
            while monotonic() < observe_deadline:
                capture()
                if closed():
                    stable += 1
                    if stable == 2:
                        device._native_exit_cancel_receipt = None
                        device.stuck_record_clear()
                        logger.info('Android exit-game dialog disappeared in two fresh frames')
                        return True
                else:
                    stable = 0
                    if (game_exit_cancel_point(device.image) is None
                            or device.image.shape != receipt['shape']
                            or not _same_dialog(device.image, receipt['signature'])):
                        raise TaskRecoveryFailed('Native dialog changed after Cancel; no repeated input')
            # Only the exact same reidentified native modal may receive one retry.
        raise TaskRecoveryFailed('Android exit-game dialog remained open after two Cancel inputs')
    finally:
        device._native_exit_dialog_handling = original_handling
