"""从 assets.py 源码解析出字面量坐标，绕开运行时的可变 roi_front。

`RuleImage._update_roi_front` 会在每次匹配成功时把 roi_front 改写成实测位置，
所以拿 `GameUiAssets.I_MAIN_GOTO_ONMYODO.roi_front` 当「声明值」是自证循环：
不管声明写成什么，匹配后两者都会相等。必须回到源码取字面量。
"""
import ast
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / 'tasks' / 'GameUi' / 'assets.py'


def declared_roi_front(name):
    """返回 assets.py 里 `NAME = RuleImage(roi_front=(...), ...)` 的字面量元组。

    Raises:
        ValueError: 找不到该 name 或 roi_front 不是字面量。
    """
    tree = ast.parse(ASSETS.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and getattr(call.func, 'id', None) == 'RuleImage'):
            continue
        for kw in call.keywords:
            if kw.arg != 'roi_front':
                continue
            values = []
            for elt in kw.value.elts if isinstance(kw.value, ast.Tuple) else []:
                values.append(elt.value if isinstance(elt, ast.Constant) else None)
            if len(values) != 4 or any(v is None for v in values):
                raise ValueError(f'{name}.roi_front 不是纯字面量，无法静态比对')
            return tuple(int(v) for v in values)
    raise ValueError(f'assets.py 中找不到 {name} 的 RuleImage 定义')


def declared_roi_back(name):
    """同declared_roi_front，但取 roi_back。"""
    tree = ast.parse(ASSETS.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and getattr(call.func, 'id', None) == 'RuleImage'):
            continue
        for kw in call.keywords:
            if kw.arg != 'roi_back':
                continue
            if isinstance(kw.value, ast.Tuple):
                return tuple(int(e.value) for e in kw.value.elts)
            # roi_back 允许写成 (x, y, w, h) 之外的表达式，此时放弃静态比对。
            return None
    raise ValueError(f'assets.py 中找不到 {name} 的 roi_back')
