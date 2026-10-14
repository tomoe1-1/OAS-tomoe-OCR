"""OCR fallback for preset text controls; original image matching takes priority."""
from module.atom.ocr import RuleOcr

def text_rule(name,keyword,roi):
    return RuleOcr(roi=roi,area=roi,mode='Full',method='Default',keyword=keyword,name=name)

SOUL_ENTRY=text_rule('soul_preset_entry_text','预设',(310,57,169,72))
SOUL_TEAM_TITLE=text_rule('soul_team_preset_title_text','队伍预设',(710,55,200,85))
SOUL_SOUL_TITLE=text_rule('soul_preset_title_text','御魂预设',(710,55,200,85))
SOUL_CONFIRM=text_rule('soul_preset_confirm_text','确定',(668,401,180,61))
TEAM_ENTRY=text_rule('battle_preset_entry_text','预设',(9,620,100,90))
TEAM_CONFIRM=text_rule('battle_preset_confirm_text','出战',(305,625,236,83))
TEAM_INCOMPLETE=text_rule('battle_preset_incomplete_text','才能出战',(200,625,480,80))

def soul_slot_center(results,roi,slot):
    """Locate the numbered row by OCR name boxes, not its arrow template."""
    if not 1 <= slot <= 4:
        raise ValueError('Soul preset slot must be in [1-4]')
    rows=[]
    for result in results:
        if not result.ocr_text.strip() or result.score < .8:
            continue
        box=result.box
        if len(box)!=4:
            continue
        xs=[float(p[0]) for p in box];ys=[float(p[1]) for p in box]
        if not (0<=min(xs)<max(xs)<=roi[2] and 0<=min(ys)<max(ys)<=roi[3]):
            continue
        y=roi[1]+(min(ys)+max(ys))/2
        if not any(abs(y-existing)<28 for existing in rows):
            rows.append(y)
    # Missing OCR on row 1 must not shift row 2 into configured slot 1.
    expected_y=roi[1]+20+(slot-1)*150
    matches=[y for y in rows if abs(y-expected_y)<=65]
    return int(matches[0]) if len(matches)==1 else None
