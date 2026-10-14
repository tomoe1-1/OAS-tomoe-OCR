"""Read challenge cooldowns without treating unrelated counts as durations."""
import re
from datetime import timedelta


def parse_team_cooldown(text):
    text = re.sub(r'\s+', '', str(text))
    match = re.fullmatch(r'(?:(\d+)小时|(?:(\d+)时))?(?:(\d+)分(?:钟)?)?(?:(\d+)秒)?', text)
    if not match or not any(match.groups()):
        return None
    hours, short_hours, minutes, seconds = (int(value or 0) for value in match.groups())
    if minutes >= 60 or seconds >= 60 or hours + short_hours > 24:
        return None
    return timedelta(hours=hours + short_hours, minutes=minutes, seconds=seconds)
