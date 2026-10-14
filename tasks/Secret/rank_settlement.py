from module.atom.image import RuleImage
from module.atom.click import RuleClick

RANK_WIN=RuleImage(roi_front=(712,25,295,133),roi_back=(0,0,1280,720),threshold=.85,
    method='Template matching',file='./tasks/Secret/se/secret_rank_win.png')
RANK_HEADING=RuleImage(roi_front=(99,193,84,46),roi_back=(0,0,1280,720),threshold=.85,
    method='Template matching',file='./tasks/Secret/se/secret_rank_heading.png')
RANK_TEAM=RuleImage(roi_front=(633,188,142,45),roi_back=(0,0,1280,720),threshold=.85,
    method='Template matching',file='./tasks/Secret/se/secret_rank_team.png')

def rank_settlement_click(task):
    if not all(task.appear(rule) for rule in (RANK_WIN,RANK_HEADING,RANK_TEAM)):
        return None
    wx,wy,_,_=RANK_WIN.roi_front
    hx,hy,_,_=RANK_HEADING.roi_front
    tx,ty,_,_=RANK_TEAM.roi_front
    if not (abs((hx-wx)+613)<20 and abs((hy-wy)-168)<20
            and abs((tx-hx)-534)<20 and abs(ty-hy+5)<20):
        return None
    height,width=task.device.image.shape[:2]
    # The verified secret rank layout has no continue caption. Bottom center
    # lies between the two panels, away from its share button and reward icons.
    point=(width//2,height-22)
    return RuleClick(roi_front=(*point,1,1),roi_back=(*point,1,1),
                     name='SECRET_RANK_CONTINUE')
