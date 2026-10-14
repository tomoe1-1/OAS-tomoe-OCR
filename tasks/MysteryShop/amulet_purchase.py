"""Price checked, bounded mystery amulet purchases with persistent receipts."""
import json
from datetime import datetime
from pathlib import Path
from time import monotonic

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import TaskRecoveryFailed
from module.logger import logger


class MysteryAmuletPurchase:
    @staticmethod
    def _amulet_ocr(image, roi, mode='Digit'):
        return RuleOcr(roi=roi, area=roi, mode=mode, method='Default',
                       keyword='', name='mystery_amulet_checked').ocr(image)

    def _amulet_dialog(self):
        title = self._amulet_ocr(self.device.image, (540,145,210,43), 'Single')
        return ''.join(str(title).split()) == '神秘的符咒'

    def buy_discount_amulets(self):
        # The daily receipt bounds retries and visits to other friends as well.
        profile = self.config.config_name
        path = Path('log/mystery_amulet') / f'{profile}-{datetime.now():%Y-%m-%d}.json'
        receipt = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'bought':0}
        if receipt.get('pending'):
            raise TaskRecoveryFailed('Amulet payment pending verification; do not pay twice')
        limit = 3 - receipt['bought']
        if limit <= 0:
            logger.info('Mystery amulet daily target of 3 already verified')
            return
        self.screenshot()
        if not self._amulet_dialog():
            if not self.appear(self.I_MS_BLUE):
                return
            stock = self._special_check_remain(self.I_MS_BLUE)
            if stock <= 0:
                return
            x,y,w,h = self.I_MS_BLUE.roi_front
            roi = (x+w//2+20,y+137,60,37)
            price = self._amulet_ocr(self.device.image,roi)
            if type(price) is not int or not 0 < price <= 80:
                logger.info(f'Skip mystery amulet with price {price}; maximum 80 jade')
                return
            click = RuleClick(roi_front=(x+w//2-5,y+140,75,30),
                              roi_back=(x+w//2-5,y+140,75,30),name='MYSTERY_AMULET_PRICE')
            self.click(click)
            self._ui_verify(self._amulet_dialog,[click])
        else:
            # Recover an already opened, unpaid dialog using its visible stock.
            if not self.appear(self.I_MS_BLUE):
                raise TaskRecoveryFailed('Amulet stock cannot be located behind dialog')
            stock = self._special_check_remain(self.I_MS_BLUE)
        quantity = min(limit,stock)
        if quantity <= 0:
            raise TaskRecoveryFailed('Amulet stock unreadable in purchase dialog')
        self._select_purchase_quantity(1)
        currency = RuleImage(roi_front=(607,539,39,48),roi_back=(595,530,55,65),
                             threshold=.85,method='Template matching',
                             file='./tasks/MysteryShop/ms/mystery_jade_currency.png')
        def quote():
            unit = self._amulet_ocr(self.device.image,(646,541,64,44))
            balance = self._amulet_ocr(self.device.image,(958,7,90,40))
            if (not self._amulet_dialog() or not self.appear(currency)
                    or type(unit) is not int or not 0 < unit <= 80
                    or type(balance) is not int or balance < unit*quantity):
                raise TaskRecoveryFailed('Amulet jade price or balance unverified; do not pay')
            return unit,balance
        unit,balance = quote()
        self._ui_verify(lambda:quote()==(unit,balance),[])
        if quantity == stock:
            # The double arrow selects available stock; never use it when
            # stock exceeds the remaining authorized purchase target.
            if not self.appear_then_click(self.I_BUY_PLUS, interval=0):
                raise TaskRecoveryFailed('Amulet maximum quantity control unavailable')
            self._ui_verify(lambda:self.O_BUY_NUMBER.ocr(self.device.image)==quantity,
                            [self.I_BUY_PLUS])
        else:
            self._select_purchase_quantity(quantity)
        total = unit*quantity
        def payment_ready():
            return (self._amulet_dialog() and self.appear(currency)
                    and self.O_BUY_NUMBER.ocr(self.device.image)==quantity
                    and self._amulet_ocr(self.device.image,(646,541,64,44))==total)
        self._ui_verify(payment_ready,[])
        path.parent.mkdir(parents=True,exist_ok=True)
        receipt.update(pending={'quantity':quantity,'unit_price':unit,'total':total,
                                'balance_before':balance,'stock_before':stock})
        path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        logger.info(f'BUY_CHECKED_AMULETS quantity={quantity} unit={unit} total={total}')
        self.click(self.C_BUY_MORE)
        end=monotonic()+30
        while monotonic()<end:
            self.screenshot()
            if self.ui_reward_appear_click():
                continue
            if not self._amulet_dialog() and self.appear(self.I_MS_BLUE):
                def verified():
                    return (not self._amulet_dialog() and self.appear(self.I_MS_BLUE)
                            and self._special_check_remain(self.I_MS_BLUE)==stock-quantity
                            and self._amulet_ocr(self.device.image,(958,7,90,40))==balance-total)
                self._ui_verify(verified,[self.C_BUY_MORE])
                receipt['bought']+=quantity
                receipt.setdefault('transactions',[]).append(receipt.pop('pending'))
                receipt['verified_at']=datetime.now().isoformat()
                path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
                self.device.click_record_clear()
                logger.info(f'VERIFIED_AMULET_PURCHASE {receipt}')
                return
        raise TaskRecoveryFailed('Amulet payment result unverified; pending receipt retained')
