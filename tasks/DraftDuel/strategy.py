"""从《协同分析.xlsx》生成的资料中选择式神和推荐搭配。

本模块不依赖游戏画面。调用方传入当前可选式神及已选队伍，
仅在 OCR 能可靠识别名称后才调用 choose_pick。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


CATALOG_PATH = Path(__file__).with_name('catalog.json')
ENCYCLOPEDIA_PATH = Path(__file__).with_name('encyclopedia.json')
STRENGTH_PATH = Path(__file__).with_name('strength_scores.json')
TIER_POINTS = {'特等': 40, '上等': 30, '中等': 20, '下等': 10}
ENCYCLOPEDIA_POINTS = {'SS': 12, 'S': 8, 'A': 4, 'B': 0,
                       'C': -4, 'D': -8, 'E': -12}
USER_RATINGS = {
    '梦山白藏主': 'S',
    '灼华桃花妖': 'S',
    '蚀月吸血姬': 'S',
    '天火命铃彦姬': 'S',
}
USER_TIERS = {'莹草': '特等', '云间不见岳': '特等'}
SUMMON_SLOT_USERS = frozenset({'猫川', '千姬', '雪御前', '跳跳妹妹', '惠比寿'})
USER_ROLES = {
    '梦山白藏主': {'support'},
    '灼华桃花妖': {'sustain'},
    '蚀月吸血姬': {'sustain', 'damage'},
    '天火命铃彦姬': {'damage'},
}
ROLE_POINTS = {'fire': 12, 'sustain': 10, 'shield': 7, 'control': 6,
               'damage': 8, 'tempo': 6, 'support': 4}
SOUL_NAMES = (
    '伤魂鸟', '轮入道', '青女房', '返魂香', '雪幽魂', '钓瓶火',
    '遗念火', '招财', '火灵', '蚌精', '木魅', '薙魂', '地藏',
    '针女', '破势', '狂骨', '网切', '树妖', '珍珠', '狰',
    '魅妖', '魍魉', '钟灵', '日女', '共潜', '共浅', '镜姬',
    '贝吹坊', '出世螺', '隐念', '海月', '三味', '镇墓兽',
)

# 固定公式：1 鬼火 + 1 治疗 + 2 输出 + 1 控制/护盾/增伤。
# 鬼火上 1 只即可（双打火冗余）；该公式同时作用于选人与上阵评分。
FORMULA_ROLES = {'fire': 1, 'sustain': 1, 'damage': 2, 'flex': 1}
# 各类型推荐式神（catalog 规范名；首选在前）。
# 治疗推荐按用户公式：SP桃花妖（灼华桃花妖）、萤草（百科名“莹草”，R卡治疗）、日和坊。
ROLE_RECOMMEND = {
    'fire': ('薰', '因幡辉夜姬', '神无月', '千姬'),
    'sustain': ('灼华桃花妖', '莹草', '日和坊', '情思日和坊'),
    'control': ('百目鬼', '小松丸'),
    'shield': ('鲤鱼精', '一目连'),
    'damage': ('时曜泷夜叉姬', '炼狱茨木童子', '茨木童子'),
}
RECOMMEND_BONUS = 8      # 首选式神额外加分
RECOMMEND_BONUS_SECOND = 4  # 次选式神额外加分


@dataclass(frozen=True)
class Entry:
    name: str
    rarity: str
    role: str
    strength: str
    weakness: str
    notes: str
    souls: str
    tier: str
    source_row: int


@dataclass(frozen=True)
class Pick:
    name: str
    score: float
    reasons: tuple[str, ...]
    suggested_souls: tuple[str, ...]


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Entry]:
    data = json.loads(path.read_text(encoding='utf-8'))
    return {row['name']: Entry(**row) for row in data['entries']}


CATALOG = load_catalog()


def load_strength_scores(path: Path = STRENGTH_PATH) -> tuple[dict, tuple]:
    if not path.exists():
        return {}, ()
    data = json.loads(path.read_text(encoding='utf-8'))
    return data['scores'], tuple(data['combinations'])


STRENGTH_SCORES, COMBINATION_RECOMMENDATIONS = load_strength_scores()


def load_encyclopedia(path: Path = ENCYCLOPEDIA_PATH) -> tuple[dict, dict, dict]:
    if not path.exists():
        return {}, {}, {}
    rows = json.loads(path.read_text(encoding='utf-8'))['entries']
    grouped = {}
    aliases = {}
    for row in rows:
        canonical = row['canonical_name']
        grouped.setdefault(canonical, []).append(row)
        aliases[re.sub(r'[\s·•・&＆:：【】\[\]（）()]', '', row['name'])] = canonical
    # 云外镜有黑、白两行；用样本量较多的一行作评分依据，定位则可合并。
    primary = {
        name: max(items, key=lambda item: (
            (item['wins'] or 0) + (item['losses'] or 0),
            ENCYCLOPEDIA_POINTS.get(item['rating'], -99)))
        for name, items in grouped.items()
    }
    return primary, aliases, grouped


ENCYCLOPEDIA, NAME_ALIASES, ENCYCLOPEDIA_VARIANTS = load_encyclopedia()


def normalize_name(name: str, catalog: Mapping[str, Entry] = CATALOG) -> str | None:
    """接受完整名称或 OCR 前后混入少量属性字的唯一名称。"""
    if '?' in (name or '') or '？' in (name or ''):
        return None
    value = re.sub(r'[\s·•・&＆:：【】\[\]（）()]', '', name or '')
    # 阵容详情的描边字体会把“祢”识别成“称”。限定完整式神名。
    value = value.replace('灶门称豆子', '灶门祢豆子')
    if value in catalog:
        return value
    if value in NAME_ALIASES and NAME_ALIASES[value] in catalog:
        return NAME_ALIASES[value]
    value = re.sub(r'^(?:SP|SSR|SR|UR|R|联动)', '', value, flags=re.I)
    if value in catalog:
        return value
    if value in NAME_ALIASES and NAME_ALIASES[value] in catalog:
        return NAME_ALIASES[value]
    matches = [candidate for candidate in catalog if candidate in value]
    if not matches:
        # OCR 漏字：value 是某个完整名的前缀/子串（如“稻荷神御馔”漏“津”）。
        # 仅当唯一候选且 value 长度 >= 2 时才归一化，防止单字误匹配。
        sub_matches = [candidate for candidate in catalog
                       if value in candidate and len(value) >= 2]
        if len(sub_matches) == 1:
            return sub_matches[0]
        return None
    length = max(map(len, matches))
    longest = [candidate for candidate in matches if len(candidate) == length]
    return longest[0] if len(longest) == 1 else None


def role_tags(entry: Entry, *, defense: int | None = None) -> set[str]:
    if entry.name in USER_ROLES:
        return set(USER_ROLES[entry.name])
    if entry.name == '云外镜':
        # 白镜侧重驱散与拉条；防御面板不明时按黑镜保守估值。
        return {'support', 'tempo'} if defense is not None and defense >= 900 else {'damage'}
    role = entry.role
    tags = set()
    if '火机' in role or '鬼火' in role:
        tags.add('fire')
    if any(word in role for word in ('奶', '治疗', '复活')):
        tags.add('sustain')
    if any(word in role for word in ('盾', '减伤', '保单核', '抗单体')):
        tags.add('shield')
    if any(word in role for word in ('控制', '推条')):
        tags.add('control')
    if '输出' in role or '增伤' in role:
        tags.add('damage' if '输出' in role else 'support')
    if '拉条' in role:
        tags.add('tempo')
    if '辅助' in role or '驱散' in role or '解控' in role:
        tags.add('support')
    extra = ENCYCLOPEDIA.get(entry.name, {}).get('role', '')
    if extra == '打火':
        tags.add('fire')
    elif extra == '治疗':
        tags.add('sustain')
    elif extra == '护卫':
        tags.add('shield')
    elif extra == '控制':
        tags.add('control')
    elif extra == '输出':
        tags.add('damage')
    elif extra == '拉条':
        tags.add('tempo')
    elif extra == '综合':
        tags.add('support')
    return tags


def is_fire_supplier(name: str, catalog: Mapping[str, Entry] = CATALOG) -> bool:
    entry = catalog.get(name)
    return entry is not None and 'fire' in role_tags(entry)


def encyclopedia_points(name: str, *, team: Iterable[str] = (),
                        defense: int | None = None) -> float:
    """百科评级和胜率是辅助信号；小样本胜率向 50% 收缩。"""
    row = ENCYCLOPEDIA.get(name)
    if name == '云外镜':
        variant = '云外镜（白）' if defense is not None and defense >= 900 else '云外镜（黑）'
        row = next((item for item in ENCYCLOPEDIA_VARIANTS.get(name, ())
                    if item['name'] == variant), row)
    if not row:
        return 0.0
    samples = (row['wins'] or 0) + (row['losses'] or 0)
    rating = row['rating']
    if name in USER_RATINGS:
        rating = USER_RATINGS[name]
    if name == '茨木童子':
        rating = 'S' if '云间不见岳' in team else 'A'
    grade = ENCYCLOPEDIA_POINTS.get(rating, 0)
    user_rating = name in USER_RATINGS or name == '茨木童子'
    grade_weight = 1.0 if user_rating else min(1.0, max(0.5, samples / 300)) if samples else 0.5
    shrunk_winrate = ((row['wins'] or 0) + 50) / (samples + 100)
    winrate_points = max(-5, min(5, (shrunk_winrate - 0.5) * 40)) if samples else 0
    return grade * grade_weight + winrate_points


def tier_points(entry: Entry, team: Iterable[str] = (), *, speed: int | None = None,
                defense: int | None = None) -> int:
    """条件未满足时取保守等级；未知等级不凭稀有度加分。"""
    if entry.name in USER_TIERS:
        return TIER_POINTS[USER_TIERS[entry.name]]
    if entry.name == '梦山白藏主':
        return 40
    label = entry.tier
    if label in TIER_POINTS:
        return TIER_POINTS[label]
    if label in ('?', '？', ''):
        return 0
    names = set(team)
    if entry.name == '云外镜':
        return 40 if defense is not None and defense >= 900 else 20
    if entry.name == '茨木童子':
        return 40 if '云间不见岳' in names else 30
    if '220以上特等' in label and speed is not None:
        return 40 if speed >= 220 else 30 if speed >= 210 else 20
    if '220以上上等' in label and speed is not None:
        return 30 if speed >= 220 else 20 if speed >= 210 else 10
    if '170上等' in label and speed is not None:
        return 30 if speed >= 170 else 20
    if '火山队特等' in label or '火系特等' in label:
        return 40 if '火山队' in names else 30 if '上等' in label else 10
    if '有季特等' in label:
        return 40 if '季' in names else 30
    if 'SP千姬上等' in label:
        return 30 if '鲸汐千姬' in names else 20
    if '伤魂鸟上等' in label:
        return 30 if '伤魂鸟' in names else 20
    if '破势特等' in label:
        return 40 if '破势' in names else 30
    if '上等到特等之间' in label or '上等特等之间' in label:
        return 35
    if '单上中等' in label or '120速中等' in label:
        return 20
    if '中等' in label:
        return 20
    if '上等' in label:
        return 30
    if '特等' in label:
        return 40
    if '下等' in label:
        return 10
    return 0


def strength_points(entry: Entry, team: Iterable[str] = (), *, speed: int | None = None,
                    defense: int | None = None) -> float:
    """新表独立强度分；用户指定评级及条件形态保持优先。"""
    old = tier_points(entry, team, speed=speed, defense=defense)
    if entry.name in USER_TIERS or entry.name in USER_RATINGS or entry.name in (
            '云外镜', '茨木童子'):
        return float(old)
    score = STRENGTH_SCORES.get(entry.name, {}).get('strength')
    if not isinstance(score, (int, float)):
        return float(old)
    if entry.tier not in TIER_POINTS and entry.tier not in ('', '?', '？'):
        return float(old)
    points = ((60, 10), (74, 20), (85, 30), (94, 40))
    if score <= 60:
        return max(0.0, score / 6)
    for (low, low_points), (high, high_points) in zip(points, points[1:]):
        if score <= high:
            return low_points + (score - low) * (high_points - low_points) / (high - low)
    return min(45.0, 40.0 + (score - 94))


# 从“组合推荐”中提取可直接由式神名称判断的核心搭配。泛指“输出/火机”
# 由 role_tags 与队伍平衡评分处理，避免把说明文本当作可点选的式神。
COMBINATION_PAIRS = (
    ({'云间不见岳', '炼狱茨木童子'}, 9),
    ({'云间不见岳', '烬天玉藻前'}, 9),
    ({'鬼王酒吞童子', '禅心云外镜'}, 10),
    ({'浮世青行灯', '心狩鬼女红叶'}, 9),
    ({'食灵', '姑获鸟'}, 8),
    ({'食灵', '鬼童丸'}, 7),
    ({'食灵', '心友犬神'}, 7),
    ({'空相面灵气', '大夜摩天阎魔'}, 8),
    ({'龙珏', '歌留多'}, 10),
    ({'不知火', '酒吞童子'}, 8),
    ({'不知火', '吸血姬'}, 8),
    ({'鲸汐千姬', '初音未来'}, 9),
    ({'季', '犬神'}, 8),
    ({'一目连', '匣中少女'}, 7),
    ({'鲤鱼精', '匣中少女'}, 7),
    ({'蚀月吸血姬', '阎魔'}, 7),
    ({'神启荒', '大夜摩天阎魔'}, 7),
    ({'神启荒', '遥念烟烟罗'}, 7),
    ({'言灵', '鬼金羊'}, 9),
    ({'入内雀', '荒骷髅'}, 7),
)


def suggested_souls(entry: Entry) -> tuple[str, ...]:
    found = [(entry.souls.index(soul), soul) for soul in SOUL_NAMES
             if soul in entry.souls]
    result = [soul for _, soul in sorted(found)[:3]]
    extra = ENCYCLOPEDIA.get(entry.name)
    if extra and len(result) < 3:
        text = str(extra['summary']) + '\n' + str(extra['panel'])
        for soul in SOUL_NAMES:
            if soul in text and soul not in result:
                result.append(soul)
                if len(result) == 3:
                    break
    return tuple(result)


def team_score(names: Iterable[str], catalog: Mapping[str, Entry] = CATALOG,
               speeds: Mapping[str, int] | None = None,
               defenses: Mapping[str, int] | None = None) -> float:
    team = list(names)
    if len(team) != len(set(team)):
        raise ValueError('阵容中不能重复选择同名式神')
    unknown = [name for name in team if name not in catalog]
    if unknown:
        raise ValueError(f'未知式神: {unknown}')
    speeds = speeds or {}
    defenses = defenses or {}
    tags = [role_tags(catalog[name], defense=defenses.get(name)) for name in team]
    score = sum(strength_points(catalog[name], team, speed=speeds.get(name),
                            defense=defenses.get(name))
                + encyclopedia_points(name, team=team, defense=defenses.get(name))
                + ((STRENGTH_SCORES[name]['combo'] - 75) * 0.18
                   if name in STRENGTH_SCORES
                   and isinstance(STRENGTH_SCORES[name]['combo'], (int, float))
                   else 0)
                for name in team)
    for role, value in ROLE_POINTS.items():
        count = sum(role in item for item in tags)
        if count:
            score += value
            if role == 'damage' and count >= 2:
                score += 4
    # 固定公式：1 鬼火 + 1 治疗 + 2 输出 + 1 控制/护盾/增伤。
    # 单火上阵即可（双打火冗余，评分略减）；无火重罚。
    fire_count = sum('fire' in item for item in tags)
    sustain_count = sum('sustain' in item for item in tags)
    damage_count = sum('damage' in item for item in tags)
    flex_count = sum(bool({'control', 'shield'} & item) or 'support' in item
                     for item in tags)
    if fire_count == 1:
        score += 20
    elif fire_count >= 2:
        score -= 14 if len(team) >= 2 else 0
    else:
        score -= 30 if len(team) >= 2 else 0
    if sustain_count:
        score += 10
    elif len(team) >= 3:
        score -= 16
    if damage_count >= 2:
        score += 8
    elif len(team) >= 4:
        score -= 14
    if flex_count:
        score += 6
    elif len(team) >= 4:
        score -= 10
    score -= 20 * max(0, len(SUMMON_SLOT_USERS.intersection(team)) - 1)
    # 表中明确写出的正向搭配。仅命中实际同队名称才加分。
    pairs = (
        ({'初音未来', '鲸汐千姬'}, 9),
        ({'桃花妖', '季'}, 8),
        ({'桃花妖', '云外镜'}, 8),
        ({'桃花妖', '镜音铃连'}, 8),
        ({'稻荷神御馔津', '纺愿缘结神'}, 8),
        ({'犬神', '季'}, 8),
    )
    pair_scores = {frozenset(pair): points for pair, points in pairs}
    for pair, points in COMBINATION_PAIRS:
        pair_scores[frozenset(pair)] = points
    score += sum(points for pair, points in pair_scores.items() if pair <= set(team))
    if {'帝释天', '纺愿缘结神'} <= set(team):
        score -= 8  # 组合表提示两名单拉重复。
    return float(score)


def choose_pick(offers: Iterable[str], team: Iterable[str] = (), *,
                team_size: int = 5, speeds: Mapping[str, int] | None = None,
                defenses: Mapping[str, int] | None = None,
                catalog: Mapping[str, Entry] = CATALOG) -> Pick:
    """在当前候选中选一个；候选识别不完整时由界面层重试 OCR。"""
    current = tuple(team)
    if not 1 <= team_size <= 8 or len(current) >= team_size:
        raise ValueError('队伍人数配置无效或已满')
    resolved = [normalize_name(name, catalog) for name in offers]
    candidates = sorted({name for name in resolved if name and name not in current})
    if not candidates:
        raise ValueError('没有可识别且未入队的候选式神')
    existing_tags = [role_tags(catalog[name], defense=(defenses or {}).get(name))
                     for name in current]
    counts = {role: sum(role in item for item in existing_tags)
              for role in ('fire', 'sustain', 'damage', 'control', 'shield', 'support')}
    formula_hint = None

    def candidate_roles(name):
        return role_tags(catalog[name], defense=(defenses or {}).get(name))

    # 固定公式补位优先级：单鬼火 → 治疗 → 双输出 → 控制/护盾/增伤。
    # 鬼火不足 1 时优先补火（1 只打火即够，双打火冗余）；候选满足该角色时生效，
    # 候选不满足则回落到综合评分，避免公式卡死选人。
    if counts['fire'] < 1:
        pool = [name for name in candidates if 'fire' in candidate_roles(name)]
        if pool:
            candidates = pool
            formula_hint = '公式：鬼火不足 1，优先补火'
    if formula_hint is None and counts['sustain'] < 1:
        pool = [name for name in candidates if 'sustain' in candidate_roles(name)]
        if pool:
            candidates = pool
            formula_hint = '公式：缺少治疗，优先补治疗'
    if formula_hint is None and counts['damage'] < 2:
        pool = [name for name in candidates if 'damage' in candidate_roles(name)]
        if pool:
            candidates = pool
            formula_hint = '公式：输出不足 2，优先补输出'
    if formula_hint is None and counts['control'] + counts['shield'] + counts['support'] < 1:
        pool = [name for name in candidates
                if bool({'control', 'shield'} & candidate_roles(name))
                or 'support' in candidate_roles(name)]
        if pool:
            candidates = pool
            formula_hint = '公式：缺少控制/护盾/增伤，优先补齐'
    baseline = team_score(current, catalog, speeds, defenses)
    existing_roles = set().union(*existing_tags)
    ranked = []
    for name in candidates:
        entry = catalog[name]
        gain = team_score((*current, name), catalog, speeds, defenses) - baseline
        new_roles = role_tags(entry, defense=(defenses or {}).get(name)) - existing_roles
        source = STRENGTH_SCORES.get(name, {})
        reasons = [f'等级：{USER_TIERS.get(name, entry.tier)}']
        if isinstance(source.get('strength'), (int, float)):
            reasons.append(f"评分表：强度{source['strength']}，组合{source['combo']}")
        extra = ENCYCLOPEDIA.get(name)
        if extra:
            samples = (extra['wins'] or 0) + (extra['losses'] or 0)
            rating = extra['rating']
            if name in USER_RATINGS:
                rating = USER_RATINGS[name]
            if name == '茨木童子':
                rating = 'S' if '云间不见岳' in current else 'A'
            if name == '云外镜':
                rating = '白镜 A' if (defenses or {}).get(name, 0) >= 900 else '黑镜 D（形态未明按黑镜）'
            reasons.append(f'对弈百科：{rating}，{samples}场')
        if formula_hint:
            reasons.append(formula_hint)
        # 公式推荐名单加分：首选 +8，次选 +4（按候选式神实际角色的推荐顺序）。
        rec_bonus = 0
        rec_label = ''
        for role in sorted(role_tags(entry, defense=(defenses or {}).get(name))):
            recommend = ROLE_RECOMMEND.get(role, ())
            if name in recommend:
                rec_bonus += (RECOMMEND_BONUS if recommend.index(name) == 0
                              else RECOMMEND_BONUS_SECOND)
                rec_label = f'公式推荐：{role}首选 {recommend[0]}'
                break
        gain += rec_bonus
        if rec_label:
            reasons.append(rec_label)
        if new_roles:
            labels = {'fire': '鬼火', 'sustain': '治疗', 'shield': '护盾',
                      'control': '控制', 'damage': '输出', 'tempo': '拉条',
                      'support': '辅助'}
            reasons.append('补位：' + '、'.join(labels[role] for role in sorted(new_roles)))
        ranked.append((gain, tier_points(entry, current, speed=(speeds or {}).get(name),
                                        defense=(defenses or {}).get(name)),
                       name, tuple(reasons)))
    gain, _, name, reasons = max(ranked, key=lambda item: (item[0], item[1], item[2]))
    return Pick(name, gain, reasons, suggested_souls(catalog[name]))


def plan_lineup(available: Iterable[str], *, team_size: int = 5,
                locked: Iterable[str] = (), speeds: Mapping[str, int] | None = None,
                defenses: Mapping[str, int] | None = None,
                max_fire: int | None = None,
                catalog: Mapping[str, Entry] = CATALOG) -> tuple[str, ...]:
    """给定完整候选池时搜索高分阵容；locked 中的成员必须保留。"""
    required = tuple(locked)
    if not 1 <= team_size <= 8 or len(required) > team_size:
        raise ValueError('队伍人数配置无效')
    if len(required) != len(set(required)) or any(name not in catalog for name in required):
        raise ValueError('已选阵容含重复或未知式神')
    if len(SUMMON_SLOT_USERS.intersection(required)) > 1:
        raise ValueError('已选阵容占用多个我方召唤位')
    if max_fire is not None and (max_fire < 0 or
            sum(is_fire_supplier(name, catalog) for name in required) > max_fire):
        raise ValueError('已上阵打火式神数量超过限制')
    candidates = sorted({name for raw in available
                         if (name := normalize_name(raw, catalog)) and name not in required})
    if len(candidates) + len(required) < team_size:
        raise ValueError('可识别候选不足以组成队伍')
    # 保留多种中途组合，让火机、盾和输出的搭配可以在后几手形成。
    states = [required]
    for _ in range(team_size - len(required)):
        next_states = {}
        for state in states:
            for name in candidates:
                if name not in state:
                    lineup = (*state, name)
                    if len(SUMMON_SLOT_USERS.intersection(lineup)) > 1:
                        continue
                    if max_fire is not None and sum(
                            is_fire_supplier(item, catalog) for item in lineup) > max_fire:
                        continue
                    key = tuple(sorted(lineup))
                    next_states[key] = lineup
        if not next_states:
            raise ValueError('召唤位或打火数量限制后候选不足以组成阵容')
        states = sorted(next_states.values(),
                        key=lambda lineup: (team_score(lineup, catalog, speeds, defenses), lineup),
                        reverse=True)[:128]
    return states[0]


def plan_duel_lineup(own: Iterable[str], mate: Iterable[str], *, locked: Iterable[str] = (),
                     team_size: int = 3,
                     catalog: Mapping[str, Entry] = CATALOG) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """协同对弈联合规划：两个号共 6 个位置按公式分配。

    公式（用户口径）：总共 2 火机 + 2 输出 + 1 保护 + 1 控制/增伤，
    两边合计 6 人；火机可全在一侧（如 2+0），不必每边 1 个。
    评分只用确定性 team_score（不带速度/防御），保证两个实例各自调用结果一致。
    返回 (own_pick, mate_pick)，各 team_size 个。
    """
    from itertools import combinations
    own_pool = sorted({name for raw in own if (name := normalize_name(raw, catalog))})
    mate_pool = sorted({name for raw in mate if (name := normalize_name(raw, catalog))})
    required = tuple(locked)
    if len(own_pool) < team_size or len(mate_pool) < team_size:
        raise ValueError('协同对弈任一账号备选不足')
    formulas = (
        # 档1 严格：恰好 2 火机、恰好 2 输出、至少 1 保护(盾或治疗)、至少 1 控制或增伤
        lambda tags: (sum('fire' in t for t in tags) == 2 and
                      sum('damage' in t for t in tags) == 2 and
                      sum(('shield' in t or 'sustain' in t) for t in tags) >= 1 and
                      sum(('control' in t or 'support' in t) for t in tags) >= 1),
        # 档2：恰好 2 火机 + 恰好 2 输出（放宽保护/控制）
        lambda tags: sum('fire' in t for t in tags) == 2 and
                     sum('damage' in t for t in tags) == 2,
        # 档3：输出恰好 2，火机尽量（纯打火 + 辅助打火都算，最多 2）
        lambda tags: sum('fire' in t for t in tags) <= 2 and
                     sum('damage' in t for t in tags) == 2,
        # 档4 兜底：评分先惩罚 3 输出（输出只能 2），再优先火机数量
        lambda tags: True,
    )
    for formula in formulas:
        best, best_rank = None, (-(1 << 30), -1, -1e18)
        for own_pick in combinations(own_pool, team_size):
            if not set(required).issubset(own_pick):
                continue
            own_tags = [role_tags(catalog[n]) for n in own_pick]
            for mate_pick in combinations(mate_pool, team_size):
                tags = own_tags + [role_tags(catalog[n]) for n in mate_pick]
                if not formula(tags):
                    continue
                fire_count = sum('fire' in t for t in tags)
                damage_count = sum('damage' in t for t in tags)
                try:
                    score = team_score((*own_pick, *mate_pick), catalog)
                except Exception:
                    continue
                rank = (-abs(damage_count - 2) * 1000, fire_count, score)
                if rank > best_rank:
                    best_rank, best = rank, (own_pick, mate_pick)
        if best is not None:
            return best
    raise ValueError('协同对弈联合规划无解')
