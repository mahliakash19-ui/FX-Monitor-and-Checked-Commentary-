"""Get euro reference rates: live (ECB, with Frankfurter as backup) or a labelled offline sample.

Live:   python fetch_rates.py --start 2024-01-01     (ECB first; Frankfurter if the ECB fails)
        python fetch_rates.py --source frankfurter   (force one source)
Sample: python fetch_rates.py --sample               (synthetic, for testing without internet)

Output: data/rates.csv with columns date, currency, rate (units of currency per 1 EUR), source.
Both live sources publish the same ECB reference rates (one fixing per business day, ~16:00 CET).
"""
import argparse
import io
import json
import urllib.request
from datetime import date

import numpy as np
import pandas as pd

from config import CURRENCIES

SAMPLE_END = "2026-11-02"
ECB_URL = ("https://data-api.ecb.europa.eu/service/data/EXR/D.{ccys}.EUR.SP00.A"
           "?format=csvdata&startPeriod={start}")
# Frankfurter v1 serves the ECB reference rates only (no blending with other providers)
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{start}..{end}?base=EUR&symbols={ccys}"
HEADERS = {"User-Agent": "fx-monitor-portfolio/1.0", "Accept": "*/*"}


def _get(url: str, timeout=60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as resp:
        return resp.read()


# ------------------------------------------------------------------ parsers (testable offline)

def parse_ecb_csv(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text))
    needed = {"CURRENCY", "TIME_PERIOD", "OBS_VALUE"}
    if not needed.issubset(raw.columns):
        raise ValueError(f"Unexpected ECB format; columns were {list(raw.columns)[:8]}")
    df = raw.rename(columns={"TIME_PERIOD": "date", "CURRENCY": "currency", "OBS_VALUE": "rate"})
    df = df[["date", "currency", "rate"]].dropna()
    df["source"] = "ECB euro reference rates (ECB Data Portal)"
    return df


def parse_frankfurter_json(obj: dict) -> pd.DataFrame:
    if obj.get("base") not in (None, "EUR") or "rates" not in obj:
        raise ValueError("Unexpected Frankfurter response (expected base EUR and a 'rates' object)")
    rows = [{"date": d, "currency": c, "rate": float(v)}
            for d, day in obj["rates"].items() for c, v in day.items()]
    df = pd.DataFrame(rows)
    df["source"] = "ECB euro reference rates (via Frankfurter API)"
    return df


# ------------------------------------------------------------------ fetchers

def fetch_ecb(start: str) -> pd.DataFrame:
    return parse_ecb_csv(_get(ECB_URL.format(ccys="+".join(CURRENCIES), start=start)).decode("utf-8"))


def fetch_frankfurter(start: str) -> pd.DataFrame:
    """One request per calendar year, so long ranges come back complete."""
    parts, y0, today = [], int(start[:4]), date.today()
    for y in range(y0, today.year + 1):
        s = start if y == y0 else f"{y}-01-01"
        e = min(date(y, 12, 31), today).isoformat()
        obj = json.loads(_get(FRANKFURTER_URL.format(start=s, end=e, ccys=",".join(CURRENCIES))))
        parts.append(parse_frankfurter_json(obj))
    return pd.concat(parts, ignore_index=True).drop_duplicates(["date", "currency"])


def fetch_live(start: str, prefer: str = "auto"):
    """Return (rates, notes). Tries the ECB, then Frankfurter, unless one is forced."""
    order = {"auto": [("ECB", fetch_ecb), ("Frankfurter", fetch_frankfurter)],
             "ecb": [("ECB", fetch_ecb)], "frankfurter": [("Frankfurter", fetch_frankfurter)]}[prefer]
    notes = []
    for name, fn in order:
        try:
            df = fn(start)
            if df.empty:
                raise ValueError("no rows returned")
            notes.append(f"{name}: OK, {len(df):,} rows")
            return df, notes
        except Exception as e:
            notes.append(f"{name}: failed ({e.__class__.__name__}: {str(e)[:120]})")
    raise RuntimeError("All live sources failed. " + " | ".join(notes))


def sample_rates(start: str, seed: int = 3) -> pd.DataFrame:
    """Random walks around rough rate levels, with a few planted shocks. NOT real data."""
    levels = {"USD": 1.08, "GBP": 0.85, "CHF": 0.95, "INR": 90.0, "SEK": 11.4,
              "PLN": 4.30, "CAD": 1.47, "AUD": 1.65, "ZAR": 20.0, "BRL": 5.9, "MXN": 19.5}
    vols = {"USD": .005, "GBP": .004, "CHF": .004, "INR": .005, "SEK": .005, "PLN": .004,
            "CAD": .005, "AUD": .006, "ZAR": .01, "BRL": .01, "MXN": .009}
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, SAMPLE_END)  # fixed end date: the sample is identical on every run
    rows = []
    for c in CURRENCIES:
        r = rng.normal(0, vols.get(c, .006), len(dates))
        # planted shocks so the monitor has something to find
        if c in ("ZAR", "PLN"):
            r[-12] += 6 * vols.get(c, .006)
        if c == "INR":
            r[-30:-10] += 0.0012  # steady drift over a few weeks
        path = levels.get(c, 1.0) * np.exp(np.cumsum(r))
        rows += [{"date": d.date().isoformat(), "currency": c, "rate": round(float(v), 5)}
                 for d, v in zip(dates, path)]
    df = pd.DataFrame(rows)
    df["source"] = "SYNTHETIC SAMPLE - not market data"
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--source", choices=["auto", "ecb", "frankfurter"], default="auto")
    ap.add_argument("--out", default="data/rates.csv")
    args = ap.parse_args()
    if args.sample:
        df = sample_rates(args.start)
    else:
        df, notes = fetch_live(args.start, args.source)
        for n in notes:
            print(n)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df):,} rows, {df.currency.nunique()} currencies, "
          f"{df.date.min()} to {df.date.max()} -> {args.out} [{df.source.iloc[0]}]")


if __name__ == "__main__":
    main()
