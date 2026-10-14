"""One input, fresh stable observations, then task-specific page verification."""
import time
import cv2
import numpy as np

from module.exception import TaskRecoveryFailed
from module.logger import logger
from module.device.repeat_policy import chess_repeat_unrestricted, exploration_retry_guard


class ClickResponseUnchanged(TaskRecoveryFailed):
    """A repeated input was fenced because the prior click had no response."""


def signature(image, x, y):
    if image is None:
        raise TaskRecoveryFailed('Click requires a current device screenshot')
    height, width = image.shape[:2]
    # Include nearby selection marks, button feedback and modal edges.
    crop = image[max(0, y-65):min(height, y+65), max(0, x-95):min(width, x+95)]
    if not crop.size:
        raise TaskRecoveryFailed('Click verification point is outside the screen')
    return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), (48, 32))


def changed(first, second):
    return float(np.mean(np.abs(first.astype(np.int16)-second.astype(np.int16)) > 30)) > .08


def _exploration_context(device):
    return cv2.resize(cv2.cvtColor(device.image, cv2.COLOR_RGB2GRAY), (96, 54))


def _exploration_monster_context(device, previous, name):
    """Revalidate the original monster on the current exploration page."""
    target = previous.get('exploration_monster_target')
    if target is None:
        return None
    guard = exploration_retry_guard(device)
    if guard is None or previous.get('exploration_guard') is not guard:
        return False
    from tasks.Exploration import page as pages
    from tasks.GameUi.page_definition import sort_pages_by_priority
    button, roi = target
    task = guard.task
    if (getattr(button, 'name', None) != name
            or task.get_current_page() != pages.page_exp_main):
        return False
    # Navigation's second capture rechecks only its first-frame candidates.
    # A newly opened modal must also be considered on this final frame.
    candidates = [(index, page) for index, page in enumerate(
        task.navigator.all_pages(task._default_detect_categories()))
        if task.match_page_once(page)]
    matched = sort_pages_by_priority(candidates)
    if (not matched or matched[0] != pages.page_exp_main
            or not task.appear(button)
            or tuple(button.roi_front) != roi):
        return False
    x, y, width, height = roi
    return (x <= previous['x'] < x + width
            and y <= previous['y'] < y + height)


def _exploration_settlement_state(device, name):
    if name != 'SETTLEMENT_RANDOM_CLICK':
        return None
    guard = exploration_retry_guard(device)
    if guard is None:
        return None
    # Background characters keep animating after a failed continuation touch.
    # Match the actual result/reward page instead of demanding still scenery.
    from tasks.GameUi.default_pages import reward_details_visible
    from tasks.GameUi.page import page_battle_result, page_reward
    task = guard.task
    if (reward_details_visible(task) or task.appear(task.I_OVER_GHOST)
            or task.appear(task.I_GB_SKIN_CONFIRM)):
        return None
    state = (task.match_page_once(page_battle_result), task.match_page_once(page_reward))
    return state if any(state) else None


def _exploration_settlement_left(device, name, previous, timeout=8):
    """Give a slow settlement page time to leave before the retry budget is spent.

    ``after_click`` normally decides ``changed`` from two fresh frames, whose
    median wait is 0.81s. Measured 2026-10-11 on tomoe2: the exploration reward
    page needs a median 2.65s (max 8.38s) to move on, so a median 0.81s verdict
    declared 50 of 138 ``changed=False`` results dead while the very next
    ``[UI]`` line showed the page had already advanced. Spending the third and
    final input on that verdict aborted a click that had worked.

    Only the settlement retry path consults this. It re-checks the semantic page
    state, not the local pixel patch, and returns ``None`` when the screen is
    still a settlement page or the caller may not judge it at all.
    """
    if name != 'SETTLEMENT_RANDOM_CLICK':
        return None
    guard = exploration_retry_guard(device)
    if guard is None or previous.get('exploration_guard') is not guard:
        return None
    settlement = previous.get('exploration_settlement')
    if settlement is None:
        return None
    deadline = time.monotonic() + timeout
    while True:
        device.screenshot()
        if (exploration_retry_guard(device) is not guard
                or getattr(device, '_escape_generation', 0) != previous.get('generation')
                or getattr(device, '_accepted_invitation_generation', 0) != previous.get('invitation')
                or getattr(device, '_task_flow_progress_generation', 0) != previous.get('progress')):
            return None
        state = _exploration_settlement_state(device, name)
        if state is None or state != settlement:
            logger.info(f'Exploration settlement page advanced after an unchanged '
                        f'local patch: {name}')
            return True
        if time.monotonic() >= deadline:
            return False


def _exploration_retry(device, previous, name):
    guard = exploration_retry_guard(device)
    if guard is None or previous.get('exploration_guard') is not guard:
        return False
    generation = getattr(device, '_escape_generation', 0)
    invitation = getattr(device, '_accepted_invitation_generation', 0)
    progress = getattr(device, '_task_flow_progress_generation', 0)
    if (previous.get('generation') != generation
            or previous.get('invitation') != invitation
            or previous.get('progress') != progress):
        raise ClickResponseUnchanged(f'Exploration retry invalidated by state change: {name}')
    if (previous.get('confirm_retry_used') or previous.get('exploration_retry_pending')
            or previous.get('exploration_retry_count', 0) >= 2):
        raise ClickResponseUnchanged(f'Exploration unchanged click retry budget exhausted: {name}')
    if name == 'SETTLEMENT_RANDOM_CLICK' and previous.get('exploration_settlement') is None:
        raise ClickResponseUnchanged(f'Exploration continuation lacks a confirmed settlement: {name}')
    frame = getattr(device, 'image_frame_id', None)
    for _ in range(2):
        device.screenshot()
        settlement = previous.get('exploration_settlement')
        if settlement is not None:
            same_context = _exploration_settlement_state(device, name) == settlement
        else:
            monster_context = _exploration_monster_context(device, previous, name)
            same_context = (monster_context if monster_context is not None else
                            not changed(previous['exploration_context'], _exploration_context(device)))
        fresh = getattr(device, 'image_frame_id', None)
        if (fresh is None or fresh == frame
                or getattr(device, '_verified_click', None) is not previous
                or exploration_retry_guard(device) is not guard
                or getattr(device, '_escape_generation', 0) != generation
                or getattr(device, '_accepted_invitation_generation', 0) != invitation
                or getattr(device, '_task_flow_progress_generation', 0) != progress
                or not same_context):
            raise ClickResponseUnchanged(f'Exploration retry context changed: {name}')
        frame = fresh
    # Spend before dispatch; failed dispatch or observation cannot renew a permit.
    previous['exploration_retry_used'] = True
    previous['exploration_retry_count'] = previous.get('exploration_retry_count', 0) + 1
    previous['exploration_retry_pending'] = True
    logger.info(f"Exploration unchanged click: allow input {previous['exploration_retry_count'] + 1}/3: {name}")
    return True


def before_click(device, x, y, name, *, exploration_retry=False):
    """A failed input may not be replayed just because its interval expired."""
    image = getattr(device, 'image', None)
    if image is None:
        device.screenshot()
    if exploration_retry:
        device._exploration_input_anchor = (
            exploration_retry_guard(device), getattr(device, '_escape_generation', 0),
            getattr(device, '_accepted_invitation_generation', 0),
        )
    current = signature(device.image, x, y)
    if chess_repeat_unrestricted(device):
        return current
    previous = getattr(device, '_verified_click', None)
    permit = getattr(device, '_confirm_retry_permit', None)
    if permit is not None:
        if (previous is not permit['receipt'] or permit['used']
                or previous['name'] != name or previous['changed']
                or previous.get('confirm_retry_used')
                or previous.get('exploration_retry_used')
                or abs(previous['x']-x) >= 95 or abs(previous['y']-y) >= 65
                or permit['generation'] != getattr(device, '_escape_generation', 0)):
            raise TaskRecoveryFailed(f'Confirmation retry permit no longer matches: {name}')
        # Consume before dispatch; an input/observation error cannot refill it.
        permit['used'] = True
        previous['confirm_retry_used'] = True
        return current
    if (exploration_retry and previous and previous['name'] == name and not previous['changed']
            and _exploration_retry(device, previous, name)):
        return signature(device.image, x, y)
    if (previous and previous['name'] == name and not previous['changed']
            and abs(previous['x']-x) < 95 and abs(previous['y']-y) < 65):
        current = signature(device.image, previous['x'], previous['y'])
        deadline = time.monotonic() + 8
        while not changed(previous['before'], current):
            if time.monotonic() >= deadline:
                raise ClickResponseUnchanged(f'Click state unchanged; repeat blocked: {name}')
            device.screenshot()
            current = signature(device.image, previous['x'], previous['y'])
        logger.info(f'Click response verified before next input: {name}')
    return signature(device.image, x, y)


def after_click(device, x, y, name, before, *, exploration_retry=False):
    """Two fresh observations; semantic stability is checked by the task's rules."""
    deadline = time.monotonic() + 8
    generation = getattr(device, '_escape_generation', 0)
    first_receipt = getattr(device, '_verified_click', None)
    exploration_guard = exploration_retry_guard(device) if exploration_retry else None
    invitation = getattr(device, '_accepted_invitation_generation', 0)
    if (exploration_guard is not None
            and getattr(device, '_exploration_input_anchor', None)
            != (exploration_guard, generation, invitation)):
        raise TaskRecoveryFailed(f'Exploration click interrupted before observation: {name}')
    exploration_used = bool(exploration_guard is not None and first_receipt
                            and first_receipt.get('exploration_guard') is exploration_guard
                            and first_receipt['name'] == name
                            and not first_receipt['changed']
                            and first_receipt.get('exploration_retry_used'))
    exploration_count = first_receipt.get('exploration_retry_count', 0) if exploration_used else 0
    previous = None
    time.sleep(.25)
    while time.monotonic() < deadline:
        device.screenshot()
        if getattr(device, '_escape_generation', 0) != generation:
            raise TaskRecoveryFailed(f'Click verification interrupted by recovery: {name}')
        if exploration_guard is not None and (
                exploration_retry_guard(device) is not exploration_guard
                or getattr(device, '_accepted_invitation_generation', 0) != invitation
                or getattr(device, '_verified_click', None) is not first_receipt):
            raise TaskRecoveryFailed(f'Exploration click observation interrupted: {name}')
        current = signature(device.image, x, y)
        # Animated courtyard backgrounds are not semantic page instability.
        # The caller must match its expected destination on these fresh frames.
        if previous is not None:
            response_changed = changed(before, current)
            permit = getattr(device, '_confirm_retry_permit', None)
            retry_used = bool(permit and permit['used'])
            device._verified_click = dict(name=name, x=x, y=y, before=before,
                                          changed=response_changed,
                                          confirm_retry_used=retry_used,
                                          exploration_retry_used=exploration_used,
                                          exploration_retry_count=exploration_count,
                                          exploration_retry_pending=False,
                                          exploration_guard=exploration_guard,
                                          generation=generation,
                                          invitation=getattr(device, '_accepted_invitation_generation', 0),
                                          progress=getattr(device, '_task_flow_progress_generation', 0),
                                          exploration_context=_exploration_context(device)
                                          if exploration_guard is not None else None,
                                          exploration_settlement=_exploration_settlement_state(device, name)
                                          if exploration_guard is not None else None)
            logger.info(f'Click result captured in two fresh frames: {name}; changed={response_changed}')
            if exploration_count >= 2 and not response_changed:
                # The local patch can look still while the page is already
                # turning over. Spend the last input only after the settlement
                # page itself confirms it has not moved.
                if _exploration_settlement_left(device, name, first_receipt) is not False:
                    return
                raise ClickResponseUnchanged(f'Exploration click unchanged after three inputs: {name}')
            return
        previous = current
        time.sleep(.15)
    raise TaskRecoveryFailed(f'Click result did not settle: {name}')
