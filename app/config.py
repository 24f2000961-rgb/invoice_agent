import os
# Public base URL of this deployment, e.g. https://your-host.example.com/a2a
# Must be exactly what you submit to the grader (no trailing slash, no query/fragment).
BASE_URL = os.environ.get("BASE_URL", "https://host/a2a").rstrip("/")
# Comma-separated list of accepted Bearer tokens. Each distinct token is treated
# as a distinct principal/user for isolation purposes.
_raw_tokens = os.environ.get("VALID_TOKENS", "")
VALID_TOKENS = {t.strip() for t in _raw_tokens.split(",") if t.strip()}
# AI Pipe (https://aipipe.org) — OpenAI-compatible proxy. Log in there to get a token.
# NOTE: using the /openai/v1 path + a bare OpenAI model name (no "openai/" prefix).
# The /openrouter/v1 path takes "<provider>/<model>" slugs instead and is on a
# separate (often credit-exhausted) backend — don't mix the two conventions.
AIPIPE_TOKEN = os.environ.get("AIPIPE_TOKEN", "")
AIPIPE_BASE_URL = os.environ.get("AIPIPE_BASE_URL", "https://aipipe.org/openai/v1")
AIPIPE_MODEL = os.environ.get("AIPIPE_MODEL", "gpt-5-nano")
DB_PATH = os.environ.get("DB_PATH", "invoice_agent.db")
MEDIA_BATCH = "application/vnd.ga5.invoice-claim-batch+json"
MEDIA_PROPOSALS = "application/vnd.ga5.invoice-action-proposals+json"
MEDIA_RECEIPTS = "application/vnd.ga5.invoice-action-receipts+json"
MEDIA_RESULTS = "application/vnd.ga5.invoice-action-results+json"
A2A_CONTENT_TYPE = "application/a2a+json"
A2A_VERSION = "1.0"
ACTIONS = {
    "settle_invoice",
    "request_approval",
    "hold_invoice",
    "reject_duplicate",
    "open_exception",
}
MAX_RESPONSE_BYTES = 512 * 1024
