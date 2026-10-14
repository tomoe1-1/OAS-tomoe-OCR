from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import TaskRecoveryFailed
from module.logger import logger
from tasks.Component.Buy.buy import Buy
from tasks.GameUi.page import random_click


class SaleAmulet(Buy):
    I_SALE_TITLE = RuleImage(roi_front=(182,81,145,40), roi_back=(0,0,1280,720),
        threshold=.85, method='Template matching',
        file='./tasks/WeeklyPurchase/mall/consignment/sale_title.png')
    I_SALE_BLUE = RuleImage(roi_front=(208,132,89,92), roi_back=(0,0,1280,720),
        threshold=.85, method='Template matching',
        file='./tasks/WeeklyPurchase/mall/consignment/sale_blue.png')
    I_SALE_MAX = RuleImage(roi_front=(367,366,64,66), roi_back=(0,0,1280,720),
        threshold=.85, method='Template matching',
        file='./tasks/WeeklyPurchase/mall/consignment/sale_max.png')
    I_SALE_JADE = RuleImage(roi_front=(224,476,34,42), roi_back=(0,0,1280,720),
        threshold=.85, method='Template matching',
        file='./tasks/WeeklyPurchase/mall/consignment/sale_jade.png')

    def _sale_dialog_visible(self):
        rules=(self.I_SALE_TITLE,self.I_SALE_BLUE,self.I_SALE_MAX,self.I_SALE_JADE)
        if not all(self.appear(rule) for rule in rules):
            return False
        # Match all controls in the same blue-ticket popup, not the shop behind it.
        tx,ty,_,_=self.I_SALE_TITLE.roi_front
        bx,by,_,_=self.I_SALE_BLUE.roi_front
        mx,my,_,_=self.I_SALE_MAX.roi_front
        jx,jy,_,_=self.I_SALE_JADE.roi_front
        return (abs((bx-tx)-26)<18 and abs((by-ty)-51)<18
                and abs((mx-bx)-159)<18 and abs((my-by)-234)<18
                and abs((jx-bx)-16)<18 and abs((jy-by)-344)<18)

    def _sale_quantity(self):
        x,y,_,_=self.I_SALE_MAX.roi_front
        roi=(int(x-183),int(y+14),64,46)
        return RuleOcr(roi=roi,area=roi,mode='Digit',method='Default',keyword='',
                       name='sale_ticket_quantity').ocr(self.device.image)

    def buy_sale_amulets(self,start_click):
        self.screenshot()
        if not self._sale_dialog_visible():
            self.ui_click(start_click,self.I_SALE_TITLE)
        self._ui_verify(self._sale_dialog_visible,[])
        before=self._sale_quantity()
        if not isinstance(before,int) or before<=0:
            raise TaskRecoveryFailed('Sale ticket quantity cannot be read; do not purchase')
        self.click(self.I_SALE_MAX)
        previous=[None]
        selected=[None]
        def maximum_verified():
            if not self._sale_dialog_visible():
                return False
            current=self._sale_quantity()
            valid=isinstance(current,int) and current>=before and current>0
            stable=valid and previous[0]==current
            previous[0]=current
            if stable:
                selected[0]=current
            return stable
        self._ui_verify(maximum_verified,[self.I_SALE_MAX])
        logger.info(f'Sale ticket maximum quantity verified: {selected[0]}')
        # One physical purchase input on the jade icon; never retry an ambiguous debit.
        self.click(self.I_SALE_JADE)
        self._ui_verify(lambda: not self._sale_dialog_visible() and
                        (self.appear(self.I_UI_REWARD) or self.appear(self.I_BUY_SUCCESS)),
                        [self.I_SALE_JADE])
        if self.appear(self.I_UI_REWARD):
            self.ui_get_reward(self.I_UI_REWARD)
        else:
            self.ui_click_until_smt_disappear(random_click(),self.I_BUY_SUCCESS)
        logger.info(f'Sale ticket purchase reward verified: {selected[0]}')
        return True
