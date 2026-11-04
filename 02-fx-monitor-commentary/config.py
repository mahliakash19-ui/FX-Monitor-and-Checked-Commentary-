"""Settings in one place so a treasury user can change them without touching logic."""

# Currencies quoted against EUR. Illustrative basket: replace with the currencies
# in the company's own FX exposure report.
CURRENCIES = ["USD", "GBP", "CHF", "INR", "SEK", "PLN", "CAD", "AUD", "ZAR", "BRL", "MXN"]

# A daily move is "unusual" when its size exceeds this many standard deviations
# of the previous 60 business days.
DAILY_Z_THRESHOLD = 3.0
VOL_WINDOW_DAYS = 60

# A month is "unusual" for a currency when its move exceeds this many standard
# deviations of the previous 12 monthly moves.
MONTHLY_Z_THRESHOLD = 2.0

# Names the draft may use instead of the ISO code (checked the same way as the code)
CURRENCY_NAMES = {
    "USD": ["US dollar", "dollar"], "GBP": ["pound sterling", "sterling", "British pound", "pound"],
    "CHF": ["Swiss franc"], "INR": ["Indian rupee", "rupee"], "SEK": ["Swedish krona", "krona"],
    "PLN": ["Polish zloty", "zloty"], "CAD": ["Canadian dollar"], "AUD": ["Australian dollar"],
    "ZAR": ["South African rand", "rand"], "BRL": ["Brazilian real"], "MXN": ["Mexican peso", "peso"],
}

# LLM settings (Groq's OpenAI-compatible API). Without GROQ_API_KEY the tool
# writes a template commentary instead, so it always runs.
LLM_BASE_URL = "https://api.groq.com/openai/v1"
LLM_MODEL = "openai/gpt-oss-20b"

# Numbers in the draft may differ from the computed facts by at most this much
# after rounding to the precision used in the facts table.
NUMBER_TOLERANCE = 0.011
