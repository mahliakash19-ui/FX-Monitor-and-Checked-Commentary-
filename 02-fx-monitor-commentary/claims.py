"""Checks on what the draft SAYS about the numbers, not just which numbers it uses.

1. Direction: "the euro rose 8.26% against BRL" is wrong if the euro fell 8.26%.
2. Attribution: "PLN moved 6.25%" is wrong if 6.25% is ZAR's move, not PLN's.
3. Counts: "strengthened against 6 of the 11" is wrong if it was 5.

Convention (ECB quote): rate = units of currency per 1 EUR, so a positive change
means the euro strengthened against that currency.
"""
import re

import config

# words that state a direction, from the point of view of whoever is the subject
UP = r"strengthen\w*|rose|rise[sn]?|gain\w*|appreciat\w*|climb\w*|advanc\w*|firm\w*|increas\w*|up\b"
DOWN = r"weaken\w*|fell|fall\w*|declin\w*|depreciat\w*|drop\w*|lost|los[es]\w*|slid\w*|eas\w*|decreas\w*|down\b"
DIR_RE = re.compile(rf"\b(?:(?P<up>{UP})|(?P<down>{DOWN}))", re.I)
PCT_RE = re.compile(r"(?<![\w.])(?P<num>[-+−]?\d+(?:\.\d+)?)\s?(?:%|per ?cent)", re.I)
EURO_RE = re.compile(r"\beuro\b|\bEUR\b", re.I)


def _currency_re():
    alts = []
    for code in config.CURRENCIES:
        names = [re.escape(n) for n in config.CURRENCY_NAMES.get(code, [])]
        alts.append(rf"(?P<{code}>\b{code}\b{''.join('|' + n for n in names)})")
    return re.compile("|".join(alts), re.I)


CCY_RE = _currency_re()


def sentences(text: str):
    return [s for s in re.split(r"(?<=[.!?;])\s+(?=[A-Z])", text) if s.strip()]


def _entities(s: str):
    """(position, 'EUR' or currency code) for every currency mentioned in the sentence."""
    out = [(m.start(), "EUR") for m in EURO_RE.finditer(s)]
    for m in CCY_RE.finditer(s):
        out.append((m.start(), m.lastgroup))
    return sorted(out)


def check_direction_and_attribution(text: str, facts: dict) -> list:
    by_ccy = {c["currency"]: c for c in facts["currencies"]}
    problems = []
    for s in sentences(text):
        ents = _entities(s)
        dirs = [(m.start(), 1 if m.group("up") else -1, m.group(0)) for m in DIR_RE.finditer(s)]
        for pm in PCT_RE.finditer(s):
            raw = pm.group("num").replace("−", "-")
            val = float(raw)
            # the currency a percentage belongs to: "4.22% against PLN" -> PLN; otherwise the
            # nearest currency (not the euro) before it, else the first one after it
            ccys = [(p, c) for p, c in ents if c != "EUR"]
            follow = re.match(r"\s*(?:against|versus|vs\.?)\s+(?:the\s+)?", s[pm.end():], re.I)
            after_against = [c for p, c in ents if follow and p == pm.end() + follow.end()]
            before = [c for p, c in ccys if p < pm.start()]
            after = [c for p, c in ccys if p > pm.start()]
            if after_against:
                ccy = None if after_against[0] == "EUR" else after_against[0]
                if ccy is None:
                    ccy = before[-1] if before else None
            else:
                ccy = before[-1] if before else (after[0] if after else None)
            if ccy is None or ccy not in by_ccy:
                continue
            f = by_ccy[ccy]
            candidates = [f["change_pct"], f["largest_daily_move_pct"]] + [d["move_pct"] for d in f["unusual_days"]]
            match = next((c for c in candidates if abs(abs(val) - abs(c)) <= config.NUMBER_TOLERANCE), None)
            if match is None:
                owner = next((k for k, g in by_ccy.items() if any(
                    abs(abs(val) - abs(c)) <= config.NUMBER_TOLERANCE
                    for c in [g["change_pct"], g["largest_daily_move_pct"]] + [d["move_pct"] for d in g["unusual_days"]])), None)
                problems.append(f"'{pm.group(0)}' is attached to {ccy}, but it is not one of {ccy}'s moves"
                                + (f" (it is {owner}'s)" if owner else ""))
                continue
            if match == 0:
                continue
            fact_sign = 1 if match > 0 else -1
            # an explicit sign in the text must match the facts table (euro point of view)
            if raw.startswith(("-", "+")):
                text_sign = -1 if raw.startswith("-") else 1
                if text_sign != fact_sign:
                    problems.append(f"'{pm.group(0)}' for {ccy} has the wrong sign; the fact is {match}%")
                continue
            # otherwise use the direction word closest to the number
            if not dirs:
                continue  # neutral wording such as "moved 4.22%" claims no direction
            dpos, dsign, dword = min(dirs, key=lambda d: abs(d[0] - pm.start()))
            # whose movement is the verb describing? the nearest currency mentioned before the verb
            subject = next((c for p, c in reversed(ents) if p < dpos), "EUR")
            euro_view_sign = dsign if subject == "EUR" else -dsign
            if euro_view_sign != fact_sign:
                said = "strengthened" if euro_view_sign > 0 else "weakened"
                actual = "strengthened" if fact_sign > 0 else "weakened"
                problems.append(f"Direction wrong for {ccy}: the sentence says the euro {said} "
                                f"(\"{dword}\"), but the euro {actual} ({match}%)")
    return problems


def count_facts(facts: dict) -> dict:
    cs = facts["currencies"]
    return {
        "total": len(cs),
        "up": sum(c["change_pct"] > 0 for c in cs),
        "down": sum(c["change_pct"] < 0 for c in cs),
        "unusual_months": sum(c["unusual_month"] for c in cs),
        "unusual_days": sum(len(c["unusual_days"]) for c in cs),
        "currencies_with_unusual_days": sum(bool(c["unusual_days"]) for c in cs),
    }


NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
N = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"


def _n(tok):
    return int(tok) if tok.isdigit() else NUMBER_WORDS[tok.lower()]


def check_counts(text: str, facts: dict) -> list:
    k = count_facts(facts)
    rules = [  # (pattern, which count it must equal, description)
        (rf"\b(?:{UP})\s+against\s+(?:{N}\s+of\s+the\s+\d+|{N})\b", "up", "currencies the euro strengthened against"),
        (rf"\b(?:{DOWN})\s+against\s+(?:{N}\s+of\s+the\s+\d+|{N})\b", "down", "currencies the euro weakened against"),
        (rf"\bof\s+the\s+{N}\s+(?:monitored\s+)?currenc", "total", "currencies monitored"),
        (rf"\b{N}\s+(?:monitored\s+)?currencies\s+(?:are|were)\s+monitored", "total", "currencies monitored"),
        (rf"\b{N}\s+(?:currencies\s+)?(?:had|showed|recorded|saw)?\s*(?:an?\s+)?unusual\s+month", "unusual_months", "unusual months"),
        (rf"\b{N}\s+unusual\s+(?:daily\s+)?(?:moves?|days?)", "unusual_days", "unusual daily moves"),
    ]
    problems = []
    for pattern, key, desc in rules:
        for m in re.finditer(pattern, text, re.I):
            tok = next(g for g in m.groups() if g)
            stated = _n(tok)
            if stated != k[key]:
                problems.append(f"Count wrong: \"{m.group(0).strip()}\" states {stated} {desc}; the facts give {k[key]}")
    return problems
