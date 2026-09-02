"""
syncv2/retry.py - retry scheduling: exponential backoff with jitter and
temporary-vs-permanent error classification.
"""
import random
import re
import time

# Error families.
ERR_TEMPORARY = "temporary"      # timeout, DNS, connection, 5xx, Neon unavailable
ERR_PERMANENT = "permanent"      # schema/payload/business-constraint/impossible state

# Explicit temporary markers matched against normalized error text (never broad
# single characters or common words such as "server").
_TEMP_TOKENS = (
    "connection timed out", "timed out", "connection refused", "dns resolution",
    "dns", "network unreachable", "network is unreachable", "network is down",
    "network error",
    "service unavailable", "temporarily unavailable", "database unavailable",
    "broken pipe", "ssl", "tls", "connection reset", "remote host closed",
    "proxyerror", "5xx", "http 5", "http/1.1 5", "operationalerror: connection",
)


def _normalize_text(text):
    return re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())


def classify_error(exc) -> str:
    """Classify an exception into a retry family.

    Temporary matches only explicit, tokenised markers (timeouts, connection
    errors, DNS/network/unavailable, HTTP 5xx). An arbitrary digit or the word
    "server" inside a message is NEVER treated as temporary by itself.
    """
    name = type(exc).__name__.lower()
    text = _normalize_text(exc)
    if any(t in name or t in text for t in _TEMP_TOKENS):
        return ERR_TEMPORARY
    return ERR_PERMANENT


def is_permanent(exc) -> bool:
    return classify_error(exc) == ERR_PERMANENT


def next_retry_delay(attempt, base_seconds=60.0, cap_seconds=3600.0, jitter=0.3,
                     rng=None):
    """Exponential backoff with jitter for the given attempt number (0-based).

    delay = min(cap, base * 2**attempt) * (1 + uniform(-jitter, +jitter))
    """
    rng = rng or random
    exp = min(cap_seconds, base_seconds * (2 ** attempt))
    return max(0.0, round(exp * (1.0 + rng.uniform(-jitter, jitter)), 3))


def sleep_until_next_retry(delay):
    time.sleep(delay)
