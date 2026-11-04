# FX Movement Monitor with a Checked Monthly Commentary

Flags unusual monthly and daily moves in a basket of currencies against the euro, drafts the FX paragraph of a monthly treasury report, and checks every number in the draft against the computed figures before a person signs off.

## Problem

Monthly treasury reporting repeats the same work: pull rates, compute changes, spot what moved unusually, write a paragraph. Generative AI can draft the paragraph, but a model that invents or mis-copies a number is not usable in a finance report. The design question is how to get the drafting speed without trusting the model with the numbers.

## Data

- **Live:** European Central Bank euro reference rates, daily, from the ECB Data Portal API (`fetch_rates.py`). Free and public. If the ECB request fails, the Frankfurter API (a free public mirror of the same ECB rates, no key needed) is tried next, and the source actually used is shown in the app and in every report.
- **Offline sample:** `fetch_rates.py --sample` builds random-walk rates with planted shocks (ZAR and PLN on one day, a slow INR drift). Every row is labelled `SYNTHETIC SAMPLE - not market data`, and the label is carried into the report header.
- The currency basket in `config.py` is illustrative; it should be replaced with the currencies in the company's own exposure report.

## Method

1. **Python computes every number** (`monitor.py`): start and end rate, monthly change, the month's z-score against the previous 12 monthly changes, the largest daily move, and days whose move exceeds 3 standard deviations of the prior 60 business days. Volatility uses prior days only, so a shock cannot inflate its own baseline.
2. **The draft** (`commentary.py`) comes from an LLM through Groq's API when `GROQ_API_KEY` is set, with a prompt that forbids calculating, forecasting, explaining causes or advising on hedging. Without a key, a fixed template writes the paragraph so the pipeline always runs.
3. **Automated checks** run on the draft before anyone reads it (`commentary.py`, `claims.py`):
   - every number and date in the text must match a computed fact;
   - each percentage must belong to the currency it is attached to;
   - "strengthened", "rose", "fell" and similar words must match the sign of the move, from whichever side the sentence is written ("the euro rose against PLN" and "PLN fell against the euro" are both checked correctly);
   - stated counts ("against 5 of the 11 currencies") must match the real counts;
   - every currency flagged as unusual must be mentioned, by code or by name.
4. **The output** (`outputs/commentary_YYYY-MM.md`) holds the facts table, the draft, the check result with any problems listed, and a sign-off line for the reviewer.

## Validation rules

| Check | Catches |
|---|---|
| Number check | Invented, mis-copied or re-rounded figures |
| Attribution check | A real figure attached to the wrong currency ("PLN moved 6.25%" when 6.25% is ZAR's move) |
| Direction check | "The euro rose 8.26%" when it fell 8.26%; a wrong minus sign |
| Count check | "Strengthened against 6 of the 11" when it was 5; counts in words ("two") too |
| Date check | Dates not present in the facts |
| Coverage check | A flagged currency left out of the draft |
| Source label in header | Sample data presented as market data |

`test_checks.py` has 15 tests: six correct drafts that must pass (including the same facts written from the other currency's side, currency names instead of codes, neutral wording and correct counts) and nine wrong drafts that must fail (wrong number, invented date, missing currency, flipped direction from either side, wrong sign, figure attached to the wrong currency, wrong counts in digits and in words). `python commentary.py --inject-error` changes one figure in the draft to show the check failing it.

## Results

On the sample data for October 2026, the monitor flagged PLN as an unusual month (z = 2.0) and found the planted one-day shocks in ZAR (7.01%, z = 6.9) and PLN (2.94%, z = 6.2). The template draft passed both checks; the injected-error draft failed the number check. These results show the pipeline working, not market behaviour.

## Limitations

- **Live data path not yet run end to end**: both live sources (ECB and Frankfurter) were written against their published formats and tested offline with responses in those formats, but built in an environment without internet access. Run `python fetch_rates.py` once and confirm the row count before relying on it.
- **LLM path not yet tested here**: same reason. The checks are model-independent, so any LLM output still goes through them.
- The direction and count checks read common phrasings, not every possible sentence. Negations ("did not weaken"), unusual wording ("the zloty gave back its gains") or counts phrased in ways the rules do not list may not be checked. The prompt asks the LLM to write from the euro's side, which keeps drafts within the phrasings the checks read; the reviewer still reads the final text.
- ECB reference rates are one daily fixing at 14:15 CET, not intraday prices.

## Where a human signs off

The final paragraph. The checks decide whether a draft is worth reading; the reviewer decides whether it goes in the report.

## Success measures for a real pilot

Time from month-end to approved paragraph, share of drafts passing checks on first try, number of reviewer edits per draft, and errors found after sign-off (target: zero).

## Daily monitoring

`daily_check.py` checks the latest business day: any currency whose move exceeds 3 standard deviations of its previous 60 business days is flagged. Each run appends to `outputs/alerts_log.csv`; a day with a flag also writes `outputs/alerts/alert_YYYY-MM-DD.md` and, if configured, emails it.

```bash
python daily_check.py                               # live rates
python daily_check.py --sample --date 2026-10-16    # offline demo: flags ZAR (+7.01%, z 6.9) and PLN (+2.94%, z 6.2)
```

**Run it every weekday on Windows.** `run_daily_check.bat` runs the check from its own folder and logs to `outputs\daily_check.log`. Schedule it for 17:00, after the ECB publishes (around 16:00 CET):

```
schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:00 /TN "FX daily check" /TR "\"C:\path\to\02-fx-monitor-commentary\run_daily_check.bat\""
```

Replace the path with the folder's real path. Remove it later with `schtasks /Delete /TN "FX daily check"`. The computer must be on at that time.

**Optional email alerts.** Set four environment variables (for example with `setx`): `ALERT_SMTP_HOST`, `ALERT_SMTP_USER`, `ALERT_SMTP_PASSWORD`, `ALERT_TO`. The script uses port 587 with STARTTLS; for Gmail this needs an app password, not the normal one. Without these variables the alert is written to file only.

## Interactive demo

`streamlit run app.py` opens a four-tab app:

| Tab | What it shows |
|---|---|
| This month's moves | The euro's move against each currency (blue = strengthened, red = weakened), with unusual months and days marked; a daily rate chart with unusual days as dots; the facts table |
| Daily check | Pick any business day: z-score chart with the normal range shaded, flagged currencies listed, and a button that runs the same check as `daily_check.py` and writes the alert; alert history below |
| Draft and check | Step by step: facts computed in Python, the paragraph drafted (LLM or template), then each of the four checks ticking pass or fail |
| Try to break it | Six one-click tamperings (flip a direction, change a number, give a figure to the wrong currency, wrong count, invent a date, leave out a flagged currency) or your own edits; the checks respond live |
| Sign-off | Final edit, checks re-run, the reviewer confirms they read the direction words, then approves; saved to `outputs/approved_commentary_YYYY-MM.md` with name and time, with a download button |

The sidebar chooses the rates and the month:

- **Rates:** synthetic sample, live reference rates (ECB first, Frankfurter as backup, or either one forced), or an uploaded CSV with columns `date, currency, rate`. A **Refresh rates** button downloads again; the sidebar shows when the rates were downloaded, from which source, and the latest rate date.
- **Groq API key:** paste your key (free at console.groq.com) to switch drafting to the LLM. The key is kept in the browser session only: it is never written to a file or log and is gone when the app closes. **Test key** checks it against Groq. The model name can be changed under *LLM settings* if Groq retires the default. If a call fails, the app says why and falls back to the template.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py                    # interactive demo
python fetch_rates.py --sample          # or: python fetch_rates.py --start 2024-01-01  (live ECB)
python monitor.py                       # outputs/facts_YYYY-MM.json
python commentary.py                    # outputs/commentary_YYYY-MM.md
export GROQ_API_KEY=...                 # optional: LLM draft instead of template
python commentary.py --inject-error     # shows the number check failing a bad draft
python test_checks.py
```
