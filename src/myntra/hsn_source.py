"""The one definition of "a usable HSN".

Deliberately pure: it knows nothing about web requests, jobs, or Shopify, so the
reader, the build, and the attribute screen can all enforce one rule from one
place. Myntra wants an 8-digit code; anything else is a gap for the seller to
fill on the attribute screen, which is why an unusable value comes back as None
rather than raising mid-build."""
import re

_EIGHT_DIGITS = re.compile(r"\d{8}")


def normalize(raw):
    """The stripped 8-digit code, or None if there isn't one.

    Accepts a non-string (openpyxl returns HSN as an int, since it is in
    fill.NUMERIC_HEADERS) and blank/None alike."""
    if raw is None:
        return None
    value = str(raw).strip()
    return value if _EIGHT_DIGITS.fullmatch(value) else None
