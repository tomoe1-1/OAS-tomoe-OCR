"""Resume a failed task only on its original page; otherwise enter via home."""
import json
import re
from datetime import datetime
from pathlib import Path
from module.exception import TaskRecoveryFailed
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main


def page_key(config, device):
    task = GameUi(config=config, device=device)
    pages = task.navigator.all_pages()
    GameUi._prepare_page_rule_image_cache(task, pages)
    matches = [page for page in pages if task.match_page_once(page)]
    return matches[0].key if len(matches) == 1 else None


def checkpoint(config_name, command):
    if Path(config_name).name != config_name or Path(command).name != command:
        raise ValueError('Invalid recovery checkpoint name')
    return Path('log/task_retry') / config_name / (command + '.page.json')


def remember(config, device, config_name, command):
    path = checkpoint(config_name, command)
    write_switch(config_name, {'task': command})
    if path.exists():
        return
    original = getattr(device, '_failed_original_page', None)
    if original is None and not getattr(device, '_failed_original_page_captured', False):
        try:
            original = page_key(config, device)
        except Exception:
            original = None
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'page': original}), encoding='utf-8')
    temporary.replace(path)


def prepare(config, device, config_name, command):
    path = checkpoint(config_name, command)
    if not path.exists():
        store = getattr(config, 'failed_task_store', None)
        failures = store.read() if store is not None else {}
        if command not in failures:
            return 'normal'
        # Older failed tasks have no captured page. Restart through verified home.
        original = None
    else:
        original = json.loads(path.read_text('utf-8'))['page']
    task = GameUi(config=config, device=device)
    task._isolated_recovery_navigation = True
    destination = task.navigator.resolve_page(page_main)
    # Home takes priority even when the failed page itself was home.
    # Verify before resetting progress; never reset on a guessed/one-frame home.
    if task.match_page_once(destination):
        task._ui_verify(lambda: task.match_page_once(destination), [], timeout=12)
        reset_from_main(config, command)
        return 'main'
    if original and page_key(config, device) == original:
        task.screenshot()
        if page_key(config, device) == original:
            return 'original'
    task.screenshot()
    if not task.match_page_once(destination):
        from tasks.GameUi.recovery_exit import click_exit
        try:
            click_exit(task)
        except TaskRecoveryFailed:
            pass
    if not task.match_page_once(destination):
        if task.goto_page(page_main, skip_first_screenshot=False,
                          max_steps=64, total_timeout=180, recovery_navigation=True) is not True:
            raise TaskRecoveryFailed('Changed failure page could not return to main')
    task._ui_verify(lambda: task.match_page_once(destination), [], timeout=12)
    reset_from_main(config, command)
    return 'main'


def reset_from_main(config, command):
    """Restart only this task's checklist; keep actual spending/win receipts."""
    if command != 'DailyTrifles':
        # The runner constructs a fresh task instance after this preparation.
        # Confirmed challenge wins and cooldowns remain independent receipts.
        return
    record = config.daily_trifles.done_record
    previous = {name: getattr(record, name) for name in (
        'courtyard_affairs_dt', 'pickup_email_dt', 'guild_donate_dt',
        'luck_msg_dt', 'store_sign_dt', 'guild_donate_finish')}
    try:
        for name in previous:
            setattr(record, name, False if name == 'guild_donate_finish'
                    else datetime(2000, 1, 1))
        config.save()
    except Exception:
        for name, value in previous.items():
            setattr(record, name, value)
        raise


def clear(config_name, command):
    checkpoint(config_name, command).unlink(missing_ok=True)
    state = read_switch(config_name)
    if state and state['task'] == command:
        switch_checkpoint(config_name).unlink(missing_ok=True)


def switch_checkpoint(config_name):
    return checkpoint(config_name, 'switch').with_name('switch.json')


def write_switch(config_name, state):
    path = switch_checkpoint(config_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(path)


def read_switch(config_name):
    path = switch_checkpoint(config_name)
    if path.exists():
        state = json.loads(path.read_text('utf-8'))
        if state.get('task') == '' and state.get('home_verified') is True:
            return None
        if not isinstance(state.get('task'), str) or not state['task']:
            raise TaskRecoveryFailed('Invalid failed-task switch checkpoint')
        return state
    # Migrate an already running backend's most recent failed screen.
    pages = list(path.parent.glob('*.page.json'))
    if pages:
        # Existing page receipts retain their first-failure timestamp. Use the
        # most recent logged failure rather than mistaking file creation order
        # for the last task that actually failed.
        logfile = Path('log') / f'{datetime.now():%Y-%m-%d}_{config_name}.txt'
        if logfile.exists():
            with logfile.open('rb') as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 65536))
                text = handle.read().decode('utf-8', errors='replace')
            names = re.findall(r'Task `([A-Za-z][A-Za-z0-9]*)` failed; retry at', text)
            for name in reversed(names):
                if (path.parent / (name + '.page.json')).exists():
                    return {'task': name}
        latest = max(pages, key=lambda item: item.stat().st_mtime_ns)
        return {'task': latest.name.removesuffix('.page.json')}
    return None


def confirm_switch_home(config, device, config_name):
    """Passively verify home before releasing a deferred failed-task switch."""
    state = read_switch(config_name)
    if not state or state.get('policy') != 2:
        return False
    if not device.app_is_running():
        return False
    task = GameUi(config=config, device=device)
    destination = task.navigator.resolve_page(page_main)
    for _ in range(2):
        device.screenshot()
        if not task.match_page_once(destination):
            return False
    write_switch(config_name, {'task': '', 'home_verified': True})
    return True


def prepare_switch(config, device, config_name):
    """Verify home before constructing a different business task."""
    task = GameUi(config=config, device=device)
    task._isolated_recovery_navigation = True
    destination = task.navigator.resolve_page(page_main)
    device.reset_task_recovery()
    task.screenshot()
    # Already home: skip both the lottery probe and generic exits, and go straight
    # to verification. Only probe for a fenced lottery page when not yet home.
    if not task.match_page_once(destination):
        # A recognized lottery page has a verified route. Use it before generic
        # exit attempts whose unchanged arrow input may already be fenced.
        from tasks.KekkaiUtilize.page import page_guild_lottery
        lottery = task.navigator.resolve_page(page_guild_lottery)
        if task.match_page_once(lottery):
            if task.goto_page(page_main, skip_first_screenshot=False,
                              max_steps=8, total_timeout=90, recovery_navigation=True) is not True:
                raise TaskRecoveryFailed('Guild lottery could not return to verified main')
    if not task.match_page_once(destination):
        from tasks.GameUi.recovery_exit import click_exit
        for _ in range(4):
            try:
                changed = click_exit(task)
            except TaskRecoveryFailed:
                break
            if task.match_page_once(destination) or not changed:
                break
    if not task.match_page_once(destination):
        # The last failed mouse control may remain fenced. One Android BACK,
        # followed by a fresh frame, gives navigation a different exit path.
        device.recover_by_escape('Return to main before switching away from a failed task')
        # This is an isolated recovery navigator, not the old business stack.
        # A fresh but unknown intermediate page must still allow bounded exits.
        device.screenshot()
        task._seen_escape_generation = getattr(device, '_escape_generation', 0)
        from module.device.escape_popup import battle_exit_confirm_point
        from module.device.native_exit_cancel import cancel_game_exit_dialog
        cancel_game_exit_dialog(device)
        for detect, name in ((battle_exit_confirm_point, 'ESC_EXIT_BATTLE_CONFIRM'),):
            point = detect(device.image)
            if point is not None:
                if device.click(*point, control_name=name) is False:
                    raise TaskRecoveryFailed('Recovery exit confirmation was blocked')
                device.screenshot()
                device.screenshot()
                if detect(device.image) is not None:
                    raise TaskRecoveryFailed('Recovery exit confirmation remained open')
        if task.goto_page(page_main, skip_first_screenshot=False,
                          max_steps=64, total_timeout=180, recovery_navigation=True) is not True:
            raise TaskRecoveryFailed('Cannot switch tasks before verified main')
    task._ui_verify(lambda: task.match_page_once(destination), [], timeout=12)
    # Keep a clean-home receipt so old failed page files are not migrated again.
    write_switch(config_name, {'task': '', 'home_verified': True})
    return True
