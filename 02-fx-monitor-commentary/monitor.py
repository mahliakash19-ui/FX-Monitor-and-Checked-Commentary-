"""Compute the month's FX facts and flag unusual moves.

All numbers in the final report come from here. The LLM never calculates.

Convention: rates are units of foreign currency per 1 EUR (ECB quote).
A positive change means the euro strengthened against that currency.

Usage: python monitor.py [--month 2026-10] [--in data/rates.csv] [--out outputs/facts_2026-10.json]
"""
import argparse
import json

import numpy as np
import pandas as pd

import config


def month_facts(rates: pd.DataFrame, month: str) -> dict:
    rates = rates.copy()
    rates["date"] = pd.to_datetime(rates["date"])
    period = pd.Period(month, "M")
    source = str(rates["source"].iloc[0]) if "source" in rates else "unknown"
    out = {"month": month, "source": source, "convention": "units of currency per 1 EUR; "
           "positive change = euro strengthened", "daily_z_threshold": config.DAILY_Z_THRESHOLD,
           "monthly_z_threshold": config.MONTHLY_Z_THRESHOLD, "currencies": []}

    for ccy, g in rates.groupby("currency"):
        g = g.sort_values("date").set_index("date")["rate"]
        ret = np.log(g).diff()
        # volatility from PRIOR days only, so a shock does not hide itself
        vol = ret.rolling(config.VOL_WINDOW_DAYS).std().shift(1)
        z = ret / vol

        in_month = g[g.index.to_period("M") == period]
        before = g[g.index.to_period("M") < period]
        if in_month.empty or before.empty:
            continue
        start_rate, end_rate = float(before.iloc[-1]), float(in_month.iloc[-1])
        change_pct = (end_rate / start_rate - 1) * 100

        month_end = g.groupby(g.index.to_period("M")).last()
        monthly_chg = month_end.pct_change() * 100
        history = monthly_chg[monthly_chg.index < period].dropna().tail(12)
        monthly_z = (change_pct / history.std()) if len(history) >= 6 else float("nan")

        zm = z[z.index.to_period("M") == period]
        rm = ret[ret.index.to_period("M") == period]
        biggest_day = rm.abs().idxmax()
        unusual_days = [
            {"date": d.date().isoformat(), "move_pct": round(float(np.expm1(rm[d]) * 100), 2),
             "z_score": round(float(zm[d]), 1)}
            for d in zm.index if abs(zm[d]) >= config.DAILY_Z_THRESHOLD
        ]
        out["currencies"].append({
            "currency": ccy,
            "start_rate": round(start_rate, 4),
            "end_rate": round(end_rate, 4),
            "change_pct": round(change_pct, 2),
            "monthly_z_score": None if np.isnan(monthly_z) else round(float(monthly_z), 1),
            "unusual_month": bool(not np.isnan(monthly_z) and abs(monthly_z) >= config.MONTHLY_Z_THRESHOLD),
            "largest_daily_move_pct": round(float(np.expm1(rm[biggest_day]) * 100), 2),
            "largest_daily_move_date": biggest_day.date().isoformat(),
            "unusual_days": unusual_days,
        })
    out["currencies"].sort(key=lambda c: -abs(c["change_pct"]))
    return out


def facts_table(facts: dict) -> str:
    lines = ["| Currency | Start rate | End rate | Change (%) | Monthly z | Largest daily move (%) | Date | Unusual days |",
             "|---|---|---|---|---|---|---|---|"]
    for c in facts["currencies"]:
        flag = " **(unusual month)**" if c["unusual_month"] else ""
        days = ", ".join(f'{d["date"]} ({d["move_pct"]}%)' for d in c["unusual_days"]) or "none"
        lines.append(f'| {c["currency"]}{flag} | {c["start_rate"]} | {c["end_rate"]} | {c["change_pct"]} | '
                     f'{c["monthly_z_score"]} | {c["largest_daily_move_pct"]} | {c["largest_daily_move_date"]} | {days} |')
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/rates.csv")
    ap.add_argument("--month", default=None, help="YYYY-MM; default = last complete month in the data")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rates = pd.read_csv(args.inp)
    last = pd.to_datetime(rates["date"]).max()
    month = args.month or str((last.to_period("M") - (0 if last.is_month_end else 1)))
    facts = month_facts(rates, month)
    out = args.out or f"outputs/facts_{month}.json"
    with open(out, "w") as fh:
        json.dump(facts, fh, indent=2)
    print(f"FX facts for {month} ({facts['source']}) -> {out}\n")
    print(facts_table(facts))


if __name__ == "__main__":
    main()


def daily_moves(rates: pd.DataFrame, day: str = None) -> pd.DataFrame:
    """Each currency's move on one business day (default: the latest in the data), with its
    z-score against the previous VOL_WINDOW_DAYS days. Used by the daily alert check."""
    rates = rates.copy()
    rates["date"] = pd.to_datetime(rates["date"])
    target = pd.Timestamp(day) if day else rates["date"].max()
    rows = []
    for ccy, g in rates.groupby("currency"):
        g = g.sort_values("date").set_index("date")["rate"]
        g = g[g.index <= target]
        if len(g) < config.VOL_WINDOW_DAYS + 2 or g.index[-1] != target:
            continue
        ret = np.log(g).diff()
        vol = ret.iloc[-config.VOL_WINDOW_DAYS - 1:-1].std()   # previous days only
        z = float(ret.iloc[-1] / vol) if vol else float("nan")
        rows.append({"date": target.date().isoformat(), "currency": ccy,
                     "previous_rate": round(float(g.iloc[-2]), 4), "rate": round(float(g.iloc[-1]), 4),
                     "move_pct": round(float(np.expm1(ret.iloc[-1]) * 100), 2), "z_score": round(z, 1),
                     "unusual": bool(abs(z) >= config.DAILY_Z_THRESHOLD)})
    return pd.DataFrame(rows).sort_values("z_score", key=abs, ascending=False) if rows else pd.DataFrame()
