"""Active Hyakki selection with a per-game observations and fresh eligible retries."""

import base64
import time
import math
import re

import cv2
import numpy as np

from module.atom.ocr import RuleOcr
from module.logger import logger
from module.device.click_verification import before_click, after_click, signature
from tasks.Chess.strategy.soul_catalog import SOUL_BY_CHINESE_NAME, resolve_soul


# Lock icon cropped from the actual tomoe2 round-18 screenshot, at 1280x720.
_LOCK_PNG = 'iVBORw0KGgoAAAANSUhEUgAAABcAAAAeCAAAAADiTtq0AAACXElEQVQoFR3By29MYRgH4Pf3feecmemYQS8zEzSqNB2tEBF1F9EliZWNhT/Bn2FlaSEEC6tG3BIRiSAijdBoiVsZt7YGoUzKzPSc833v+7o8DwaGO91i6mYXNCRvTBhIu50awo5Vubil2fnYss3NtArwrbZkCAfz2qRCpZLX2GP8TVPExZQnHDLiOkvllbnEsZl4Wf+daCpZwgE1mf5y0XJLo/yn2ZkPDaQMYL8t9AwWFu/Pcliq9oQLd6e9sCOMZMt9/fbrnY+GCr3bemXskbNgwYbi6g097empbzZF5tDG8Py4CwM4DHev3+bf3vvREkt8eJNemRIn1mNvZd0Qf5qKnTHq9/a58Xdcb6jgaLlcClpfUkQkvLqYfvjuJ957xYlcJmM4SU0UqA8gnuXqY/U4FUEySOdbsiSfDeAsdOwhPE5HknSY5EW93V3pXWISS3xxwnicCTixhi6/0qUDO/OBWOLLj4lxFsJi7IXn6BraZw2F5K9OiuCcKBjm0nPXN7RVGSH42qQXnGXmSNNntdnt1ZJ6jgxfn0wUJ436UOK5ty9GB/MkPrLpjamYcDxvGezrtaej1Q5SiSi9+WTR4NiKoo0lnH79ele1m5mstm89TS2ODHSZGJnam+k91eUJW/jm7WcuxOjmivEm82WmNrKmY1GCtNV4UOMQA7vXLlMYlyS5iFh0bu7jfJMIpS39JQIRqYpK2qzXPydiBcU1K7oJICVR1fhno/FLYAm5pVkL0F/6X5I6MkbREViAQARVUkD/AaHIsCDQX0pEMCARATo9GSIC/adqQCJi/gBl0VUBF8RtpwAAAABJRU5ErkJggg=='


class ChessHyakkiDraftMixin:
    O_HYAKKI_ROUND = RuleOcr(
        roi=(469, 0, 75, 30), area=(469, 0, 75, 30),
        mode='Single', method='Default', keyword='', name='hyakki_turn_round')
    O_HYAKKI_MODE_TIMER = RuleOcr(
        roi=(735, 0, 65, 30), area=(735, 0, 65, 30),
        mode='Single', method='Default', keyword='', name='hyakki_turn_mode_timer')
    O_HYAKKI_TURN = RuleOcr(
        roi=(540, 235, 205, 95), area=(540, 235, 205, 95),
        mode='Single', method='Default', keyword='', name='hyakki_own_turn')
    O_HYAKKI_LOOT = RuleOcr(
        roi=(15, 105, 1055, 300), area=(15, 105, 1055, 300),
        mode='Single', method='Default', keyword='', name='hyakki_loot')

    @staticmethod
    def _hyakki_turn_image(image, require_unlocked=True):
        """Return own row only with a unique gold name and known lock UI."""
        if image is None or getattr(image, 'shape', ()) != (720, 1280, 3):
            return None
        rgb = image.astype(np.int16)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        gold = ((r > 170) & (g > 150) & (65 < b) & (b < 155)
                & (r - b > 65) & (r - g < 45))
        rows = tuple(54 + 85 * i for i in range(8))
        scores = [int(gold[y-12:y+12, 1108:1220].sum()) for y in rows]
        own = [i for i, count in enumerate(scores) if count >= 20]
        if len(own) != 1 or any(n >= 8 for i, n in enumerate(scores) if i != own[0]):
            return None
        template = cv2.imdecode(np.frombuffer(base64.b64decode(_LOCK_PNG),
                                              np.uint8), cv2.IMREAD_GRAYSCALE)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        locks = []
        for y in rows:
            crop = gray[max(0, y+16):min(720, y+55), 1163:1203]
            if crop.shape[0] < template.shape[0]:
                return None
            locks.append(float(cv2.matchTemplate(
                crop, template, cv2.TM_CCOEFF_NORMED).max()))
        # Absence of an arbitrary icon is insufficient: the same frame must
        # positively contain this event's lock artwork on at least two rows.
        if (require_unlocked and locks[own[0]] >= .65) or sum(v >= .75 for v in locks) < 2:
            return None
        return own[0]

    def _hyakki_context(self):
        return (getattr(getattr(self, 'chess_state', None), 'game_number', 0),
                self._read_round_number(),
                getattr(self.device, '_escape_generation', 0),
                getattr(self.device, '_accepted_invitation_generation', 0))

    def _hyakki_frame_key(self):
        return (getattr(self.device, 'image_frame_id', None),
                getattr(getattr(self, 'chess_state', None), 'game_number', 0),
                getattr(self.device, '_escape_generation', 0),
                getattr(self.device, '_accepted_invitation_generation', 0),
                id(self.device.image))

    @staticmethod
    def _hyakki_own_locked(image):
        """Gold is identity only. A positive lock on that row rejects input."""
        if getattr(image, 'shape', ()) != (720, 1280, 3):
            return False
        rgb = image.astype(np.int16)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        gold = ((r > 170) & (g > 150) & (65 < b) & (b < 155)
                & (r - b > 65) & (r - g < 45))
        rows = tuple(54 + 85 * i for i in range(8))
        scores = [int(gold[y-12:y+12, 1108:1220].sum()) for y in rows]
        own = [i for i, count in enumerate(scores) if count >= 20]
        if len(own) != 1 or any(n >= 8 for i, n in enumerate(scores) if i != own[0]):
            return False
        template = cv2.imdecode(np.frombuffer(base64.b64decode(_LOCK_PNG), np.uint8),
                                cv2.IMREAD_GRAYSCALE)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        y = rows[own[0]]
        crop = gray[y+16:min(720, y+55), 1163:1203]
        return float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED).max()) >= .65

    def _hyakki_note_turn_prompt(self):
        """Capture exact UI prompt before slow loot OCR; never renew a turn's TTL."""
        key = self._hyakki_frame_key()
        if (key[0] is None or key[1] <= 0 or getattr(self.device, '_handling_invitation', False)
                or getattr(self.device.image, 'shape', ()) != (720, 1280, 3)):
            self._chess_hyakki_turn_ticket = None
            return None
        header = self._hyakki_header()
        cached = getattr(self, '_chess_hyakki_prompt_cache', None)
        if cached and cached[0] == key and self._hyakki_same_header(cached[2], header):
            return cached[1]
        ticket = getattr(self, '_chess_hyakki_turn_ticket', None)
        # Across fresh frames, the previously read complete round label may
        # be reused only with stable actual round/mode artwork and same scope.
        stable_ticket = bool(ticket and self._hyakki_turn_permitted(ticket['context'])
                             and cached and cached[1]
                             and cached[1]['context'] == ticket['context']
                             and cached[0][1:4] == key[1:4]
                             and self._hyakki_same_header(cached[2], header))
        round_raw = ('' if stable_ticket else
                     self._normalize_ocr_text(self.O_HYAKKI_ROUND.ocr(self.device.image)))
        round_match = re.fullmatch(r'([1-9]\d?)回目', round_raw)
        mode_raw = self._normalize_ocr_text(self.O_HYAKKI_MODE_TIMER.ocr(self.device.image))
        parsed = re.fullmatch(r'鬼(\d{1,2})', mode_raw)
        if (not stable_ticket and round_match is None) or parsed is None:
            self._chess_hyakki_turn_ticket = None
            if (self._hyakki_frame_key() == key
                    and getattr(self.device.image, 'shape', ()) == (720, 1280, 3)
                    and self._hyakki_same_header(header, self._hyakki_header())):
                self._chess_hyakki_prompt_cache = (key, None, header)
            return None
        round_no = ticket['context'][1] if stable_ticket else int(round_match.group(1))
        context = (key[1], round_no, key[2], key[3])
        mode = '鬼'
        remaining = int(parsed.group(1))
        ticket = getattr(self, '_chess_hyakki_turn_ticket', None)
        if ticket and ticket['context'] != context:
            self._chess_hyakki_turn_ticket = None
        prompt = False
        locked = False
        observed = time.monotonic()
        if context[1] is not None and context[1] > 0 and mode == '鬼':
            locked = self._hyakki_own_locked(self.device.image)
            if not self._hyakki_turn_permitted(context):
                prompt = any(self._normalize_ocr_text(item.ocr_text) == '轮到你了'
                             and math.isfinite(float(item.score)) and float(item.score) >= .90
                             for item in self.O_HYAKKI_TURN.detect_and_ocr(self.device.image))
        if (self._hyakki_frame_key() != key
                or getattr(self.device.image, 'shape', ()) != (720, 1280, 3)
                or not self._hyakki_same_header(header, self._hyakki_header())
                or getattr(self.device, '_handling_invitation', False)
                or self._hyakki_frame_key() != key):
            self._chess_hyakki_turn_ticket = None
            return None
        if locked or mode != '鬼':
            self._chess_hyakki_turn_ticket = None
        elif self._hyakki_default_first_turn(self.device.image):
            # Last place acts first without the later players' turn prompt.
            # This authority is checked again on every fresh frame; the first
            # selection lock, changed ranking, or uncertain identity revokes it.
            self._chess_hyakki_turn_ticket = dict(context=context, expires=None,
                                                source='last_place_first')
        elif prompt:
            births = getattr(self, '_chess_hyakki_turn_births', None)
            if not births or births['game'] != context[0]:
                births = dict(game=context[0], rounds={})
                self._chess_hyakki_turn_births = births
            # Recovery/invitation changes must not mint another three seconds.
            first_seen = births['rounds'].setdefault(context[1], observed)
            if time.monotonic() < first_seen + 3:
                self._chess_hyakki_turn_ticket = dict(context=context, expires=first_seen + 3)
        result = dict(context=context, mode=mode, remaining=remaining, prompt=prompt, locked=locked)
        self._chess_hyakki_prompt_cache = (key, result, header)
        return result

    def _hyakki_turn_permitted(self, context):
        ticket = getattr(self, '_chess_hyakki_turn_ticket', None)
        if (not ticket or ticket['context'] != context
                or getattr(self.device, '_handling_invitation', False)
                or self._hyakki_frame_key()[1:4] != (context[0], context[2], context[3])):
            return False
        if ticket.get('source') == 'last_place_first':
            return self._hyakki_default_first_turn(self.device.image)
        return time.monotonic() < ticket['expires']

    @staticmethod
    def _hyakki_default_first_turn(image):
        """Unique last-place identity and no player has selected yet."""
        if getattr(image, 'shape', ()) != (720, 1280, 3):
            return False
        rgb = image.astype(np.int16)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        gold = ((r > 170) & (g > 150) & (65 < b) & (b < 155)
                & (r-b > 65) & (r-g < 45))
        rows = tuple(54+85*i for i in range(8))
        scores = [int(gold[y-12:y+12, 1108:1220].sum()) for y in rows]
        if scores[7] < 20 or any(count >= 8 for count in scores[:7]):
            return False
        template = cv2.imdecode(np.frombuffer(base64.b64decode(_LOCK_PNG), np.uint8),
                                cv2.IMREAD_GRAYSCALE)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        for y in rows:
            crop = gray[y+16:min(720, y+55), 1163:1203]
            score = float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED).max())
            if not math.isfinite(score) or score >= .65:
                return False
        return True

    def _hyakki_header(self):
        image = self.device.image
        # Actual prompt samples count down 20 -> 19 at x769 onward.
        # Keep the mode glyph, excluding normal timer animation/digits.
        # The decoration beyond the real round-text ROI animates during turns.
        return (image[0:30, 469:544].copy(), image[0:30, 706:760].copy())

    @staticmethod
    def _hyakki_same_header(first, second):
        return all(a.shape == b.shape and a.size and
                   int(np.max(np.abs(a.astype(np.int16) - b.astype(np.int16)))) <= 12
                   for a, b in zip(first, second))

    def _hyakki_loot_cards(self):
        """Exact scene loot names, independent of whose turn is active."""
        frame = getattr(self.device, 'image_frame_id', None)
        if frame is None or getattr(self.device.image, 'shape', ()) != (720, 1280, 3):
            return ()
        key = self._hyakki_frame_key()
        cached = getattr(self, '_chess_hyakki_loot_cache', None)
        if cached and cached[0] == key:
            return cached[1]
        x, y = self.O_HYAKKI_LOOT.roi[:2]
        cards = []
        for item in self.O_HYAKKI_LOOT.detect_and_ocr(self.device.image):
            name = self._normalize_ocr_text(item.ocr_text).strip('()（）[]【】')
            score = float(item.score)
            if name not in SOUL_BY_CHINESE_NAME or not math.isfinite(score) or score < .80:
                continue
            points = item.box
            cx = x + (min(int(p[0]) for p in points) + max(int(p[0]) for p in points)) // 2
            cy = y + (min(int(p[1]) for p in points) + max(int(p[1]) for p in points)) // 2
            if 45 <= cx <= 1020 and 135 <= cy <= 390:
                cards.append(dict(name=name, position=(cx, cy + 130)))
        current_key = self._hyakki_frame_key()
        if current_key != key:
            return ()
        value = tuple(cards)
        self._chess_hyakki_loot_cache = (key, value)
        return value

    def _hyakki_scene_positive(self):
        """Only an established local game may supplement hidden scene markers."""
        game = getattr(getattr(self, 'chess_state', None), 'game_number', 0)
        frame = getattr(self.device, 'image_frame_id', None)
        if (game <= 0 or frame is None
                or getattr(self.device.image, 'shape', ()) != (720, 1280, 3)):
            return False
        key = self._hyakki_frame_key()
        header = self._hyakki_header()
        cached = getattr(self, '_chess_hyakki_scene_cache', None)
        if cached and cached[0] == key and self._hyakki_same_header(cached[2], header):
            return cached[1]
        prompt = self._hyakki_note_turn_prompt()
        context = prompt['context'] if prompt else self._hyakki_context()
        value = False
        if prompt and prompt['prompt']:
            value = True
        elif (context[0] == game and context[1] is not None and context[1] > 0
                and self._read_chess_mode() == '鬼'):
            cards = self._hyakki_loot_cards()
            # A known name alone cannot grant identity across screenshots,
            # game/round changes, recovery, or inconsistent mode readings.
            value = ((bool(cards) or bool(prompt and prompt['prompt'])) and getattr(self.device, 'image_frame_id', None) == frame
                     and self._hyakki_context() == context
                     and self._read_chess_mode() == '鬼')
        if self._hyakki_frame_key() != key:
            return False
        if (getattr(self.device.image, 'shape', ()) != (720, 1280, 3)
                or not self._hyakki_same_header(header, self._hyakki_header())):
            return False
        self._chess_hyakki_scene_cache = (key, value, header)
        return value

    def _hyakki_observation(self, reference=None):
        key = self._hyakki_frame_key()
        if key[0] is None or getattr(self.device.image, 'shape', ()) != (720, 1280, 3):
            return None
        header = self._hyakki_header()
        note = self._hyakki_note_turn_prompt()
        if not note or note['mode'] != '鬼':
            return None
        context = note['context']
        if reference is not None and (context != reference['context']
                or not self._hyakki_same_header(reference['header'], header)):
            return None
        if not self._hyakki_turn_permitted(context) or note['locked']:
            return None
        cached = getattr(self, '_chess_hyakki_draft_cache', None)
        if cached and cached[0] == key and self._hyakki_same_header(cached[1]['header'], header):
            return cached[1]
        observed = time.monotonic()
        remaining = note['remaining']
        cards = list(self._hyakki_loot_cards()) if remaining is not None and remaining > 2 else []
        if (self._hyakki_frame_key() != key or not self._hyakki_turn_permitted(context)
                or not self._hyakki_same_header(header, self._hyakki_header())
                or self._hyakki_frame_key() != key):
            return None
        result = dict(frame=key[0], key=key, context=context, mode='鬼', remaining=remaining,
                      own=None, cards=cards, observed=observed, header=header)
        self._chess_hyakki_draft_cache = (key, result)
        return result

    def _hyakki_pick(self, cards):
        preferred = set()
        for config in self.get_lineup_strategy()['shikigami'].values():
            for value in config.get('preferred_souls', ()):
                entry = resolve_soul(value)
                if entry:
                    preferred.add(entry.chinese_name)
        utility = {'招财猫': 8, '火灵': 7, '伤魂鸟': 6, '破势': 5,
                   '钓瓶火': 4, '魍魉之匣': 4, '蝠翼': 3, '镜姬': 2}
        return max(cards, key=lambda c: (c['name'] in preferred,
                                         utility.get(c['name'], 1),
                                         -c['position'][0]))

    def _handle_hyakki_draft(self):
        """Poll once. True means input pending, never reward confirmation."""
        first = self._hyakki_observation()
        if not first or first['mode'] != '鬼' or first['context'][1] is None:
            return False
        ledger = getattr(self, '_chess_hyakki_draft_ledger', None)
        if not ledger or ledger['game'] != first['context'][0]:
            ledger = dict(game=first['context'][0], rounds={})
            self._chess_hyakki_draft_ledger = ledger
        round_no = first['context'][1]
        if ledger['rounds'].get(round_no, {}).get('status') == 'completed':
            return True
        if not first['cards'] or not self._hyakki_turn_permitted(first['context']):
            return False
        pick = self._hyakki_pick(first['cards'])
        if sum(c['name'] == pick['name'] for c in first['cards']) != 1:
            return False
        self.screenshot()
        second = self._hyakki_observation(first)
        if (not second or second['frame'] == first['frame']
                or second['context'] != first['context'] or second['mode'] != '鬼'
                or not self._hyakki_turn_permitted(second['context']) or second['remaining'] is None
                or second['remaining'] <= 4 or time.monotonic() - first['observed'] > 6):
            return False
        matches = [c for c in second['cards'] if c['name'] == pick['name']]
        if len(matches) != 1 or time.monotonic() - second['observed'] > 1.5:
            return False
        # Record pending before Control.click: successful dispatch returns None,
        # and post-click observation may raise; only fresh prompt-authorized targets permit retry.
        pending = dict(context=second['context'], name=pick['name'],
                       position=matches[0]['position'], status='pending')
        ledger['rounds'][round_no] = pending
        name = f'CHESS_HYAKKI_{second["context"][0]}_{round_no}'
        if self.device.handle_control_check(name) is False:
            pending['status'] = 'control-rejected'
            return True
        before_click(self.device, *pending['position'], name)
        self.screenshot()
        final = self._hyakki_observation(second)
        if (not final or final['frame'] == second['frame']
                or final['context'] != second['context'] or final['mode'] != '鬼'
                or not self._hyakki_turn_permitted(final['context']) or final['remaining'] is None
                or final['remaining'] <= 4 or time.monotonic() - final['observed'] > 1.5):
            pending['status'] = 'target-changed'
            return True
        targets = [c for c in final['cards'] if c['name'] == pick['name']]
        if len(targets) != 1:
            pending['status'] = 'target-changed'
            return True
        pending['position'] = targets[0]['position']
        response_before = signature(self.device.image, *pending['position'])
        if (self._hyakki_frame_key() != final['key']
                or getattr(self.device.image, 'shape', ()) != (720, 1280, 3)
                or not self._hyakki_same_header(final['header'], self._hyakki_header())
                or not self._hyakki_turn_permitted(final['context'])
                or self._hyakki_frame_key() != final['key']):
            pending['status'] = 'target-changed'
            return True
        # Generic guards ran above; this event supplies the final semantic
        # observation after any recovery, without another recovery before input.
        result = self.device.click(*pending['position'], control_check=False, control_name=name)
        if result is False:
            pending['status'] = 'control-rejected'
            logger.warning('Hyakki throw rejected; awaiting the next fresh eligible target')
        else:
            after_click(self.device, *pending['position'], name, response_before)
            logger.info(f'Hyakki bean input dispatched toward {pick["name"]}; reward unconfirmed')
        return True

