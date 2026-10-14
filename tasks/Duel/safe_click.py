"""Keep Duel recognition intact and sample inside the round battle button."""
from module.atom.click import RuleClick
from module.atom.image import RuleImage


class DuelBattleButton(RuleImage):
    @classmethod
    def from_rule(cls, rule):
        return cls(
            roi_front=tuple(rule.roi_front), roi_back=tuple(rule.roi_back),
            method=rule.method, threshold=rule.threshold,
            file=rule.file, profile=rule.profile,
        )

    def coord(self):
        # Use the whole matched button, excluding its rectangular corners.
        x, y, width, height = self.roi_front
        padding = min(2, width / 8, height / 8)
        safe_width, safe_height = width - 2 * padding, height - 2 * padding
        safe_roi = (
            x + padding, y + padding,
            safe_width, safe_height,
        )
        click = RuleClick(
            roi_front=safe_roi, roi_back=safe_roi,
            name=self.name, profile=self.profile,
        )
        cx, cy = x + width / 2, y + height / 2
        for _ in range(32):
            point = click.coord()
            if ((point[0] - cx) / (safe_width / 2)) ** 2 + \
                    ((point[1] - cy) / (safe_height / 2)) ** 2 <= 1:
                return point
        # Bounded sampling fallback; no device input occurs during sampling.
        centre = (int(cx), int(cy), 1, 1)
        return RuleClick(centre, centre, name=self.name).coord()

    def coord_more(self):
        # A wider sampling profile must not expand the battle click area.
        return self.coord()
