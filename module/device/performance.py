"""Device response budgets; independent of image/OCR recognition settings."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PerformanceProfile:
    low_spec: bool = False

    def timeout(self, seconds):
        return seconds * (3 if self.low_spec else 1)

    def screenshot_interval(self, seconds, combat=False):
        return max(seconds, 1.5 if combat else 0.8) if self.low_spec else seconds

    @property
    def click_interval(self):
        return 1.2 if self.low_spec else 0

    @property
    def click_limit(self):
        # The fourth repeated request is blocked before it reaches the game.
        return 4

    @property
    def alternating_click_limit(self):
        # Stop an A/B loop before either button reaches its fourth request.
        return 3
