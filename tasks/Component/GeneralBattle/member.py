"""Keep member-only behavior scoped to the active team workflow."""
from functools import wraps


def is_member_battle(task) -> bool:
    role = getattr(task, 'user_status', None)
    return bool(getattr(task, '_member_active', False)) or getattr(role, 'value', role) == 'member'


def member_mode(method):
    @wraps(method)
    def run(task, *args, **kwargs):
        previous = getattr(task, '_member_active', False)
        task._member_active = True
        try:
            return method(task, *args, **kwargs)
        finally:
            task._member_active = previous
    return run
