"""Set the optional 今日不再提示 preference before an existing confirm input."""
from functools import lru_cache
from pathlib import Path
from time import monotonic
import cv2
import numpy as np
from module.exception import TaskRecoveryFailed

_ASSETS=Path(__file__).resolve().parent/'confirmation_assets'

@lru_cache(maxsize=4)
def _template(name):
    data=cv2.imdecode(np.frombuffer((_ASSETS/name).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
    return cv2.cvtColor(data,cv2.COLOR_BGR2RGB)

def confirmation_option(image, point):
    """Return (state, circle point, label origin) only beside this gold confirm."""
    if (not isinstance(image,np.ndarray) or image.ndim!=3 or image.shape[2]!=3
            or abs(image.shape[0]-720)>3 or abs(image.shape[1]-1280)>3):
        return None
    label=_template('dont_prompt_label.png')
    scores=cv2.matchTemplate(image[160:560,300:1000],label,cv2.TM_CCOEFF_NORMED)
    _,score,_,loc=cv2.minMaxLoc(scores)
    if score<.94:
        return None
    lx,ly=loc[0]+300,loc[1]+160
    # Another identical label/option makes ownership ambiguous.
    suppressed=scores.copy()
    suppressed[max(0,loc[1]-label.shape[0]):loc[1]+label.shape[0],
               max(0,loc[0]-label.shape[1]):loc[0]+label.shape[1]]=0
    if cv2.minMaxLoc(suppressed)[1] >= .94:
        raise TaskRecoveryFailed('Ambiguous confirmation reminder options')
    button=_template('gold_confirm.png')
    x1=max(0,lx-80);y1=ly+45
    area=image[y1:min(720,ly+150),x1:min(1280,lx+300)]
    _,score,_,loc=cv2.minMaxLoc(cv2.matchTemplate(area,button,cv2.TM_CCOEFF_NORMED))
    if score<.94:
        return None
    bx,by=x1+loc[0],y1+loc[1]
    if not (bx<=point[0]<bx+button.shape[1] and by<=point[1]<by+button.shape[0]):
        return None
    circle=_template('empty_circle.png')
    cx,cy=lx-44,ly-5
    region=image[cy:cy+40,cx:cx+42]
    _,empty,_,loc=cv2.minMaxLoc(cv2.matchTemplate(region,circle,cv2.TM_CCOEFF_NORMED))
    state='empty' if empty>=.94 else 'unknown'
    if state=='unknown':
        # Reuse the real blue/white selected-circle art from GeneralRoom.
        # Ignore the surrounding paper, but require the actual tick pixels.
        checked=_template('checked_circle.png')
        for size in (34,36,38):
            template=cv2.resize(checked,(size,size))
            yy,xx=np.indices((size,size));radius=np.hypot(xx-(size-1)/2,yy-(size-1)/2)
            mask=(radius<=size*.36).astype(np.uint8)*255
            scores=cv2.matchTemplate(region,template,cv2.TM_CCORR_NORMED,mask=mask)
            scores=np.nan_to_num(scores,nan=0,posinf=0,neginf=0)
            _,score,_,pos=cv2.minMaxLoc(scores)
            if score>=.97 and abs(cx+pos[0]+size/2-(lx-26))<=3 and abs(cy+pos[1]+size/2-(ly+13))<=3:
                state='checked';break
    return state,(lx-26,ly+13),(lx,ly)

def _context(image,origin):
    x,y=origin
    crop=image[max(0,y-130):min(720,y+135),max(0,x-170):min(1280,x+290)].copy()
    # Ignore only the circle's own tick; dialog text and buttons must remain.
    top=max(0,y-130);left=max(0,x-170)
    crop[y-8-top:y+36-top,x-48-left:x-4-left]=0
    return cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)

def ensure_confirmation_option(task,point,name):
    """Never confirms a modal; caller still owns the original business action."""
    device=task.device
    first=confirmation_option(getattr(device,'image',None),point)
    if first is None:
        return
    previous=getattr(device,'_verified_click',None)
    if previous and previous.get('name')=='CONFIRM_DONT_PROMPT:'+name and first[0]!='checked':
        raise TaskRecoveryFailed('Reminder checkbox already clicked without a verified tick')
    if previous and previous.get('name')==name and not previous.get('changed'):
        raise TaskRecoveryFailed('Cannot change reminder option after an unverified confirmation')
    generation=device._escape_generation
    invitation=getattr(device,'_accepted_invitation_generation',0)
    guard=getattr(device,'_task_flow_guard',None)
    context=_context(device.image,first[2])
    frame=getattr(device,'image_frame_id',None)
    stable=0;state=None;clicked=False
    deadline=monotonic()+device.performance.timeout(12)
    for _ in range(24):
        if monotonic()>=deadline:
            break
        task.screenshot()
        fresh=getattr(device,'image_frame_id',None)
        if (fresh is None or fresh==frame or device._escape_generation!=generation
                or getattr(device,'_accepted_invitation_generation',0)!=invitation
                or getattr(device,'_task_flow_guard',None) is not guard):
            raise TaskRecoveryFailed('Confirmation reminder observation interrupted')
        frame=fresh
        option=confirmation_option(device.image,point)
        if option is None or option[2]!=first[2] or option[1]!=first[1]:
            raise TaskRecoveryFailed('Confirmation reminder dialog changed')
        current=_context(device.image,option[2])
        if current.shape!=context.shape or np.mean(np.abs(current.astype(np.int16)-context.astype(np.int16))>25)>.015:
            raise TaskRecoveryFailed('Confirmation reminder context changed')
        stable=stable+1 if option[0]==state else 1;state=option[0]
        if stable<2:
            continue
        if state=='checked':
            return
        if state=='unknown':
            raise TaskRecoveryFailed('Confirmation reminder checkmark is not recognized')
        if not clicked:
            if device.click(*option[1],control_name='CONFIRM_DONT_PROMPT:'+name) is False:
                raise TaskRecoveryFailed('Confirmation reminder checkbox input was blocked')
            clicked=True;stable=0;state=None
            deadline=monotonic()+device.performance.timeout(12)
    raise TaskRecoveryFailed('Confirmation reminder checkmark not verified after one input')
