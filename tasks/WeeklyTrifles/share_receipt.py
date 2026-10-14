"""Capture transient collection-share receipts, including click verification frames."""
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import json
import re
import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
_ASSETS = Path(__file__).resolve().parent / 'collect'

@lru_cache(maxsize=3)
def _template(name):
    bgr=cv2.imdecode(np.frombuffer((_ASSETS/name).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)

def collection_share_success(image):
    if not isinstance(image,np.ndarray) or image.ndim!=3 or image.shape[2]!=3:
        return False
    h,w=image.shape[:2]
    if w!=1280 or abs(h-720)>3:
        return False
    def matches(name,roi):
        x,y,rw,rh=roi;crop=image[y:y+rh,x:x+rw];template=_template(name)
        return cv2.minMaxLoc(cv2.matchTemplate(crop,template,cv2.TM_CCOEFF_NORMED))[1]>=.92
    return (matches('collect_share_success.png',(450,180,380,150))
            and matches('collect_share_qr_anchor.png',(1020,480,140,130))
            and matches('collect_share_footer.png',(200,620,240,100)))

def _receipt_path(profile):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',profile):
        raise ValueError('Invalid profile for share receipt')
    return _ROOT/'log/task_progress'/f'{profile}_weekly_collection_share.json'

def _week(now):
    year,week,_=now.isocalendar()
    return f'{year}-{week:02d}'

def share_done_this_week(profile, now=None):
    path=_receipt_path(profile)
    if not path.exists():
        return False
    data=json.loads(path.read_text(encoding='utf-8'))
    return (data.get('week')==_week(now or datetime.now())
            and data.get('evidence') in ('share_success','reward_claimed'))

def save_share_receipt(profile, evidence):
    if evidence not in ('share_success','reward_claimed'):
        raise ValueError('Unverified share receipt')
    path=_receipt_path(profile);path.parent.mkdir(parents=True,exist_ok=True)
    now=datetime.now()
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'week':_week(now),'at':now.isoformat(),
                                    'evidence':evidence}),encoding='utf-8')
    temporary.replace(path)

@contextmanager
def capture_collection_share(device):
    # A toast may be gone by the time click() returns: inspect the screenshots
    # taken inside click verification too. This wrapper exists only in this task.
    original=device.screenshot
    owned='screenshot' in device.__dict__
    previous=device.__dict__.get('screenshot')
    state=SimpleNamespace(seen=False,armed=False)
    generation=device._escape_generation
    def capture(*args,**kwargs):
        result=original(*args,**kwargs)
        if state.armed and device._escape_generation==generation:
            state.seen=state.seen or collection_share_success(device.image)
        return result
    device.screenshot=capture
    try:
        yield state
    finally:
        if owned:
            device.screenshot=previous
        else:
            del device.screenshot
