"""Conservative text matching and low-confidence numeric validation."""
import math
import re


def is_numeric_text(text, mode):
    """Only rescue complete numeric expressions, never guessed characters."""
    text = str(text).strip()
    if mode == 'DIGIT':
        return re.fullmatch(r'[0-9]+', text) is not None
    if mode == 'DIGITCOUNTER':
        match = re.fullmatch(r'([0-9]+)\s*/\s*([0-9]+)', text)
        return bool(match and int(match[1]) <= int(match[2]))
    if mode == 'QUANTITY':
        return re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:千|万|亿)?', text) is not None
    return False


def keyword_indices(items, keyword):
    """Match an entire keyword in a box or a contiguous line of nearby boxes.

    Text in unrelated rows/columns must never create a clickable keyword.
    Detector ordering is not assumed; both horizontal and vertical lines work.
    """
    if not isinstance(keyword, str) or not keyword:
        return []
    texts = [str(item.ocr_text) for item in items]
    for index, text in enumerate(texts):
        if keyword in text:
            return [index]  # never merge separate occurrences into one click area
    if len(items) > 128:
        return []  # bound grouping work; exact single boxes were already checked
    rects = []
    for item in items:
        try:
            points = item.box
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            rect = min(xs), min(ys), max(xs), max(ys)
            if not all(math.isfinite(value) for value in rect):
                raise ValueError('non-finite box')
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                raise ValueError('empty box')
            rects.append(rect)
        except (TypeError, ValueError, IndexError):
            rects.append(None)
    for vertical in (False, True):
        # Choose only the nearest compatible next box; ambiguity rejects a chain.
        successors = {}
        for index, rect in enumerate(rects):
            if rect is None or not texts[index]:
                continue
            a0, p0, a1, p1 = ((rect[1], rect[0], rect[3], rect[2])
                              if vertical else rect)
            candidates = []
            for other, next_rect in enumerate(rects):
                if other == index or next_rect is None or not texts[other]:
                    continue
                b0, q0, b1, q1 = ((next_rect[1], next_rect[0], next_rect[3], next_rect[2])
                                  if vertical else next_rect)
                span, other_span = p1 - p0, q1 - q0
                gap = b0 - a1
                overlap = min(p1, q1) - max(p0, q0)
                if (b0 >= a1 - min(span, other_span) * 0.1
                        and b1 > a1
                        and gap <= min(span, other_span)
                        and overlap >= 0.7 * min(span, other_span)
                        and max(span, other_span) <= 2 * min(span, other_span)):
                    candidates.append((max(0.0, gap), other))
            candidates.sort()
            if candidates and (len(candidates) == 1
                               or candidates[1][0] - candidates[0][0] > 1.0):
                successors[index] = candidates[0][1]
        for start in range(len(items)):
            chain, joined = [], ''
            index = start
            while index not in chain and len(chain) <= len(keyword):
                chain.append(index)
                old_length = len(joined)
                joined += texts[index]
                offset = joined.find(keyword)
                if offset >= 0:
                    end = offset + len(keyword)
                    cursor = 0
                    matched = []
                    for member in chain:
                        next_cursor = cursor + len(texts[member])
                        if cursor < end and next_cursor > offset:
                            matched.append(member)
                        cursor = next_cursor
                    return sorted(matched)
                # No suffix can begin this keyword: adding another box cannot help.
                if not any(joined.endswith(keyword[:length])
                           for length in range(1, len(keyword))):
                    break
                if index not in successors or len(joined) == old_length:
                    break
                index = successors[index]
    return []
