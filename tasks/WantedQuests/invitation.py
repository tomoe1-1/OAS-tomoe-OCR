"""Per-quest invitation limits, retained across task retries until refresh."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path


def refresh_cycle(now):
    if now.hour < 5:
        return (now - timedelta(days=1)).strftime('%Y-%m-%d') + ':18'
    return now.strftime('%Y-%m-%d') + (':05' if now.hour < 18 else ':18')


def quest_key(texts):
    labels = []
    for prefix in ('自己击败', '好友击败'):
        values = []
        for text in texts:
            text = re.sub(r'\s+', '', text)
            if prefix in text:
                text = text[text.index(prefix):]
                text = re.sub(r'\d{1,2}[/／]\d{1,2}', '', text)
                values.append(text)
        if len(set(values)) != 1:
            return None
        labels.append(values[0])
    return '|'.join(labels)


# Ledger keys have two shapes, and both ends must agree on them.
#
#   'S|<slot>|<type>'      provisional: the card showed no owner labels yet, so
#                          the only identity available was its template slot.
#                          Written before inviting; promoted once labels appear.
#   '<own>|<friend>'       precise: the owner labels, as shown on the expanded
#                          card and later on the panel.
#
# The invitation flow can only produce the first shape BEFORE it invites (the
# labels appear as a consequence of inviting). The panel check can only produce
# the second shape. So the ledger must let a provisional entry be found by the
# panel check afterwards, which it does by tracking the slot entry and matching
# it against the labels observed later -- see InvitationLedger.renames.
PROVISIONAL_PREFIX = 'S|'


def provisional_key(slot, ctype):
    """Build the provisional key, normalising the cooperation type to its name.

    CooperationType is an int-mixin Enum, so '%s' on the member yields '2'
    rather than 'Jade'. Normalising here keeps the stored key identical no
    matter whether the caller passed the member or its name -- otherwise the
    same quest filed from two call sites would get two different keys.
    """
    from tasks.WantedQuests.config import CooperationType
    if isinstance(ctype, CooperationType):
        type_name = ctype.name
    elif ctype is None:
        type_name = None
    else:
        type_name = CooperationType(ctype).name
    return '%s%s|%s' % (PROVISIONAL_PREFIX, slot or '*', type_name or '*')


def precise_key(own, friend):
    """The panel-side key: owner labels only, progress stripped.

    Thin alias over quest_key, kept because it documents which of the two key
    shapes a given call site is allowed to produce.
    """
    return quest_key([own, friend])


def is_provisional(key):
    """True while a ledger entry is still filed under a template slot.

    Call sites use this to tell "the card expanded and gained labels" (promote)
    apart from "the owners of this card changed" (refuse).
    """
    return bool(key) and key.startswith(PROVISIONAL_PREFIX)


class InvitationLedger:
    LIMIT = 2

    def __init__(self, path, now=None):
        self.path = Path(path)
        self.cycle = refresh_cycle(now or datetime.now())
        data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.quests = data.get('quests', {}) if data.get('cycle') == self.cycle else {}

    def state(self, key):
        return self.quests.get(key, dict(sent=0, checked=0, accepted=False))

    def renames(self, old_key, new_key):
        """Re-file an entry once its precise owner labels become readable.

        The invitation flow has to identify a card BEFORE inviting, when only its
        template slot is known, so it writes 'S|<slot>|<type>'. The panel check
        only ever sees the owner labels, so it looks the entry up by them. Without
        this bridge a completed invitation would be forgotten and the same quest
        re-invited on every retry.
        """
        if old_key == new_key or new_key in self.quests or old_key not in self.quests:
            return new_key
        self.quests[new_key] = self.quests.pop(old_key)
        self.save()
        return new_key

    def sent(self, key):
        state = dict(self.state(key))
        if state['accepted'] or state['sent'] >= self.LIMIT or state['checked'] < state['sent']:
            raise ValueError('An invitation is pending, accepted, or already at its limit')
        state['sent'] += 1
        self.quests[key] = state
        self.save()

    def checked(self, key, accepted):
        state = dict(self.state(key))
        state['checked'] = state['sent']
        state['accepted'] = bool(accepted)
        self.quests[key] = state
        self.save()

    def complete(self, key):
        state = self.state(key)
        return (not state['accepted'] and state['sent'] == self.LIMIT
                and state['checked'] == self.LIMIT)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(cycle=self.cycle, quests=self.quests),
                                        ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
