"""协同对弈选人和上场阶段的 1280×720 界面规则。"""

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr


class DraftDuelAssets:
    O_SCREEN = RuleOcr(roi=(0, 0, 1280, 720), area=(0, 0, 1280, 720),
                       mode='Full', method='Default', keyword='', name='draft_screen')
    # 选秀结束后的本局大厅；与“未开启”的新局入口、战斗准备页区分。
    I_POST_DRAFT_ROSTER = RuleImage(
        roi_front=(420, 18, 37, 80), roi_back=(395, 0, 85, 125),
        threshold=0.83, method='Multi-scale template matching',
        file='./tasks/DraftDuel/post_draft_roster.png',
    )
    I_LOBBY_BATTLE = RuleImage(
        roi_front=(1163, 585, 84, 86), roi_back=(1110, 535, 170, 175),
        threshold=0.85, method='Multi-scale template matching',
        file='./tasks/DraftDuel/lobby_battle.png',
    )
    I_EMPTY_TEAMMATE = RuleImage(
        roi_front=(980, 320, 70, 70), roi_back=(970, 310, 90, 90),
        threshold=0.83, method='Template matching',
        file='./tasks/DraftDuel/empty_teammate.png',
    )
    C_START = RuleClick(roi_front=(1175, 605, 50, 50),
                        roi_back=(1175, 605, 50, 50), name='draft_start')
    O_ROUND = RuleOcr(roi=(450, 120, 380, 55), area=(450, 120, 380, 55),
                      mode='Single', method='Default', keyword='', name='draft_round')
    O_NAMES = (
        RuleOcr(roi=(190, 652, 150, 55), area=(190, 652, 150, 55),
                mode='Single', method='Default', keyword='', name='draft_left_name'),
        RuleOcr(roi=(555, 652, 180, 55), area=(555, 652, 180, 55),
                mode='Single', method='Default', keyword='', name='draft_middle_name'),
        RuleOcr(roi=(970, 652, 150, 55), area=(970, 652, 150, 55),
                mode='Single', method='Default', keyword='', name='draft_right_name'),
    )
    C_SELECT = (
        RuleClick(roi_front=(350, 625, 95, 75), roi_back=(350, 625, 95, 75),
                  name='draft_left_select'),
        RuleClick(roi_front=(720, 625, 95, 75), roi_back=(720, 625, 95, 75),
                  name='draft_middle_select'),
        RuleClick(roi_front=(1090, 625, 95, 75), roi_back=(1090, 625, 95, 75),
                  name='draft_right_select'),
    )
    O_BATTLE_CONFIRM = RuleOcr(
        roi=(1060, 540, 210, 110), area=(1060, 540, 210, 110),
        mode='Single', method='Default', keyword='', name='battle_confirm')
    C_DETAIL_CARDS = tuple(
        RuleClick(roi_front=(x-20, 46, 40, 42),
                  roi_back=(x-20, 46, 40, 42), name=f'battle_detail_{index}')
        for index, x in enumerate((115, 190, 265, 335, 405, 480))
    )
    O_DETAIL_TITLE = RuleOcr(
        roi=(535, 20, 210, 50), area=(535, 20, 210, 50),
        mode='Single', method='Default', keyword='', name='detail_title')
    C_MINE_TAB = RuleClick(roi_front=(1103, 664, 28, 25),
                           roi_back=(1103, 664, 28, 25), name='detail_mine')
    C_DETAIL_CLOSE = RuleClick(roi_front=(1170, 93, 60, 60),
                               roi_back=(1170, 93, 60, 60), name='detail_close')
    O_LINEUP_NAMES = tuple(
        RuleOcr(roi=(x, 172, 190, 40), area=(x, 172, 190, 40),
                mode='Single', method='Default', keyword='', name=f'lineup_name_{index}')
        for index, x in enumerate((230, 420, 615, 805, 995))
    )
    O_LINEUP_SPEEDS = tuple(
        RuleOcr(roi=(x, 346, 85, 31), area=(x, 346, 85, 31),
                mode='Single', method='Default', keyword='', name=f'lineup_speed_{index}')
        for index, x in enumerate((278, 468, 663, 853, 1043))
    )
    O_LINEUP_DEFENSES = tuple(
        RuleOcr(roi=(x, 303, 85, 31), area=(x, 303, 85, 31),
                mode='Single', method='Default', keyword='', name=f'lineup_defense_{index}')
        for index, x in enumerate((278, 468, 663, 853, 1043))
    )
    C_BATTLE_CARD = tuple(
        RuleClick(roi_front=(x, 638, 70, 28), roi_back=(x, 638, 70, 28),
                  name=f'battle_card_{index}')
        for index, x in enumerate((290, 405, 520, 635, 750))
    )
    C_BATTLE_CONFIRM = RuleClick(
        roi_front=(1120, 565, 100, 100), roi_back=(1120, 565, 100, 100),
        name='battle_confirm')
    O_LOBBY_SCORE = RuleOcr(
        roi=(98, 81, 240, 123), area=(98, 81, 240, 123),
        mode='Single', method='Default', keyword='', name='lobby_score')
    O_LOBBY_BUTTON = RuleOcr(
        roi=(1140, 555, 120, 125), area=(1140, 555, 120, 125),
        mode='Single', method='Default', keyword='', name='lobby_button')
    O_RESULT_CONTINUE = RuleOcr(
        roi=(492, 657, 290, 55), area=(492, 657, 290, 55),
        mode='Single', method='Default', keyword='', name='result_continue')
    O_TERMINAL_RESULT = RuleOcr(
        roi=(460, 140, 380, 190), area=(460, 140, 380, 190),
        mode='Single', method='Default', keyword='', name='terminal_result')
    O_FINAL_BANNER = RuleOcr(
        roi=(420, 205, 450, 235), area=(420, 205, 450, 235),
        mode='Single', method='Default', keyword='', name='final_banner')
    O_TEAMMATE_LEAVE = RuleOcr(
        roi=(430, 260, 420, 210), area=(430, 260, 420, 210),
        mode='Single', method='Default', keyword='', name='teammate_leave')
    O_BATTLE_DEFEAT = RuleOcr(
        roi=(650, 30, 365, 185), area=(650, 30, 365, 185),
        mode='Single', method='Default', keyword='', name='battle_defeat')
    C_TEAMMATE_LEAVE_CONFIRM = RuleClick(
        roi_front=(705, 398, 70, 45), roi_back=(705, 398, 70, 45),
        name='teammate_leave_confirm')
    C_SETTLEMENT_DISMISS = RuleClick(
        roi_front=(620, 670, 45, 35), roi_back=(620, 670, 45, 35),
        name='settlement_dismiss')
    O_BOUNTY_REJECT = RuleOcr(
        roi=(885, 477, 120, 83), area=(885, 477, 120, 83),
        mode='Single', method='Default', keyword='', name='bounty_reject')
    C_BOUNTY_REJECT = RuleClick(
        roi_front=(818, 485, 80, 72), roi_back=(818, 485, 80, 72),
        name='bounty_reject')
    O_FRIEND_CANCEL = RuleOcr(
        roi=(435, 434, 165, 65), area=(435, 434, 165, 65),
        mode='Single', method='Default', keyword='', name='friend_cancel')
    C_FRIEND_CANCEL = RuleClick(
        roi_front=(479, 443, 95, 49), roi_back=(479, 443, 95, 49),
        name='friend_cancel')
    C_RESULT_CONTINUE = RuleClick(
        roi_front=(605, 662, 75, 54), roi_back=(605, 662, 75, 54),
        name='result_continue')

    # 邀请面板（队长在组队大厅点右侧加号后弹出；与普通组队邀请面板布局不同）
    # 好友分类标签：好友/最近/寮友/跨区
    O_F_LIST_1 = RuleOcr(roi=(324, 93, 92, 48), area=(324, 93, 92, 48),
                         mode='Single', method='Default', keyword='', name='draft_f_list_1')
    O_F_LIST_2 = RuleOcr(roi=(443, 93, 87, 48), area=(443, 93, 87, 48),
                         mode='Single', method='Default', keyword='', name='draft_f_list_2')
    O_F_LIST_3 = RuleOcr(roi=(553, 93, 87, 48), area=(553, 93, 87, 48),
                         mode='Single', method='Default', keyword='', name='draft_f_list_3')
    O_F_LIST_4 = RuleOcr(roi=(662, 93, 87, 48), area=(662, 93, 87, 48),
                         mode='Single', method='Default', keyword='', name='draft_f_list_4')
    # 好友列表两列：左列/右列
    O_FRIEND_NAME_1 = RuleOcr(roi=(425, 130, 90, 380), area=(425, 130, 90, 380),
                              mode='Full', method='Default', keyword='', name='draft_friend_name_1')
    O_FRIEND_NAME_2 = RuleOcr(roi=(720, 130, 125, 380), area=(720, 130, 125, 380),
                              mode='Full', method='Default', keyword='', name='draft_friend_name_2')
    I_INVITE_ENSURE = RuleClick(
        roi_front=(708, 549, 101, 46), roi_back=(660, 525, 200, 95),
        name='draft_invite_ensure')
    I_INVITE_CANCEL = RuleClick(
        roi_front=(452, 549, 101, 46), roi_back=(410, 525, 190, 95),
        name='draft_invite_cancel')
    # 队员端：收到队长的协同对弈邀请弹窗，底部“邀”按钮（图2，中心 ≈(557,277)）
    O_INVITE_ACCEPT = RuleOcr(
        roi=(530, 248, 55, 59), area=(530, 248, 55, 59),
        mode='Single', method='Default', keyword='', name='draft_invite_accept')

    # ===== 协同对弈大厅邀请好友（队长侧）=====
    # 邀请面板分类标签：好友/最近/寮友/跨区（1280×720 截图换算）
    C_INVITE_TAB_FRIEND = RuleClick(
        roi_front=(370, 116, 60, 40), roi_back=(370, 116, 60, 40),
        name='draft_invite_tab_friend')
    C_INVITE_TAB_RECENT = RuleClick(
        roi_front=(486, 116, 60, 40), roi_back=(486, 116, 60, 40),
        name='draft_invite_tab_recent')
    C_INVITE_TAB_GUILD = RuleClick(
        roi_front=(596, 116, 60, 40), roi_back=(596, 116, 60, 40),
        name='draft_invite_tab_guild')
    C_INVITE_TAB_CROSS = RuleClick(
        roi_front=(705, 116, 60, 40), roi_back=(705, 116, 60, 40),
        name='draft_invite_tab_cross')
    # 面板已打开的标志：识别“跨区”标签文字
    O_INVITE_PANEL_OPEN = RuleOcr(
        roi=(662, 93, 88, 48), area=(662, 93, 88, 48),
        mode='Single', method='Default', keyword='跨区', name='draft_invite_panel_open')
    # 好友列表两列（Full OCR，box 相对 roi，需加 roi 偏移）
    O_INVITE_NAMES_LEFT = RuleOcr(
        roi=(365, 191, 151, 300), area=(365, 191, 151, 300),
        mode='Full', method='Default', keyword='', name='draft_invite_names_left')
    O_INVITE_NAMES_RIGHT = RuleOcr(
        roi=(699, 191, 101, 300), area=(699, 191, 101, 300),
        mode='Full', method='Default', keyword='', name='draft_invite_names_right')
    # 底部邀请/取消按钮
    C_INVITE_CONFIRM = RuleClick(
        roi_front=(765, 575, 60, 40), roi_back=(765, 575, 60, 40),
        name='draft_invite_confirm')
    C_INVITE_CANCEL = RuleClick(
        roi_front=(509, 575, 60, 40), roi_back=(509, 575, 60, 40),
        name='draft_invite_cancel')

    # ===== 庭院收到协同对弈邀请（队员侧）=====
    # 弹窗标题“协同对弈”所在区域，用于检测邀请弹窗
    O_ACCEPT_TITLE = RuleOcr(
        roi=(228, 253, 82, 28), area=(228, 253, 82, 28),
        mode='Single', method='Default', keyword='协同对弈', name='draft_accept_title')
    # 接受按钮（对勾）
    C_ACCEPT_CONFIRM = RuleClick(
        roi_front=(132, 254, 46, 53), roi_back=(132, 254, 46, 53),
        name='draft_accept_confirm')
    # 拒绝按钮（叉）
    C_ACCEPT_REJECT = RuleClick(
        roi_front=(50, 254, 46, 53), roi_back=(50, 254, 46, 53),
        name='draft_accept_reject')
