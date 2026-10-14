"""Refresh page identity after ESC, without replaying a completed task."""
from time import monotonic
from module.device.escape_popup import battle_exit_confirm_point, game_exit_dialog_present, game_exit_cancel_point
from module.device.native_exit_cancel import cancel_game_exit_dialog
from module.exception import TaskRecoveryFailed
from module.logger import logger


def handle_escape_recovery(task):
    device = task.device
    generation = getattr(device, '_escape_generation', 0)
    if (not generation or generation == getattr(task, '_seen_escape_generation', 0)
            or getattr(device, '_recovery_handling', False)):
        return
    from tasks.GameUi.game_ui import GameUi

    device._recovery_handling = True
    try:
        task.interval_timer.clear()
        task.animates.clear()
        navigator = task if isinstance(task, GameUi) else GameUi(task.config, device)
        navigator.navigator.current_page = None
        navigator.navigator.last_enter_success_page_key = None
        navigator.navigator.edge_penalties.clear()
        confirmed_generation = None
        waited_generation = None
        performance = getattr(device, 'performance', None)
        timeout = performance.timeout(60) if performance is not None else 60
        isolated = getattr(task, '_isolated_recovery_navigation', False)
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            # The task loop resumes only with a fresh, recognized page.
            learning = getattr(device, '_error_learning', None)
            plan = learning.plan if learning is not None else None
            delay = plan['settle'] if plan and waited_generation != device._escape_generation else 0.8
            waited_generation = device._escape_generation
            device.sleep(delay)
            device.screenshot()
            cancel_point = game_exit_dialog_present(device.image)
            if cancel_point:
                cancel_game_exit_dialog(device, deadline=deadline)
                continue
            # screenshot() may have started another recovery generation.
            learning = getattr(device, '_error_learning', None)
            plan = learning.plan if learning is not None else None
            task._seen_escape_generation = device._escape_generation
            point = battle_exit_confirm_point(device.image)
            if point is not None:
                if confirmed_generation != device._escape_generation:
                    logger.info('ESC recovery: confirm exit battle dialog')
                    if device.click(*point, control_name='ESC_EXIT_BATTLE_CONFIRM') is not False:
                        confirmed_generation = device._escape_generation
                        if learning is not None:
                            learning.confirmed_exit()
                    else:
                        raise TaskRecoveryFailed('ESC recovery battle exit input was blocked')
                continue
            before_detection = device._escape_generation
            page = None
            if plan:
                preferred = navigator.navigator.pages.get(plan['page'])
                if preferred is not None:
                    page = navigator._detect_pages([preferred], skip_first_screenshot=True)
            if page is None:
                if isolated:
                    page = navigator._detect_current_page_with_fallback(
                        skip_first_screenshot=True, categories=navigator._default_detect_categories())
                else:
                    page = navigator._detect_current_page(skip_first_screenshot=True)
            if device._escape_generation != before_detection:
                continue
            if page is None:
                if isolated:
                    # Only an independent recovery navigator may continue bounded
                    # exits from an unknown intermediate frame. Old tasks unwind.
                    return
                continue
            navigator.navigator.current_page = page
            if learning is not None:
                learning.page_observed(page.key)
            guard = getattr(device, '_task_flow_guard', None)
            if (not isolated and guard is not None and getattr(guard, 'command', None) not in ('GotoMain', 'Restart')
                    and (page.key == 'main'
                         or page.key != getattr(device, '_failed_original_page', None))):
                raise TaskRecoveryFailed('Recovery reached main or changed the failed page; restart task from its entry')
            logger.info(f'ESC recovery: current page reidentified as {page.key}; resume target matching')
            device.stuck_record_clear()
            device.click_record_clear()
            return
        raise TaskRecoveryFailed(f'ESC recovery did not identify a page within {timeout}s')
    finally:
        device._recovery_handling = False
