"""Tests for the draft checks. Run: python test_checks.py  (or python -m pytest -q)

Facts used below: the euro strengthened 4.22% against PLN (unusual month, plus a
2.94% day on 2026-10-16) and weakened 0.5% against USD.
"""
from commentary import run_checks

FACTS = {
    "month": "2026-10",
    "currencies": [
        {"currency": "PLN", "start_rate": 3.9849, "end_rate": 4.1529, "change_pct": 4.22,
         "monthly_z_score": 2.0, "unusual_month": True, "largest_daily_move_pct": 2.94,
         "largest_daily_move_date": "2026-10-16",
         "unusual_days": [{"date": "2026-10-16", "move_pct": 2.94, "z_score": 6.2}]},
        {"currency": "USD", "start_rate": 1.3398, "end_rate": 1.3331, "change_pct": -0.5,
         "monthly_z_score": -0.2, "unusual_month": False, "largest_daily_move_pct": 1.05,
         "largest_daily_move_date": "2026-10-01", "unusual_days": []},
    ],
}
GOOD = ("In October 2026 the euro strengthened 4.22% against PLN, with a 2.94% move on 2026-10-16. "
        "The euro weakened 0.5% against USD.")


def ok(text):
    return run_checks(text, FACTS) == []


# ---- correct drafts pass
def test_correct_draft_passes():
    assert ok(GOOD), run_checks(GOOD, FACTS)

def test_currency_point_of_view_passes():
    # same facts written from the other currency's side
    assert ok("PLN weakened 4.22% against the euro. USD rose 0.5% against the euro.")

def test_currency_names_pass():
    assert ok("The euro strengthened 4.22% against the Polish zloty and weakened 0.5% against the US dollar.")

def test_neutral_wording_passes():
    assert ok("PLN moved 4.22% over the month; USD moved 0.5%.")

def test_sign_dropped_is_allowed():
    assert ok("The euro weakened 0.5% against USD. The euro rose 4.22% against PLN.")

def test_correct_counts_pass():
    assert ok("The euro strengthened against 1 of the 2 monitored currencies and weakened against 1. "
              "PLN moved 4.22%.")


# ---- wrong numbers, dates, coverage
def test_wrong_number_fails():
    assert not ok("The euro rose 4.62% against PLN. USD moved 0.5%.")

def test_invented_date_fails():
    assert not ok("PLN moved 2.94% on 2026-10-17. USD moved 0.5%.")

def test_missing_flagged_currency_fails():
    assert not ok("The euro was broadly stable; it weakened 0.5% against USD.")


# ---- direction
def test_direction_flipped_fails():
    assert not ok("The euro weakened 4.22% against PLN. The euro weakened 0.5% against USD.")

def test_direction_flipped_from_currency_side_fails():
    assert not ok("PLN moved 4.22%. USD fell 0.5% against the euro.")

def test_wrong_explicit_sign_fails():
    assert not ok("PLN: -4.22% over the month. USD: -0.5%.")


# ---- attribution
def test_number_attached_to_wrong_currency_fails():
    # 2.94% is PLN's daily move, not USD's
    assert not ok("PLN moved 4.22%. USD moved 2.94% on 2026-10-16.")


# ---- counts
def test_wrong_up_count_fails():
    assert not ok("The euro strengthened against 2 of the 2 monitored currencies. PLN moved 4.22%.")

def test_wrong_total_fails():
    assert not ok("Of the 3 currencies monitored, PLN moved 4.22% and USD moved 0.5%.")

def test_count_in_words_checked():
    assert not ok("The euro strengthened against two currencies. PLN moved 4.22%.")


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok  ", name)
                passed += 1
            except AssertionError as e:
                print("FAIL", name, e)
    print(f"{passed} tests passed")
