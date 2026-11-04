"""Draft the FX paragraph of a monthly treasury report, then check it.

The draft comes from an LLM (Groq, if GROQ_API_KEY is set) or from a fixed
template. Either way, two checks run before a human sees it:

  1. Number check: every number in the draft must match a computed fact.
  2. Coverage check: every currency flagged as unusual must be mentioned.

  3. Direction and attribution: each percentage must belong to the currency it is
     attached to, and "rose"/"fell" must match the sign of the move.
  4. Counts: "against 5 of the 11" must match the real counts.

A draft that fails is marked FAILED and lists what is wrong. The reviewer
signs off at the bottom of the output file in all cases.

Usage: python commentary.py [--facts outputs/facts_2026-10.json] [--inject-error]
"""
import argparse
import glob
import json
import os
import re
import urllib.error
import urllib.request

import config
from claims import CCY_RE, check_counts, check_direction_and_attribution, count_facts
from monitor import facts_table

SYSTEM_PROMPT = """You write the FX paragraph of a corporate treasury's monthly report.
Rules:
- Use ONLY numbers that appear in the facts table, copied exactly as written. Never calculate, round differently or estimate.
- Rates are units of currency per 1 EUR. A positive change means the euro strengthened against that currency; a negative change means it weakened.
- Write every move from the euro's point of view ("the euro strengthened 4.22% against PLN") and attach each percentage to the currency it belongs to.
- If you state a count ("against 5 of the 11 currencies"), take it from the counts line.
- Mention every currency marked "(unusual month)" and every currency with unusual days.
- No forecasts, no explanations of causes, no hedging advice.
- 120 to 180 words, plain prose, no bullet points, no headings."""


def llm_draft(facts: dict, api_key: str = None, model: str = None) -> str:
    """Draft with Groq. The key comes from the caller (the app's sidebar) or GROQ_API_KEY."""
    key = api_key or os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("No Groq API key provided")
    body = json.dumps({
        "model": model or config.LLM_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Month: {facts['month']}\n\nFacts table:\n{facts_table(facts)}\n\n"
                                        f"Counts: {count_facts(facts)}"},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{config.LLM_BASE_URL}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "fx-monitor-portfolio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:  # turn Groq's error body into a readable message
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Groq returned HTTP {e.code}: {detail}") from None


def template_draft(facts: dict) -> str:
    """Deterministic fallback so the pipeline runs without an API key."""
    cs = facts["currencies"]
    up = [c for c in cs if c["change_pct"] > 0]
    down = [c for c in cs if c["change_pct"] < 0]
    parts = [f"In {facts['month']}, the euro strengthened against {len(up)} of the "
             f"{len(cs)} monitored currencies and weakened against {len(down)}."]
    if up:
        top = max(up, key=lambda c: c["change_pct"])
        parts.append(f"The largest euro gain was against {top['currency']} ({top['change_pct']}%, "
                     f"from {top['start_rate']} to {top['end_rate']}).")
    if down:
        bot = min(down, key=lambda c: c["change_pct"])
        parts.append(f"The largest euro decline was against {bot['currency']} ({bot['change_pct']}%, "
                     f"from {bot['start_rate']} to {bot['end_rate']}).")
    for c in cs:
        if c["unusual_month"]:
            parts.append(f"{c['currency']} moved {c['change_pct']}% over the month, a z-score of "
                         f"{c['monthly_z_score']} against its previous monthly moves.")
        for d in c["unusual_days"]:
            parts.append(f"{c['currency']} moved {d['move_pct']}% on {d['date']}, a z-score of {d['z_score']}.")
    return " ".join(parts)


NUM_RE = re.compile(r"(?<![\w.])[-+−]?\d+(?:\.\d+)?")
DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")


def allowed_numbers(facts: dict) -> set:
    year = float(facts["month"].split("-")[0])
    # settings and calendar numbers an LLM may legitimately write ("October 2026", "60-day")
    vals = {config.DAILY_Z_THRESHOLD, config.MONTHLY_Z_THRESHOLD, float(config.VOL_WINDOW_DAYS), 12.0, year}
    for c in facts["currencies"]:
        for k in ("start_rate", "end_rate", "change_pct", "monthly_z_score", "largest_daily_move_pct"):
            if c[k] is not None:
                vals.add(float(c[k]))
        for d in c["unusual_days"]:
            vals.update({float(d["move_pct"]), float(d["z_score"])})
    vals |= {abs(v) for v in vals}  # "fell 1.63%" is the same fact as -1.63
    # only the actual counts may appear; check_counts() verifies each one is used in the right place
    vals |= {float(v) for v in count_facts(facts).values()}
    return vals


def check_numbers(text: str, facts: dict) -> list:
    allowed = allowed_numbers(facts)
    stripped = DATE_RE.sub(" ", text)  # dates are checked separately
    problems = []
    for m in NUM_RE.findall(stripped):
        v = float(m.replace("−", "-"))
        if not any(abs(v - a) <= config.NUMBER_TOLERANCE for a in allowed):
            problems.append(f"Number {m} does not match any computed fact")
    known_dates = {facts["month"]} | {c["largest_daily_move_date"] for c in facts["currencies"]} | {
        d["date"] for c in facts["currencies"] for d in c["unusual_days"]}
    for d in DATE_RE.findall(text):
        if d not in known_dates:
            problems.append(f"Date {d} does not appear in the facts")
    return problems


def check_coverage(text: str, facts: dict) -> list:
    must = [c["currency"] for c in facts["currencies"] if c["unusual_month"] or c["unusual_days"]]
    mentioned = {m.lastgroup for m in CCY_RE.finditer(text)}
    return [f"Flagged currency {c} is not mentioned" for c in must if c not in mentioned]


def run_checks(text: str, facts: dict) -> list:
    return (check_numbers(text, facts) + check_direction_and_attribution(text, facts)
            + check_counts(text, facts) + check_coverage(text, facts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", default=None)
    ap.add_argument("--inject-error", action="store_true",
                    help="change one number in the draft to show the check catching it")
    args = ap.parse_args()
    path = args.facts or sorted(glob.glob("outputs/facts_*.json"))[-1]
    facts = json.load(open(path))

    if os.environ.get("GROQ_API_KEY"):
        draft, how = llm_draft(facts), f"LLM ({config.LLM_MODEL} via Groq)"
    else:
        draft, how = template_draft(facts), "template (no GROQ_API_KEY set)"
    if args.inject_error:
        draft = re.sub(r"(\d+\.\d+)%", lambda m: f"{float(m.group(1)) + 0.4:.2f}%", draft, count=1)
        how += " + injected error"

    problems = run_checks(draft, facts)
    status = "PASSED" if not problems else "FAILED"
    report = "\n".join([
        f"# FX commentary draft: {facts['month']}", "",
        f"Data source: {facts['source']}. Convention: {facts['convention']}.", "",
        "## Facts (computed in Python)", "", facts_table(facts), "",
        "## Draft commentary", "", f"_Written by: {how}_", "", draft, "",
        f"## Automated checks: {status}", "",
        *([f"- {p}" for p in problems] or ["- All numbers match computed facts; all flagged currencies are mentioned."]),
        "", "## Review", "",
        "Reviewed by: ________  Date: ________  Decision: approve / edit / reject", "",
    ])
    out = f"outputs/commentary_{facts['month']}{'_error-test' if args.inject_error else ''}.md"
    open(out, "w").write(report)
    print(f"Draft by {how}. Checks {status}. -> {out}")
    for p in problems:
        print("  -", p)


if __name__ == "__main__":
    main()
