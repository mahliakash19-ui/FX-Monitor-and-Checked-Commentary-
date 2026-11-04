"""Ways to break a draft on purpose, used by the app's "Try to break it" tab and by tests."""
import re

import config
from claims import DIR_RE, PCT_RE, sentences

OPPOSITE = {"strengthened": "weakened", "weakened": "strengthened", "gain": "decline", "decline": "gain",
            "rose": "fell", "fell": "rose", "gained": "lost", "lost": "gained", "strengthen": "weaken",
            "weaken": "strengthen", "declined": "gained", "rise": "fall", "fall": "rise"}


def flip_direction(text):
    """Reverse the direction word in the first sentence that states a percentage with a direction."""
    parts = sentences(text)
    for i, s in enumerate(parts):
        m = DIR_RE.search(s)
        if m and PCT_RE.search(s) and m.group(0).lower() in OPPOSITE:
            new = OPPOSITE[m.group(0).lower()]
            parts[i] = s[:m.start()] + new + s[m.end():]
            return " ".join(parts)
    return text


def change_number(text):
    return re.sub(r"(\d+\.\d+)%", lambda m: f"{float(m.group(1)) + 0.4:.2f}%", text, count=1)


def wrong_currency(text):
    codes = [c for c in config.CURRENCIES if re.search(rf"\b{c}\b", text)]
    if len(codes) < 2:
        return text
    a, b = codes[0], codes[1]
    # swap the first mention of each, so a real figure ends up next to the other currency
    return re.sub(rf"\b({a}|{b})\b", lambda m: b if m.group(1) == a else a, text, count=2)


def wrong_count(text):
    return re.sub(r"against (\d+) of", lambda m: f"against {int(m.group(1)) + 1} of", text, count=1)


def invent_date(text):
    return re.sub(r"(\d{4}-\d{2}-)(\d{2})", lambda m: f"{m.group(1)}{int(m.group(2)) % 28 + 1:02d}", text, count=1)


def drop_flagged(text, facts):
    flagged = [c["currency"] for c in facts["currencies"] if c["unusual_month"] or c["unusual_days"]]
    if not flagged:
        return text
    return " ".join(s for s in sentences(text) if flagged[-1] not in s)


TAMPERINGS = {
    "Flip a direction": lambda t, f: flip_direction(t),
    "Change a number": lambda t, f: change_number(t),
    "Give a figure to the wrong currency": lambda t, f: wrong_currency(t),
    "Wrong count": lambda t, f: wrong_count(t),
    "Invent a date": lambda t, f: invent_date(t),
    "Leave out a flagged currency": drop_flagged,
}
