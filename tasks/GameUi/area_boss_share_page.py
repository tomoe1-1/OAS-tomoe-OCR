"""Recognize the Area Boss formation-share page and its blue back button."""
from pathlib import Path
from functools import lru_cache
import cv2
import numpy as np
from module.atom.image import RuleImage

_ASSETS=Path(__file__).resolve().parent/'page'

I_AREA_SHARE_BACK=RuleImage(roi_front=(23,24,71,72),roi_back=(8,8,106,108),
    threshold=.92,method='Template matching',file='./tasks/GameUi/page/area_share_back_blue.png')

@lru_cache(maxsize=4)
def _template(name):
    image=cv2.imdecode(np.frombuffer((_ASSETS/name).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
    return cv2.cvtColor(image,cv2.COLOR_BGR2RGB)

def area_share_visible(image):
    if not isinstance(image,np.ndarray) or image.shape[:2]!=(720,1280):
        return False
    def matches(name,roi):
        x,y,w,h=roi
        return cv2.minMaxLoc(cv2.matchTemplate(image[y:y+h,x:x+w],_template(name),
                                              cv2.TM_CCOEFF_NORMED))[1]>=.92
    return (matches('area_share_back_blue.png',(8,8,106,108))
            and (matches('area_share_choices.png',(770,70,245,70))
                 or matches('area_share_formation.png',(300,455,240,85)))
            and matches('area_share_footer.png',(1170,610,110,110)))

def is_area_share_page(task):
    return area_share_visible(task.device.image)
