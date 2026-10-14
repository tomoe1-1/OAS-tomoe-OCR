"""Recognize town from any of its exclusive entrance labels."""
from tasks.GameUi.assets import GameUiAssets
from module.atom.image import RuleImage


# Current leftmost town entrance, captured from the user's supplied image.
I_TOWN_GARDEN_LABEL = RuleImage(
    roi_front=(198, 141, 28, 62), roi_back=(86, 120, 447, 130),
    threshold=0.8, method="Template matching",
    file="./tasks/GameUi/page/town_garden_label.png",
)


TOWN_ENTRANCES = (
    GameUiAssets.I_TOWN_GOTO_MAIN,
    GameUiAssets.I_TOWN_GOTO_DUEL,
    GameUiAssets.I_TOWN_GOTO_DEMON_ENCOUNTER,
    GameUiAssets.I_TOWN_GOTO_HUNT,
    GameUiAssets.I_TOWN_GOTO_DRAFT_DUEL,
    GameUiAssets.I_TOWN_GOTO_HYAKKIYAKOU,
    I_TOWN_GARDEN_LABEL,
)


def town_visible(task) -> bool:
    # Each label belongs to town. Scenery is deliberately excluded: the old
    # dark background template also matches some courtyard skins.
    return any(task.appear(entrance) for entrance in TOWN_ENTRANCES)
