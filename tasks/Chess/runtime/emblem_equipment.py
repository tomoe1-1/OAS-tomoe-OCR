"""Prepare-stage emblem equipment with visual consumption receipts and fresh unconsumed retries."""

import time
import cv2
import numpy as np

from module.logger import logger
from tasks.Chess.strategy.grigri import grigri_bond_name, grigri_category, resolve_grigri_name
from tasks.Chess.strategy.shikigami_catalog import SHIKIGAMI_BONDS_BY_ROMAJI


class ChessEmblemEquipmentMixin:
    def _guarded_emblem_targets(self, emblem_name, verified_names):
        bond = grigri_bond_name(emblem_name)
        strategy = self.get_lineup_strategy()['shikigami']
        lineup_bonds = {b for name in strategy
                        for b in SHIKIGAMI_BONDS_BY_ROMAJI.get(name, ())}
        if bond is None or bond not in lineup_bonds:
            return []
        targets = []
        for name in verified_names:
            if name in getattr(self, '_chess_emblem_uncertain_targets', set()):
                continue
            config = strategy.get(name)
            if config is None or bond in SHIKIGAMI_BONDS_BY_ROMAJI.get(name, ()):
                continue
            _, first, second = self._shikigami_attributes(name)
            gear = (first, second)
            if all(gear) or any(grigri_bond_name(item) == bond for item in gear if item):
                continue
            # Leave one slot for a declared preferred soul if it is still missing.
            preferred = config.get('preferred_souls', ())
            if preferred and sum(item is None for item in gear) == 1 and not any(item in preferred for item in gear):
                continue
            position = getattr(self, '_board_actual_positions', {}).get(name, config['position'])
            if self._shikigami_name_at_set(int(position)) != name:
                continue
            targets.append((name, int(position)))
        return sorted(targets, key=lambda item: (
            bool(strategy[item[0]].get('preferred_souls')),
            strategy[item[0]].get('deploy_weight', 99), item[1]))

    def _emblem_hand_positions(self):
        positions = []
        roi_x, roi_y = self.O_BADGE_AREA.roi[:2]
        for result in self.O_BADGE_AREA.detect_and_ocr(self.device.image):
            if '纹章' not in self._normalize_ocr_text(result.ocr_text):
                continue
            points = result.box
            positions.append((roi_x + (min(int(p[0]) for p in points)
                                      + max(int(p[0]) for p in points)) // 2,
                              roi_y + (min(int(p[1]) for p in points)
                                      + max(int(p[1]) for p in points)) // 2))
        return positions

    def _emblem_context(self):
        return (getattr(getattr(self, 'chess_state', None), 'game_number', 0),
                self._read_round_number(), getattr(self.device, '_escape_generation', 0))

    def _emblem_valid(self, session):
        return (time.monotonic() < session['deadline']
                and self._emblem_context() == session['context']
                and self._is_preparation_mode()
                and self._observe_shop_visibility() is False
                and self._shikigami_name_at_set(session['position']) == session['target']
                and self._board_set_has_shikigami_visual(session['position'])
                and time.monotonic() < session['deadline'])

    def _emblem_inventory(self):
        return tuple(sorted(card['name'] for card in self._recognized_emblem_hand_cards()
                            if card['similarity'] == 1.0))

    def _emblem_snapshot(self):
        """Keep every text, including unknown OCR, in the receipt comparison."""
        texts, cards, unknown = [], [], False
        x, y = self.O_BADGE_AREA.roi[:2]
        for result in self.O_BADGE_AREA.detect_and_ocr(self.device.image):
            text = self._normalize_ocr_text(result.ocr_text).strip('()（）[]【】')
            if not text:
                continue
            texts.append(text)
            name, similarity = resolve_grigri_name(text)
            if name is not None and grigri_category(name) == 'emblem':
                if similarity != 1.0:
                    unknown = True
                    continue
                points = result.box
                left, right = min(int(p[0]) for p in points), max(int(p[0]) for p in points)
                top, bottom = min(int(p[1]) for p in points), max(int(p[1]) for p in points)
                cards.append(dict(name=name, text=text, similarity=similarity,
                    text_roi=(x + left, y + top, right - left, bottom - top),
                    position=(x + (min(int(p[0]) for p in points) + max(int(p[0]) for p in points)) // 2,
                              y + (min(int(p[1]) for p in points) + max(int(p[1]) for p in points)) // 2)))
            elif '纹' in text and text != '发现纹章':
                unknown = True
        return tuple(sorted(texts)), cards, unknown

    def _emblem_text_visual_count(self, patch):
        """Reject an OCR disappearance while its actual name remains visible."""
        x, y, width, height = self.O_BADGE_AREA.roi
        area = self.device.image[y:y + height, x:x + width]
        if patch is None or not patch.size or float(np.std(patch)) < 10:
            return None
        if area.shape[0] < patch.shape[0] or area.shape[1] < patch.shape[1]:
            return None
        scores = cv2.matchTemplate(area, patch, cv2.TM_CCOEFF_NORMED)
        count = 0
        while scores.size:
            _, score, _, location = cv2.minMaxLoc(scores)
            if score < 0.85:
                break
            count += 1
            px, py = location
            dx, dy = max(8, patch.shape[1] // 2), max(4, patch.shape[0] // 2)
            scores[max(0, py-dy):py+dy+1, max(0, px-dx):px+dx+1] = -1
        return count

    def _emblem_final_drag(self, session):
        previous = getattr(self.device, 'image_frame_id', None)
        self.screenshot()
        frame = getattr(self.device, 'image_frame_id', None)
        if frame is None or frame == previous or not self._emblem_valid(session):
            return None
        texts, cards, unknown = self._emblem_snapshot()
        card = next((c for c in cards if c['name'] == session['name']), None)
        budget = getattr(self, '_preparation_work_deadline', None)
        if (unknown or texts != session['texts'] or card is None or not self._emblem_valid(session)
                or (budget is not None and time.monotonic() + 2 >= budget)):
            return None
        session['input_frame'] = getattr(self.device, 'image_frame_id', None)
        return card['position'], self._soul_target_position(session['position'])

    def _settle_emblem(self, session):
        last = getattr(self.device, 'image_frame_id', None)
        count = 0
        expected = list(session['texts'])
        expected.remove(session['text'])
        while time.monotonic() < session['deadline']:
            self.screenshot()
            if not self._emblem_valid(session):
                return False
            frame = getattr(self.device, 'image_frame_id', None)
            if frame is None or frame == last:
                time.sleep(self.SCREENSHOT_INTERVAL)
                continue
            last = frame
            texts, cards, unknown = self._emblem_snapshot()
            visual = self._emblem_text_visual_count(session['text_patch'])
            count = count + 1 if (not unknown and bool(texts) and texts == tuple(expected)
                and visual == session['visual_before'] - 1) else 0
            if count >= 2:
                if not self._emblem_valid(session):
                    return False
                if not self._record_shikigami_soul(session['target'], session['name']):
                    return False
                session['completed'] = True
                getattr(self, '_chess_emblem_uncertain_targets', set()).discard(session['target'])
                getattr(self, '_chess_emblem_uncertain_names', set()).discard(session['name'])
                logger.info(f'Chess emblem confirmed: {session["name"]} -> {session["target"]}')
                return True
            time.sleep(self.SCREENSHOT_INTERVAL)
        return False

    def _emblem_unconsumed_retry(self, session):
        """Retry only when two new frames prove the original card still exists."""
        context = self._emblem_context()
        if (context[0] != session['context'][0] or context[1] is None
                or session['context'][1] is None or context[1] < session['context'][1]):
            return False
        deadline = self._chess_emblem_event_budgets.get(context[1], 0)
        budget = getattr(self, '_preparation_work_deadline', None)
        if budget is not None:
            deadline = min(deadline, budget)
        previous = getattr(self.device, 'image_frame_id', None)
        for _ in range(2):
            if time.monotonic() >= deadline:
                return False
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if (frame is None or frame == previous or self._emblem_context() != context
                    or not self._is_preparation_mode()
                    or self._observe_shop_visibility() is not False):
                return False
            previous = frame
            texts, cards, unknown = self._emblem_snapshot()
            if (unknown or texts != session['texts']
                    or not any(c['name'] == session['name'] for c in cards)
                    or self._emblem_text_visual_count(session['text_patch']) != session['visual_before']):
                return False
        if self._emblem_context() != context or time.monotonic() >= deadline:
            return False
        self._chess_emblem_uncertain_targets.discard(session['target'])
        self._chess_emblem_uncertain_names.discard(session['name'])
        return True

    def _equip_emblems_guarded(self, verified_names, max_actions=None):
        session = getattr(self, '_chess_emblem_session', None)
        game = getattr(getattr(self, 'chess_state', None), 'game_number', 0)
        if getattr(self, '_chess_emblem_game', None) != game:
            self._chess_emblem_game = game
            self._chess_emblem_session = session = None
            self._chess_emblem_uncertain_targets = set()
            self._chess_emblem_uncertain_names = set()
            self._chess_emblem_event_budgets = {}
        event_context = self._emblem_context()
        if event_context[1] is None:
            return []
        if (session and session['context'][0] == event_context[0]
                and (session['context'][1] is None or event_context[1] < session['context'][1])):
            return []
        budgets = self._chess_emblem_event_budgets
        if budgets and event_context[1] < max(budgets):
            return []  # A backward OCR round is not a new event budget.
        event_deadline = budgets.setdefault(event_context[1], time.monotonic() + 24)
        work_deadline = getattr(self, '_preparation_work_deadline', None)
        if work_deadline is not None:
            event_deadline = min(event_deadline, work_deadline)
        if time.monotonic() >= event_deadline:
            return []
        if session and session['sent'] and not session['completed']:
            current = self._emblem_context()
            if session['context'] == current and time.monotonic() < session['deadline']:
                if self._settle_emblem(session):
                    return [session['name']]
            if not self._emblem_unconsumed_retry(session):
                return []
            self._chess_emblem_session = None
            session = None
        equipped = []
        limit = self.SOUL_EQUIP_SAFETY_LIMIT if max_actions is None else max(0, int(max_actions))
        for _ in range(limit):
            self.screenshot()
            if not self._is_preparation_mode() or not self._ensure_shop_closed():
                break
            self.screenshot()
            context = self._emblem_context()
            if context != event_context or time.monotonic() >= event_deadline:
                break
            texts, cards, unknown = self._emblem_snapshot()
            if unknown:
                break
            card = next((card for card in cards
                         if card['name'] not in self._chess_emblem_uncertain_names
                         and card['similarity'] == 1.0 and self._emblem_targets(card['name'], verified_names)), None)
            if card is None:
                break
            target, position = self._emblem_targets(card['name'], verified_names)[0]
            x, y, width, height = card['text_roi']
            patch = self.device.image[y:y+height, x:x+width].copy()
            visual_before = self._emblem_text_visual_count(patch)
            if visual_before is None or visual_before < 1:
                break
            session = dict(context=context, name=card['name'], target=target,
                           text=card['text'], texts=texts,
                           text_patch=patch, visual_before=visual_before,
                           position=position, inventory=self._emblem_inventory(),
                           deadline=min(time.monotonic() + 8, event_deadline),
                           sent=False, completed=False)
            self._chess_emblem_session = session
            if not self._emblem_valid(session):
                break
            previous = getattr(self.device, 'image_frame_id', None)
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if frame is None or frame == previous or not self._emblem_valid(session):
                break
            refreshed = next((c for c in self._recognized_emblem_hand_cards()
                              if c['name'] == card['name'] and c['similarity'] == 1.0), None)
            if refreshed is None or self._emblem_inventory() != session['inventory']:
                break
            if not self._emblem_valid(session):
                break
            session['sent'] = True
            self._chess_emblem_uncertain_targets = set(getattr(self, '_chess_emblem_uncertain_targets', set())) | {target}
            self._chess_emblem_uncertain_names.add(card['name'])
            # Existing drag helper performs its own preparation/shop/budget guards.
            dispatched = self._equip_soul_card(refreshed['position'], position,
                                               f'EMBLEM_{card["name"]}',
                                               pre_drag_guard=lambda: self._emblem_final_drag(session))
            if not dispatched or not self._settle_emblem(session):
                break
            equipped.append(card['name'])
        return equipped
