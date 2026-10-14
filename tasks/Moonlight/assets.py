"""月映千灯截图识别素材；对应 as/moonlight/image.json。"""
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr

class MoonlightAssets:
    I_MOON_ENTRY = RuleImage(roi_front=(1180, 289, 54, 59), roi_back=(1115, 100, 165, 490), threshold=0.8, method="Template matching", file="./tasks/Moonlight/as/moonlight/moon_entry.png")
    I_MOON_LOBBY = RuleImage(roi_front=(151, 23, 130, 31), roi_back=(125, 10, 185, 65), threshold=0.8, method="Template matching", file="./tasks/Moonlight/as/moonlight/moon_lobby.png")
    I_MOON_ENTER_BATTLE = RuleImage(roi_front=(470, 110, 31, 119), roi_back=(435, 70, 110, 215), threshold=0.8, method="Template matching", file="./tasks/Moonlight/as/moonlight/moon_enter_battle.png")
    I_MOON_BATTLE = RuleImage(roi_front=(158, 30, 120, 28), roi_back=(125, 10, 190, 70), threshold=0.8, method="Template matching", file="./tasks/Moonlight/as/moonlight/moon_battle.png")
    I_MOON_CHALLENGE = RuleImage(roi_front=(1144, 609, 76, 34), roi_back=(1100, 568, 165, 132), threshold=0.8, method="Template matching", file="./tasks/Moonlight/as/moonlight/moon_challenge.png")
    O_MOON_RESOURCE = RuleOcr(roi=(1110, 24, 102, 39), area=(1110, 24, 102, 39), mode="Digit", method="Default", keyword="", name="moon_resource")
    O_MOON_REWARD_NOTICE = RuleOcr(roi=(410, 255, 445, 78), area=(410, 255, 445, 78), mode="Full", method="Default", keyword="", name="moon_reward_notice")

