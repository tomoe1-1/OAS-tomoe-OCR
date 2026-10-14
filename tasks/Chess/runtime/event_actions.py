"""Interrupted Chess discovery events: bounded, single-input actions."""

import random
import time

import numpy as np

from module.device.click_verification import before_click, after_click, signature

from module.exception import TaskRecoveryFailed
from module.logger import logger


class ChessEventActionsMixin:
    DISCOVER_EVENT_TIMEOUT = 30.0

    def _discover_context(self):
        return (
            getattr(getattr(self, 'chess_state', None), 'game_number', 0),
            self._read_round_number(),
            getattr(self.device, '_escape_generation', 0),
        )

    def _discover_choices(self):
        return tuple(rule for rule in (
            self.I_SELECT_SOUL_1, self.I_SELECT_SOUL_2,
            self.I_SELECT_SOUL_3,
        ) if self.appear(rule))

    def _discover_choice_visual(self):
        """Bind option artwork/text above the three generic Select buttons."""
        image = getattr(self.device, 'image', None)
        if getattr(image, 'shape', ()) != (720, 1280, 3):
            return None
        choices = self._discover_choices()
        if len(choices) != 3:
            return None
        patches = []
        for rule in choices:
            x, y, _, _ = rule.roi_front
            left, top, width, height = int(x)-50, int(y)-178, 170, 165
            if left < 0 or top < 0 or left+width > 1280 or top+height > 720:
                return None
            patch = image[top:top+height, left:left+width].copy()
            if float(np.std(patch)) < 8:
                return None
            patches.append((rule.name, (left, top, width, height), patch))
        return tuple(patches)

    @staticmethod
    def _discover_same_choice_visual(expected, current):
        if not expected or not current or len(expected) != len(current):
            return False
        for (name, roi, patch), (other, other_roi, image) in zip(expected, current):
            if name != other or roi != other_roi or patch.shape != image.shape:
                return False
            delta = np.abs(patch.astype(np.int16)-image.astype(np.int16))
            # Small render noise is allowed; different names/icons/artwork
            # invalidate the original selected content even with identical buttons.
            if int(np.max(delta)) > 4:
                return False
        return True

    def _discover_cards(self):
        return self._discover_hand_snapshot()[0]

    def _discover_hand_snapshot(self):
        """One hand OCR per frame, shared by both complete discovery names."""
        frame = getattr(self.device, 'image_frame_id', None)
        cached = getattr(self, '_chess_discover_hand_cache', None)
        if frame is not None and cached and cached[0] == frame:
            return cached[1]
        results = self.O_BADGE_AREA.detect_and_ocr(self.device.image)
        roi_x, roi_y = self.O_BADGE_AREA.roi[:2]
        cards, texts = [], []
        for result in results:
            text = self._normalize_ocr_text(result.ocr_text).strip('()（）[]【】')
            if text:
                texts.append(text)
            if text not in ('发现御魂', '发现纹章'):
                continue
            points = result.box
            cards.append(dict(kind=text, text=text, score=float(result.score),
                position=(roi_x + (min(int(p[0]) for p in points)
                                  + max(int(p[0]) for p in points)) // 2,
                          roi_y + (min(int(p[1]) for p in points)
                                  + max(int(p[1]) for p in points)) // 2)))
        value = (sorted(cards, key=lambda card: card['position'][0]), tuple(texts))
        self._chess_discover_hand_cache = (frame, value)
        return value

    def _discover_card_key(self, cards):
        return tuple((card['kind'], card['text'],
                      card['position'][0] // 8, card['position'][1] // 8)
                     for card in cards)

    @staticmethod
    def _discover_inventory(cards):
        return tuple(sorted((card['kind'], card['text']) for card in cards))

    def _discover_context_valid(self, session):
        if time.monotonic() >= session['deadline']:
            raise TaskRecoveryFailed('Chess discovery event budget exhausted')
        valid = (session['context'][1] is not None
                and self._discover_context() == session['context']
                and self._read_chess_mode() in session.get('modes', ('备',))
                and self._is_in_chess_game()
                and not self.appear(self.I_SELECT_GRIGRI))
        if time.monotonic() >= session['deadline']:
            raise TaskRecoveryFailed('Chess discovery observation exceeded budget')
        return valid

    def _discover_new_session(self, context, card=None, cards=()):
        if context[1] is None:
            raise TaskRecoveryFailed('Chess discovery needs a known round')
        session = dict(context=context, deadline=time.monotonic()
                       + self.DISCOVER_EVENT_TIMEOUT, phase='card',
                       card=card, before_count=sum(c['kind'] == card['kind']
                       for c in cards) if card else None,
                       completed=False, selected=None)
        session['inventory'] = self._discover_inventory(cards)
        self._chess_discover_session = session
        return session

    def _discover_input(self, session, next_phase, *, rule=None, point=None):
        if not self._discover_context_valid(session):
            raise TaskRecoveryFailed('Chess discovery context changed before input')
        if self._read_chess_mode() != '备':
            raise TaskRecoveryFailed('Chess discovery input needs preparation')
        if next_phase == 'card_sent':
            valid = (self._observe_shop_visibility() is False
                     and not self.appear(self.I_USE_SOUL)
                     and not self._discover_choices()
                     and self._discover_card_key(self._discover_cards())
                     == session['inventory_key'])
        elif next_phase == 'use_sent':
            valid = (self.appear(self.I_USE_SOUL) and not self._discover_choices()
                     and self._discover_inventory(self._discover_cards())
                     == session['inventory'])
        else:
            valid = (len(self._discover_choices()) == 3 and self.appear(rule)
                     and self._discover_same_choice_visual(
                         session.get('choice_visual'), self._discover_choice_visual()))
        if not valid or time.monotonic() >= session['deadline']:
            raise TaskRecoveryFailed('Chess discovery target changed before input')
        # Remember the pending phase before dispatch; fresh same-phase frames may authorize retry.
        session['phase'] = next_phase
        session['last_input_frame'] = getattr(self.device, 'image_frame_id', None)
        if rule is not None:
            point = rule.coord()
            name = rule.name
        else:
            name = 'CHESS_DISCOVER_CARD'
        if next_phase == 'choice_sent':
            if self.device.handle_control_check(name) is False:
                raise TaskRecoveryFailed('Chess discovery choice control rejected')
            before_click(self.device, *point, name)
            previous = getattr(self.device, 'image_frame_id', None)
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if (frame is None or frame == previous or not self._discover_context_valid(session)
                    or not self.appear(rule) or len(self._discover_choices()) != 3
                    or not self._discover_same_choice_visual(
                        session.get('choice_visual'), self._discover_choice_visual())):
                raise TaskRecoveryFailed('Chess discovery choice content changed before input')
            point = rule.coord()  # Relocate from the final actual matched frame.
            response_before = signature(self.device.image, *point)
            result = self.device.click(x=point[0], y=point[1],
                                       control_check=False, control_name=name)
            if result is not False:
                after_click(self.device, *point, name, response_before)
        else:
            result = self.device.click(x=point[0], y=point[1], control_name=name)
        if result is False:
            raise TaskRecoveryFailed('Chess discovery input was rejected')

    def _discover_stable(self, session, read, accept, retry=None):
        """Two different newly captured frames; no input during instability."""
        previous = None
        last_frame = getattr(self.device, 'image_frame_id', None)
        while time.monotonic() < session['deadline']:
            self.screenshot()
            if not self._discover_context_valid(session):
                raise TaskRecoveryFailed('Chess discovery context changed')
            frame = getattr(self.device, 'image_frame_id', None)
            if frame is None or frame == last_frame:
                time.sleep(self.SCREENSHOT_INTERVAL)
                continue
            last_frame = frame
            value = read()
            if accept(value):
                if previous is not None and value == previous:
                    if not self._discover_context_valid(session):
                        raise TaskRecoveryFailed('Chess discovery context changed during confirmation')
                    return value
                previous = value
            else:
                if retry is not None and previous is not None and value == previous:
                    if not self._discover_context_valid(session):
                        raise TaskRecoveryFailed('Chess discovery retry context changed')
                    retry(value)
                    previous = None
                else:
                    previous = value if retry is not None else None
            time.sleep(self.SCREENSHOT_INTERVAL)
        raise TaskRecoveryFailed('Chess discovery confirmation timed out')

    def _discover_resume(self, session):
        if not self._discover_context_valid(session):
            raise TaskRecoveryFailed('Chess discovery cannot resume in another context')
        if session['completed']:
            if self._discover_choices():
                raise TaskRecoveryFailed('Chess completed discovery panel reopened')
            return True

        if session['phase'] == 'card_sent':
            def use_state():
                return (bool(self.appear(self.I_USE_SOUL)),
                        tuple(rule.name for rule in self._discover_choices()),
                        self._discover_inventory(self._discover_cards()))
            # Do not press Use for an unrelated card after inventory drift.
            def retry_card(value):
                if value[0] or value[1] or value[2] != session['inventory']:
                    return
                cards = self._discover_cards()
                if self._discover_card_key(cards) != session['inventory_key']:
                    return
                selected = next((c for c in cards if c['kind'] == session['card']['kind']
                                 and c['text'] == session['card']['text']), None)
                if selected is not None:
                    self._discover_input(session, 'card_sent', point=selected['position'])
            self._discover_stable(session, use_state,
                lambda value: value[0] and not value[1]
                and value[2] == session['inventory'], retry=retry_card)
            self._discover_input(session, 'use_sent', rule=self.I_USE_SOUL)

        if session['phase'] in ('use_sent', 'orphan'):
            def choices_state():
                return (tuple(rule.name for rule in self._discover_choices()),
                        bool(self.appear(self.I_USE_SOUL)),
                        self._discover_inventory(self._discover_cards()))
            def retry_use(value):
                if value[0] or session['phase'] != 'use_sent':
                    return
                if value[1] and value[2] == session['inventory']:
                    self._discover_input(session, 'use_sent', rule=self.I_USE_SOUL)
            names = self._discover_stable(session, choices_state,
                lambda value: len(value[0]) == 3, retry=retry_use)[0]
            options = self._discover_choices()
            if tuple(rule.name for rule in options) != names:
                raise TaskRecoveryFailed('Chess discovery options changed before selection')
            selected = random.choice(options)
            session['selected'] = selected.name
            session['choice_visual'] = self._discover_choice_visual()
            if session['choice_visual'] is None:
                raise TaskRecoveryFailed('Chess discovery option content unavailable')
            self._discover_input(session, 'choice_sent', rule=selected)

        if session['phase'] == 'choice_sent':
            def receipt():
                closed = not self._discover_choices() and not self.appear(self.I_USE_SOUL)
                if not closed or self._observe_shop_visibility() is not False:
                    return False
                if session['card'] is None:
                    return True  # Orphan panel closure; no claimed card consumption.
                cards, texts = self._discover_hand_snapshot()
                if any(text.startswith('发现') and text not in ('发现御魂', '发现纹章')
                       for text in texts):
                    return False
                # Positive hand OCR somewhere is required: no text != empty hand.
                if not texts:
                    return False
                expected = list(session['inventory'])
                expected.remove((session['card']['kind'], session['card']['text']))
                return self._discover_inventory(cards) == tuple(expected)
            def retry_choice(value):
                if value is not False:
                    return
                names = tuple(r.name for r in self._discover_choices())
                if len(names) != 3 or session['selected'] not in names:
                    return
                if not self._discover_same_choice_visual(
                        session.get('choice_visual'), self._discover_choice_visual()):
                    return
                def content_state():
                    current = tuple(r.name for r in self._discover_choices())
                    if not current:
                        return 'closed'
                    if current == names and self._discover_same_choice_visual(
                            session.get('choice_visual'), self._discover_choice_visual()):
                        return 'same'
                    return 'changed'
                stable = self._discover_stable(session, content_state,
                    lambda current: current in ('same', 'closed'))
                if stable == 'closed':
                    return  # Original input may have closed the panel meanwhile.
                choices = self._discover_choices()
                selected = next((r for r in choices if r.name == session['selected']), None)
                if tuple(r.name for r in choices) == names and selected is not None:
                    self._discover_input(session, 'choice_sent', rule=selected)
            self._discover_stable(session, receipt, lambda value: value is True,
                                  retry=retry_choice)
            session['completed'] = True
            logger.info(f'Chess discovery confirmed: option={session["selected"]}')
            return True
        raise TaskRecoveryFailed('Chess discovery has an invalid pending phase')

    def _resume_discover_event_if_needed(self):
        session = getattr(self, '_chess_discover_session', None)
        choices = self._discover_choices()
        if not choices and (not session or session['completed']):
            return False
        context = self._discover_context()
        if session and session['context'][0] != context[0]:
            self._chess_discover_session = session = None
        if session and not session['completed']:
            if context[1] is not None and session['context'][:2] == context[:2]:
                session['context'] = context  # Keep deadline and inventory across recovery.
            if self._retire_discover_event_if_passed(session, context):
                return False
            self._discover_resume(session)
            return True
        if not choices:
            return False
        if session and session['completed']:
            if context[1] is None or context[1] <= session['context'][1]:
                raise TaskRecoveryFailed('Chess discovery closed panel cannot replay')
        session = self._discover_new_session(context)
        session['phase'] = 'orphan'
        self._discover_resume(session)
        return True

    def _retire_discover_event_if_passed(self, session, context):
        """A later stable board retires uncertainty; it never grants a receipt."""
        mode = self._read_chess_mode()
        if (mode not in ('备', '战', '鬼', '待')
                or context[1] is None or context[1] < session['context'][1]
                or (context[1] == session['context'][1] and mode not in ('战', '鬼', '待'))
                or self._discover_choices() or self.appear(self.I_USE_SOUL)):
            return False
        observer = dict(context=context, deadline=time.monotonic() + 3,
                        modes=(mode,))
        self._discover_stable(observer,
            lambda: not self._discover_choices() and not self.appear(self.I_USE_SOUL)
                    and self._observe_shop_visibility() is False,
            lambda value: value is True)
        session['completed'] = True
        session['confirmed'] = False
        logger.warning('Chess discovery passed without a consumption receipt')
        return True

    def _discover_cards_guarded(self):
        used = 0
        if self._resume_discover_event_if_needed():
            # A resumed event is accounted by its own ledger, never re-consumed.
            return used
        for _ in range(self.DISCOVER_SOUL_SAFETY_LIMIT):
            self.screenshot()
            if (not self._is_preparation_mode()
                    or not self._is_in_chess_game()
                    or self._observe_shop_visibility() is not False):
                break
            cards = self._discover_cards()
            if not cards:
                break
            context = self._discover_context()
            if context[1] is None:
                break
            card = cards[0]
            session = self._discover_new_session(context, card, cards)
            key = self._discover_card_key(cards)
            session['inventory_key'] = key
            self._discover_stable(session,
                lambda: (self._discover_card_key(self._discover_cards()),
                         self._observe_shop_visibility(),
                         bool(self.appear(self.I_USE_SOUL)),
                         bool(self._discover_choices())),
                lambda value: value == (key, False, False, False))
            # Resolve target coordinates from the final observed frame.
            current = self._discover_cards()
            if self._discover_card_key(current) != key:
                raise TaskRecoveryFailed('Chess discovery hand changed before input')
            session['card'] = current[0]
            self._discover_input(session, 'card_sent', point=current[0]['position'])
            self._discover_resume(session)
            used += 1
        return used

    def _reconcile_shop_closed_visual(self):
        """Preparation header is already checked; inspect shop artwork only."""
        if self._shop_refresh_marker_visible():
            return False
        for index in range(1, 6):
            roi = getattr(self, f'O_SHIKIGAMI_GOLD_{index}').roi
            if self.store_gold_rule.match_all_any(self.device.image, roi=list(roi),
                    threshold=self.SHOP_GOLD_ICON_THRESHOLD, nms_threshold=0.3,
                    frame_id=self.device.image_frame_id):
                return False
        return True if self.appear(self.I_MARKET) else None

    def _reconcile_hyakki_board_guarded(self):
        """Read-only two-frame reconciliation; a timeout never aborts the game."""
        pending = getattr(self, '_chess_hyakki_pending', None)
        if not pending:
            return True
        context = self._discover_context()
        if context[0] != pending['game']:
            self._chess_hyakki_pending = None
            return True
        if context[1] is None or context[1] <= pending['round']:
            return False
        if context[1] in self.BOSS_CHALLENGE_ROUNDS:
            self._chess_hyakki_pending = None
            return True
        if self._read_chess_mode() != '备' or self.appear(self.I_SELECT_GRIGRI):
            return False
        if getattr(self.device.image, 'shape', ())[:2] != (720, 1280):
            return False
        checked = pending.get('checked_round')
        if checked is None or context[1] > checked:
            pending['checked_round'] = context[1]
            pending['deadline'] = time.monotonic() + 12
        if time.monotonic() >= pending['deadline']:
            # Allow guarded preparation work while preserving the unresolved
            # observation. Retry with a new budget only in a later round.
            return True
        header = self._hyakki_header()
        positions = dict(self._board_actual_positions)
        names = set(self._board_lineup_names)
        last_frame = getattr(self.device, 'image_frame_id', None)
        previous = None
        while time.monotonic() < pending['deadline']:
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if frame is None or frame == last_frame:
                time.sleep(self.SCREENSHOT_INTERVAL)
                continue
            last_frame = frame
            if (getattr(self.device.image, 'shape', ())[:2] != (720, 1280)
                    or getattr(self.chess_state, 'game_number', 0) != context[0]
                    or getattr(self.device, '_escape_generation', 0) != context[2]
                    or not self._hyakki_same_header(header, self._hyakki_header())):
                return False
            if (self.appear(self.I_SELECT_GRIGRI)
                    or self._reconcile_shop_closed_visual() is not True
                    or self._discover_choices() or self.appear(self.I_USE_SOUL)):
                return False
            absent = tuple(sorted(name for name in names
                if positions.get(name, self.shikigami_deploy_positions.get(name)) is not None
                and not self._board_set_has_shikigami_visual(int(
                    positions.get(name, self.shikigami_deploy_positions.get(name))))))
            if previous is not None and absent == previous:
                if (self._discover_context() != context or self._read_chess_mode() != '备'
                        or time.monotonic() >= pending['deadline']):
                    return False
                self.screenshot()
                final_frame = getattr(self.device, 'image_frame_id', None)
                if (final_frame is None or final_frame == frame
                        or getattr(self.device.image, 'shape', ())[:2] != (720, 1280)
                        or self.chess_state.game_number != context[0]
                        or getattr(self.device, '_escape_generation', 0) != context[2]
                        or not self._hyakki_same_header(header, self._hyakki_header())
                        or self.appear(self.I_SELECT_GRIGRI)
                        or self._reconcile_shop_closed_visual() is not True
                        or self._discover_choices() or self.appear(self.I_USE_SOUL)):
                    return False
                if any(self._board_set_has_shikigami_visual(int(positions.get(
                        name, self.shikigami_deploy_positions.get(name)))) for name in absent):
                    return False
                if time.monotonic() >= pending['deadline']:
                    return False
                for name in absent:
                    position = positions.get(name, self.shikigami_deploy_positions.get(name))
                    self._board_lineup_names.discard(name)
                    self._board_actual_positions.pop(name, None)
                    self._board_shikigami_attributes.pop(name, None)
                    self._player_deployed_positions.discard(int(position))
                self._chess_hyakki_pending = None
                logger.info(f'Chess post-Hyakki stable lineup check: removed={list(absent)}')
                return True
            previous = absent
            time.sleep(self.SCREENSHOT_INTERVAL)
        logger.warning('Chess lineup observation deferred; retain original state and continue preparation')
        return True
