"""Fuzzy matching, fzf style, for the command palette and the file finder. No Qt.

The query's characters must appear in order. Matches score higher when they
are consecutive, start a word, or fall in the file name rather than the folders.
"""

import re


def prefilter(query: str) -> re.Pattern:
    """A fast regex that every match passes (characters in order, any case)."""
    return re.compile(".*?".join(re.escape(c) for c in query), re.IGNORECASE)


def score(query: str, text: str) -> int | None:
    """How well `query` matches `text`, or None if it doesn't. Higher is better.

    Tries the alignments that start at each occurrence of the first character
    (greedy after that) and keeps the best, so "app" prefers ".../app.py" over
    the "a" inside "vimcommander"."""
    if not query:
        return 0
    q, t = query.casefold(), text.casefold()
    best = None
    start = t.find(q[0])
    tries = 0
    while start >= 0 and tries < 16:
        s = _align(q, t, text, start)
        if s is not None and (best is None or s > best):
            best = s
        start = t.find(q[0], start + 1)
        tries += 1
    if best is None:
        return None
    name = t[max(t.rfind("/"), t.rfind("\\")) + 1:]
    if name.startswith(q):
        best += 25  # the file name itself starts with the query
    return best * 10 - len(text) // 4  # shorter texts win ties


def _align(q: str, t: str, text: str, start: int) -> int | None:
    name_start = max(t.rfind("/"), t.rfind("\\")) + 1
    total, ti, previous = 0, start, -2
    for qc in q:
        found = t.find(qc, ti)
        if found < 0:
            return None
        bonus = 1
        if found == previous + 1:
            bonus += 6  # consecutive
        if found == 0 or not t[found - 1].isalnum():
            bonus += 8  # start of a word
        elif text[found].isupper() and text[found - 1].islower():
            bonus += 6  # camelCase hump
        if found >= name_start:
            bonus += 3  # in the file name, not the folders
        total += bonus
        previous, ti = found, found + 1
    return total


def rank(query: str, items: list[str], limit: int = 200, bonus=None) -> list[tuple[int, str]]:
    """The best `limit` (score, item) pairs, best first. `bonus(item)` may add points."""
    if not query:
        return [(0, it) for it in items[:limit]]
    regex = prefilter(query)
    scored = []
    for item in items:
        if regex.search(item):
            s = score(query, item)
            if s is not None:
                scored.append((s + (bonus(item) if bonus else 0), item))
    scored.sort(key=lambda p: -p[0])
    return scored[:limit]
