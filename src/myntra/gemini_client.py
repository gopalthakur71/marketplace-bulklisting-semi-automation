import logging

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

# Explain-only. The model must never propose or invent a fix or a value.
PROMPT = (
    "You are helping an online seller understand a Myntra catalog upload rejection. "
    "Explain the following rejection message in one or two plain-English sentences. "
    "Do NOT suggest, invent, or guess any fix, code, or value — only explain what it means.\n\n"
    "Rejection message: {text}"
)


def _default_client(api_key, model):
    """Build a thin callable around google-generativeai. Imported lazily so the
    dependency is optional and tests (which always inject a client) never load it."""
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    gm = genai.GenerativeModel(model)

    def _call(prompt):
        return gm.generate_content(prompt).text

    return _call


def explain(error_text, *, api_key=None, model=DEFAULT_MODEL, client=None, retries=1):
    """Return a plain-English explanation, or None on any failure. Sends ONLY the
    error text — never the product row (privacy, spec §5)."""
    if client is None:
        if not api_key:
            return None
        try:
            client = _default_client(api_key, model)
        except Exception as exc:
            _log.warning("Gemini client init failed: %s", exc)
            return None
    prompt = PROMPT.format(text=error_text)
    for attempt in range(retries + 1):
        try:
            out = client(prompt)
            out = (out or "").strip()
            return out or None
        except Exception as exc:
            _log.warning("Gemini explain failed (attempt %d): %s", attempt + 1, exc)
    return None
