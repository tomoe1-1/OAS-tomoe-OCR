"""User-authorized repeat policy scoped to the active Chess runner."""


def chess_repeat_unrestricted(device):
    guard = getattr(device, '_task_flow_guard', None)
    return (guard is not None
            and getattr(guard, '_entered', False) is True
            and getattr(guard, 'device', None) is device
            and getattr(guard, 'command', None) == 'Chess'
            and not getattr(device, '_handling_invitation', False))


def exploration_retry_guard(device):
    """Only the entered Exploration runner receives one unchanged-click retry."""
    guard = getattr(device, '_task_flow_guard', None)
    if (guard is not None and getattr(guard, '_entered', False) is True
            and getattr(guard, 'device', None) is device
            and getattr(guard, 'command', None) == 'Exploration'
            and not getattr(device, '_handling_invitation', False)):
        return guard
    return None
