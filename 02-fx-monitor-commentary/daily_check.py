"""Daily FX monitor: check the latest business day for unusual moves and raise an alert.

Run it once a day (for example at 17:00 Paris time, after the ECB publishes ~16:00 CET):
    python daily_check.py                      # live rates (ECB, Frankfurter as backup)
    python daily_check.py --sample --date 2026-10-16   # offline demo on the synthetic sample

Every run appends one line per currency to outputs/alerts_log.csv. When at least one
currency moved more than DAILY_Z_THRESHOLD standard deviations, it also writes
outputs/alerts/alert_YYYY-MM-DD.md and, if email settings are present, sends it.

Optional email (all four must be set as environment variables):
    ALERT_SMTP_HOST, ALERT_SMTP_USER, ALERT_SMTP_PASSWORD, ALERT_TO   (port 587, STARTTLS)
"""
import argparse
import os
import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

import config
from fetch_rates import fetch_live, sample_rates
from monitor import daily_moves

LOG = Path("outputs/alerts_log.csv")
ALERT_DIR = Path("outputs/alerts")


def alert_text(moves: pd.DataFrame, source: str) -> str:
    hits = moves[moves.unusual]
    day = moves.date.iloc[0]
    lines = [f"# FX alert: {day}", "",
             f"{len(hits)} currency(ies) moved more than {config.DAILY_Z_THRESHOLD:g} standard deviations of "
             f"their previous {config.VOL_WINDOW_DAYS} business days.", "",
             f"Source: {source}. Rates are units of currency per 1 EUR; a positive move means the euro strengthened.", "",
             "| Currency | Previous rate | Rate | Move (%) | z-score |", "|---|---|---|---|---|"]
    lines += [f"| {r.currency} | {r.previous_rate} | {r.rate} | {r.move_pct:+.2f} | {r.z_score} |"
              for r in hits.itertuples()]
    lines += ["", "This is an automated flag, not an explanation. A treasury analyst should check the move "
                  "against exposures and hedges before acting.", ""]
    return "\n".join(lines)


def send_email(subject: str, body: str) -> str:
    keys = ["ALERT_SMTP_HOST", "ALERT_SMTP_USER", "ALERT_SMTP_PASSWORD", "ALERT_TO"]
    if not all(os.environ.get(k) for k in keys):
        return "email not configured (skipped)"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ["ALERT_SMTP_USER"], os.environ["ALERT_TO"]
    msg.set_content(body)
    with smtplib.SMTP(os.environ["ALERT_SMTP_HOST"], 587, timeout=30) as s:
        s.starttls()
        s.login(os.environ["ALERT_SMTP_USER"], os.environ["ALERT_SMTP_PASSWORD"])
        s.send_message(msg)
    return f"email sent to {os.environ['ALERT_TO']}"


def run(rates: pd.DataFrame, day: str = None, notify: bool = True) -> dict:
    """Check one day. Returns a summary the app can display."""
    moves = daily_moves(rates, day)
    if moves.empty:
        return {"status": "no data", "moves": moves, "alert_file": None, "email": ""}
    source = str(rates["source"].iloc[0]) if "source" in rates else "unknown"
    stamp = datetime.now().isoformat(timespec="seconds")
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    moves.assign(checked_at=stamp, source=source).to_csv(LOG, mode="a", header=not LOG.exists(), index=False)
    if not moves.unusual.any():
        return {"status": "no unusual moves", "moves": moves, "alert_file": None, "email": ""}
    text = alert_text(moves, source)
    f = ALERT_DIR / f"alert_{moves.date.iloc[0]}.md"
    f.write_text(text)
    email = ""
    if notify:
        try:
            email = send_email(f"FX alert {moves.date.iloc[0]}: "
                               + ", ".join(moves[moves.unusual].currency), text)
        except Exception as e:
            email = f"email failed ({e.__class__.__name__}: {e})"
    return {"status": "ALERT", "moves": moves, "alert_file": str(f), "email": email}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="use the synthetic sample instead of live rates")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD; default: latest business day in the data")
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()
    if args.sample:
        rates = sample_rates("2024-01-01")
    else:
        start = (pd.Timestamp(date.today()) - pd.DateOffset(months=6)).date().isoformat()
        rates, notes = fetch_live(start)
        for n in notes:
            print(n)
    out = run(rates, args.date, notify=not args.no_email)
    if out["moves"].empty:
        print("No data for that date.")
        return
    print(f"{out['moves'].date.iloc[0]}: {out['status']}")
    print(out["moves"].to_string(index=False))
    if out["alert_file"]:
        print(f"Alert written to {out['alert_file']}; {out['email']}")


if __name__ == "__main__":
    main()
