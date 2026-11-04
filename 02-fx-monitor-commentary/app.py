"""FX monitor and checked commentary: interactive demo.

Tabs: see the month's moves and unusual days; watch the paragraph drafted and
checked step by step; try to break the checks; sign off the final text.

Usage: streamlit run app.py
"""
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import config
from claims import check_counts, check_direction_and_attribution, count_facts
from commentary import check_coverage, check_numbers, llm_draft, run_checks, template_draft
from daily_check import ALERT_DIR, LOG as ALERT_LOG, run as run_daily_check
from fetch_rates import fetch_live, sample_rates
from monitor import daily_moves
from monitor import facts_table, month_facts
from tamper import TAMPERINGS

BLUE, RED, INK, MUTED = "#2a78d6", "#e34948", "#0b0b0b", "#52514e"   # diverging poles + text inks

st.set_page_config(page_title="FX monitor and commentary", layout="wide")

# ---------------------------------------------------------------- data
with st.sidebar:
    st.header("Rates")
    src = st.radio("Source", ["Sample (synthetic)", "Live reference rates", "Upload CSV"],
                   help="Sample: random-walk rates with planted shocks, labelled as synthetic. "
                        "Live: the ECB's daily euro reference rates, from the ECB Data Portal, with the "
                        "Frankfurter API (same ECB rates) as a backup. "
                        "Upload: a CSV with columns date, currency, rate (units of currency per 1 EUR).")


@st.cache_data(show_spinner=False)
def load_sample():
    return sample_rates("2024-01-01")


@st.cache_data(show_spinner="Downloading reference rates…", ttl=3600)
def load_live(start, prefer):
    df, notes = fetch_live(start, prefer)
    return df, notes, datetime.now().strftime("%Y-%m-%d %H:%M")


live_notes, fetched_at = [], None
if src.startswith("Sample"):
    rates = load_sample()
elif src.startswith("Live"):
    with st.sidebar:
        prefer_label = st.selectbox("Live source", ["ECB, then Frankfurter if it fails", "ECB only", "Frankfurter only"],
                                    help="Both publish the same ECB reference rates (one fixing per business day, "
                                         "around 16:00 CET). Frankfurter is a free public mirror used as a backup.")
        prefer = {"ECB, then Frankfurter if it fails": "auto", "ECB only": "ecb", "Frankfurter only": "frankfurter"}[prefer_label]
        if st.button("🔄 Refresh rates", help="Download again now instead of using the copy from the last hour."):
            load_live.clear()
    try:
        rates, live_notes, fetched_at = load_live("2024-01-01", prefer)
    except Exception as e:
        st.error(f"Could not download live rates. {e}\n\nSwitch to the sample or upload a CSV.")
        st.stop()
    with st.sidebar:
        st.caption(f"Downloaded {fetched_at} · {rates['source'].iloc[0]} · latest rate date "
                   f"{pd.to_datetime(rates['date']).max().date()}")
        with st.expander("Download log"):
            for n in live_notes:
                st.write(n)
else:
    up = st.sidebar.file_uploader("Rates CSV", type=["csv"], help="Columns: date, currency, rate. One row per currency per day.")
    if not up:
        st.info("Upload a rates CSV in the sidebar, or switch to the sample.")
        st.stop()
    rates = pd.read_csv(up)
    if "source" not in rates:
        rates["source"] = f"uploaded file {up.name}"

rates = rates.copy()
rates["date"] = pd.to_datetime(rates["date"])
last = rates["date"].max()
months = sorted({str(p) for p in rates["date"].dt.to_period("M").unique()})
complete = [m for m in months if pd.Period(m, "M") < last.to_period("M") or last.is_month_end]


def test_groq(key: str) -> str:
    """Cheap check that the key works: list the models it can use."""
    req = urllib.request.Request(f"{config.LLM_BASE_URL}/models",
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": "fx-monitor-portfolio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ids = [m["id"] for m in json.loads(resp.read()).get("data", [])]
        return f"OK: key accepted; {len(ids)} models available" + (
            "" if not ids or st.session_state.get("llm_model", config.LLM_MODEL) in ids
            else f". Note: '{st.session_state.get('llm_model', config.LLM_MODEL)}' is not in the list")
    except urllib.error.HTTPError as e:
        return f"Rejected (HTTP {e.code}). Check the key."
    except Exception as e:
        return f"Could not reach Groq ({e.__class__.__name__})."


with st.sidebar:
    month = st.selectbox("Month", complete[::-1][:12], help="Only complete months are listed.")
    st.header("Drafting")
    env_key = os.environ.get("GROQ_API_KEY", "")
    groq_key = st.text_input(
        "Groq API key", type="password", value="", placeholder="gsk_…" if not env_key else "using GROQ_API_KEY from environment",
        help="Get a free key at console.groq.com. The key stays in this browser session only: it is not "
             "written to any file or log, and it is gone when you close the app.")
    api_key = groq_key.strip() or env_key
    with st.expander("LLM settings"):
        llm_model = st.text_input("Model", value=config.LLM_MODEL, key="llm_model",
                                  help="Any chat model your Groq account offers. Change it if Groq retires this one.")
        if st.button("Test key", disabled=not api_key):
            st.write(test_groq(api_key))
    use_llm = st.checkbox("Use LLM to draft (Groq)", value=bool(api_key), disabled=not api_key,
                          help="Enter a Groq key above to enable. Without it a fixed template writes the paragraph.")
    speed = st.select_slider("Animation speed", ["Fast", "Normal", "Slow"], value="Normal")
    delay = {"Fast": 0.05, "Normal": 0.4, "Slow": 1.0}[speed]
    st.header("Reviewer")
    reviewer = st.text_input("Your name (needed to approve)")

facts = month_facts(rates.assign(date=rates["date"].dt.strftime("%Y-%m-%d")), month)
cs = pd.DataFrame(facts["currencies"])
k = count_facts(facts)

st.title("FX monitor and checked commentary")
st.caption(f"{month} · {facts['source']} · rates are units of currency per 1 EUR; a positive change means the euro strengthened")
if "SYNTHETIC" in facts["source"]:
    st.warning("Sample data: synthetic rates with planted shocks, not market data.")

tab_moves, tab_daily, tab_draft, tab_break, tab_sign = st.tabs(
    ["📈 This month's moves", "🔔 Daily check", "▶ Draft and check", "🧪 Try to break it", "✅ Sign-off"])

# ---------------------------------------------------------------- 1 moves
with tab_moves:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Currencies monitored", k["total"])
    c2.metric("Euro strengthened against", k["up"])
    c3.metric("Euro weakened against", k["down"])
    c4.metric("Unusual months / unusual days", f"{k['unusual_months']} / {k['unusual_days']}")

    st.subheader(f"Euro's move against each currency in {month}")
    bars = cs.assign(
        direction=cs.change_pct.map(lambda v: "Euro strengthened" if v > 0 else "Euro weakened"),
        flag=cs.apply(lambda r: "unusual month" if r.unusual_month else ("unusual day" if r.unusual_days else ""), axis=1),
        label=cs.apply(lambda r: f"{r.change_pct:+.2f}%" + ("  ◆ unusual month" if r.unusual_month else
                                                           ("  ◆ unusual day" if r.unusual_days else "")), axis=1))
    order = bars.sort_values("change_pct").currency.tolist()
    base = alt.Chart(bars).encode(
        y=alt.Y("currency:N", sort=order, title=None, axis=alt.Axis(labelColor=INK, ticks=False, domain=False)),
        tooltip=[alt.Tooltip("currency:N"), alt.Tooltip("change_pct:Q", title="change (%)", format="+.2f"),
                 alt.Tooltip("start_rate:Q", title="start rate"), alt.Tooltip("end_rate:Q", title="end rate"),
                 alt.Tooltip("monthly_z_score:Q", title="monthly z-score"), alt.Tooltip("flag:N", title="flag")])
    bar = base.mark_bar(cornerRadiusEnd=4, height=16).encode(
        x=alt.X("change_pct:Q", title="Change over the month (%)",
                axis=alt.Axis(grid=True, gridColor="#e9e8e4", labelColor=MUTED, titleColor=MUTED, domain=False)),
        color=alt.Color("direction:N", scale=alt.Scale(domain=["Euro strengthened", "Euro weakened"], range=[BLUE, RED]),
                        legend=alt.Legend(title=None, orient="top")))
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=MUTED).encode(x="x:Q")
    # value labels at the end of each bar, outside it: right of positive bars, left of negative ones
    pos = base.transform_filter("datum.change_pct > 0").mark_text(align="left", dx=6, color=INK, fontSize=11)\
        .encode(x="change_pct:Q", text="label:N")
    neg = base.transform_filter("datum.change_pct <= 0").mark_text(align="right", dx=-6, color=INK, fontSize=11)\
        .encode(x="change_pct:Q", text="label:N")
    lo, hi = float(cs.change_pct.min()), float(cs.change_pct.max())
    pad = (hi - lo) * 0.3  # room for the labels
    bar = bar.encode(x=alt.X("change_pct:Q", title="Change over the month (%)",
                             scale=alt.Scale(domain=[min(lo, 0) - pad, max(hi, 0) + pad]),
                             axis=alt.Axis(grid=True, gridColor="#e9e8e4", labelColor=MUTED, titleColor=MUTED,
                                           domain=False)))
    st.altair_chart((bar + zero + pos + neg).properties(height=34 * len(bars)), use_container_width=True)
    st.caption("◆ marks a currency whose month (or a single day) was unusual. Hover a bar for rates and z-score.")

    st.subheader("Daily rate and unusual days")
    pick = st.selectbox("Currency", order[::-1],
                        index=order[::-1].index(next((c for c in order[::-1]
                                                      if bars.set_index("currency").loc[c, "flag"]), order[-1])))
    p = pd.Period(month, "M")
    window = rates[(rates.currency == pick) & (rates.date >= (p - 2).start_time) & (rates.date <= p.end_time)]
    f = cs.set_index("currency").loc[pick]
    marks = pd.DataFrame(f.unusual_days) if f.unusual_days else pd.DataFrame(columns=["date", "move_pct", "z_score"])
    if not marks.empty:
        marks["date"] = pd.to_datetime(marks["date"])
        marks = marks.merge(window[["date", "rate"]], on="date")
    line = alt.Chart(window).mark_line(color=BLUE, strokeWidth=2).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(labelColor=MUTED, grid=False)),
        y=alt.Y("rate:Q", title=f"{pick} per EUR", scale=alt.Scale(zero=False),
                axis=alt.Axis(labelColor=MUTED, titleColor=MUTED, gridColor="#e9e8e4")),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("rate:Q", format=".4f")])
    shade = alt.Chart(pd.DataFrame({"s": [p.start_time], "e": [p.end_time]})).mark_rect(
        color="#f0efec", opacity=0.7).encode(x="s:T", x2="e:T")
    layers = shade + line
    if not marks.empty:
        pts = alt.Chart(marks).mark_point(size=140, filled=True, color=INK, stroke="#fcfcfb", strokeWidth=2).encode(
            x="date:T", y="rate:Q",
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("move_pct:Q", title="daily move (%)", format="+.2f"),
                     alt.Tooltip("z_score:Q", title="z-score")])
        lbl = alt.Chart(marks).mark_text(dy=-14, color=INK, fontSize=11).encode(
            x="date:T", y="rate:Q", text=alt.Text("move_pct:Q", format="+.2f"))
        layers = layers + pts + lbl
    st.altair_chart(layers.properties(height=300), use_container_width=True)
    st.caption(f"Shaded: {month}. Black dots: days whose move exceeded {config.DAILY_Z_THRESHOLD:g} standard deviations "
               f"of the previous {config.VOL_WINDOW_DAYS} business days.")

    with st.expander("Facts table (every number the paragraph may use)"):
        st.markdown(facts_table(facts))

# ---------------------------------------------------------------- daily check
with tab_daily:
    st.markdown("The monthly report looks back; the **daily check** watches each business day. It flags any "
                f"currency whose move was more than {config.DAILY_Z_THRESHOLD:g} standard deviations of its previous "
                f"{config.VOL_WINDOW_DAYS} business days, writes an alert file and logs every run. "
                "`daily_check.py` runs the same check on a schedule (see the README).")
    days = sorted(rates["date"].dt.date.unique())[::-1]
    default_day = days[0]
    if "SYNTHETIC" in str(rates["source"].iloc[0]):
        st.caption("Sample data: try 2026-10-16, the day with the planted shocks.")
    day = st.selectbox("Business day", days[:120], index=0, format_func=lambda d: d.isoformat())
    preview = daily_moves(rates.assign(date=rates["date"].dt.strftime("%Y-%m-%d")), day.isoformat())
    if preview.empty:
        st.info("Not enough history before this day to judge it.")
    else:
        hits = preview[preview.unusual]
        if len(hits):
            st.error(f"🔔 {len(hits)} unusual move(s) on {day}: " +
                     ", ".join(f"{r.currency} {r.move_pct:+.2f}% (z {r.z_score})" for r in hits.itertuples()))
        else:
            st.success(f"No unusual moves on {day}. Largest: {preview.iloc[0].currency} "
                       f"{preview.iloc[0].move_pct:+.2f}% (z {preview.iloc[0].z_score}).")
        zc = alt.Chart(preview.assign(flag=preview.unusual.map({True: "unusual", False: "normal"}))).mark_bar(
            cornerRadiusEnd=4, height=14).encode(
            y=alt.Y("currency:N", sort=preview.sort_values("z_score").currency.tolist(), title=None,
                  axis=alt.Axis(labelOverlap=False, ticks=False, domain=False)),
            x=alt.X("z_score:Q", title="Move in standard deviations (z-score)",
                    scale=alt.Scale(domain=[min(-config.DAILY_Z_THRESHOLD - 1, float(preview.z_score.min()) - 1),
                                            max(config.DAILY_Z_THRESHOLD + 1, float(preview.z_score.max()) + 1)])),
            color=alt.condition("datum.z_score > 0", alt.value(BLUE), alt.value(RED)),
            tooltip=["currency", alt.Tooltip("move_pct:Q", title="move (%)", format="+.2f"), "z_score",
                     "previous_rate", "rate"])
        band = alt.Chart(pd.DataFrame({"x": [-config.DAILY_Z_THRESHOLD], "x2": [config.DAILY_Z_THRESHOLD]})).mark_rect(
            color="#f0efec", opacity=0.8).encode(x="x:Q", x2="x2:Q")
        rules = alt.Chart(pd.DataFrame({"x": [-config.DAILY_Z_THRESHOLD, config.DAILY_Z_THRESHOLD]})).mark_rule(
            color=MUTED, strokeDash=[4, 3]).encode(x="x:Q")
        st.altair_chart((band + rules + zc).properties(height=30 * len(preview)), use_container_width=True)
        st.caption(f"Grey band: normal range (within ±{config.DAILY_Z_THRESHOLD:g}). Bars outside it are flagged. "
                   "Blue: euro strengthened that day; red: euro weakened.")
        if st.button("Run daily check and log it", type="primary"):
            out = run_daily_check(rates.assign(date=rates["date"].dt.strftime("%Y-%m-%d")), day.isoformat(),
                                  notify=False)
            if out["alert_file"]:
                st.warning(f"Alert written to `{out['alert_file']}` and the run added to `{ALERT_LOG}`.")
                st.markdown(Path(out["alert_file"]).read_text())
            else:
                st.success(f"No alert. The run was added to `{ALERT_LOG}`.")
    with st.expander("Alert history"):
        files = sorted(ALERT_DIR.glob("alert_*.md"), reverse=True) if ALERT_DIR.exists() else []
        if not files:
            st.write("No alerts yet.")
        for f in files[:10]:
            st.markdown(f"**{f.stem.replace('alert_', '')}**")
            st.markdown(f.read_text())

# ---------------------------------------------------------------- 2 draft and check
CHECKS = [
    ("Every number and date matches a computed fact", check_numbers),
    ("Each percentage belongs to the currency it is attached to; direction words match the sign",
     check_direction_and_attribution),
    ("Stated counts match the real counts", check_counts),
    ("Every flagged currency is mentioned", check_coverage),
]


def show_checks(text, animate=False):
    all_problems = []
    for label, fn in CHECKS:
        probs = fn(text, facts)
        all_problems += probs
        if probs:
            st.markdown(f"❌ **{label}**")
            for pr in probs:
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;– {pr}")
        else:
            st.markdown(f"✅ {label}")
        if animate:
            time.sleep(delay)
    return all_problems


with tab_draft:
    st.markdown("Python computes every number; the draft only puts them into words; four checks run before a person reads it.")
    if st.button("▶ Draft and check", type="primary"):
        with st.status("Step 1: computing the facts in Python", expanded=True) as s:
            st.write(f"{k['total']} currencies, {month}. Changes, z-scores and unusual days calculated from "
                     f"{len(rates):,} daily rates.")
            time.sleep(delay)
            s.update(label=f"Step 1: facts computed ({k['unusual_months']} unusual month(s), {k['unusual_days']} unusual day(s))",
                     state="complete", expanded=False)
        with st.status("Step 2: drafting the paragraph", expanded=True) as s:
            how = f"LLM ({llm_model} via Groq)" if use_llm else "template (no LLM)"
            st.write(f"Writer: {how}. Instruction: use only numbers from the facts table; no forecasts or advice.")
            try:
                draft = llm_draft(facts, api_key=api_key, model=llm_model) if use_llm else template_draft(facts)
            except Exception as e:
                st.warning(f"LLM call failed: {e}. Using the template instead.")
                draft, how = template_draft(facts), "template (LLM failed)"
            time.sleep(delay)
            s.update(label=f"Step 2: draft written by {how}", state="complete", expanded=False)
        st.markdown("**Draft**")
        st.info(draft)
        with st.status("Step 3: checking the draft", expanded=True) as s:
            probs = show_checks(draft, animate=True)
            s.update(label="Step 3: checks " + ("PASSED" if not probs else f"FAILED ({len(probs)} problem(s))"),
                     state="complete" if not probs else "error", expanded=True)
        st.session_state.update(draft=draft, draft_how=how, draft_month=month)
        st.success("Draft ready. Open **Try to break it** to tamper with it, or **Sign-off** to approve it.")
    elif st.session_state.get("draft_month") == month:
        st.markdown("**Last draft**")
        st.info(st.session_state["draft"])

# ---------------------------------------------------------------- 3 break it
with tab_break:
    base_text = st.session_state.get("draft") if st.session_state.get("draft_month") == month else template_draft(facts)
    st.markdown("Change the paragraph the way an LLM might get it wrong, and watch the checks respond. "
                "Pick a tampering below or edit the text yourself.")
    kind = st.radio("Tamper", ["Original draft"] + list(TAMPERINGS), horizontal=True)
    start = base_text if kind == "Original draft" else TAMPERINGS[kind](base_text, facts)
    if start == base_text and kind != "Original draft":
        st.caption("This draft has nothing to tamper with for that option; edit the text yourself.")
    text = st.text_area("Paragraph", start, height=170, key=f"break_{kind}_{month}")
    left, right = st.columns([3, 2])
    with left:
        probs = show_checks(text)
    with right:
        if probs:
            st.error(f"FAILED: {len(probs)} problem(s). This paragraph cannot be approved.")
        else:
            st.success("PASSED. A reviewer still reads it before it goes in the report.")
        st.caption("Not caught: negations (\"did not weaken\") and unusual wording the rules do not read. "
                   "That is why a person signs off.")

# ---------------------------------------------------------------- 4 sign-off
with tab_sign:
    base_text = st.session_state.get("draft") if st.session_state.get("draft_month") == month else None
    if not base_text:
        st.info("Run **Draft and check** first.")
    else:
        final = st.text_area("Final paragraph (edit if needed; checks re-run on every change)", base_text, height=170,
                             key=f"final_{month}")
        probs = run_checks(final, facts)
        if probs:
            st.error("Checks FAILED, so this text cannot be approved:\n\n" + "\n".join(f"- {p}" for p in probs))
        else:
            st.success("Checks PASSED.")
        confirm = st.checkbox("I have read the paragraph against the facts table, including the direction words")
        disabled = bool(probs) or not reviewer or not confirm
        if not reviewer:
            st.caption("Enter your name in the sidebar to approve.")
        if st.button("Approve for the report", type="primary", disabled=disabled):
            stamp = datetime.now().isoformat(timespec="seconds")
            doc = "\n".join([f"# FX commentary: {month}", "", f"Data source: {facts['source']}.", "",
                             "## Facts (computed in Python)", "", facts_table(facts), "",
                             "## Approved paragraph", "", final, "",
                             f"Drafted by: {st.session_state.get('draft_how')}. Checks: PASSED.",
                             f"Approved by: {reviewer} at {stamp}.", ""])
            out = Path(f"outputs/approved_commentary_{month}.md")
            out.write_text(doc)
            st.success(f"Approved by {reviewer} and saved to {out}.")
            st.download_button("Download approved commentary", doc, file_name=out.name)
