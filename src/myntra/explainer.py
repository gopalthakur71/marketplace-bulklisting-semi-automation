from dataclasses import dataclass

from src.myntra.signature import normalize
from src.myntra.explanation_store import get as store_get, learn as store_learn
from src.myntra.gemini_client import explain as gemini_explain


@dataclass
class ExplainedIssue:
    sku: str | None
    style_id: str | None
    scope: str
    source_type: str
    raw_reason: str
    explanation: str
    action: str
    field: str | None
    category: str | None
    source: str          # yaml | plain | learned | gemini | raw
    cells: dict | None


def match_rule(message, rules):
    """First YAML rule whose `match` substring is in the message, else None. Mirrors
    error_reader.classify() but reports a miss so the caller can fall through."""
    low = str(message or "").strip().lower()
    for rule in rules.get("rules", []):
        if str(rule["match"]).lower() in low:
            return {"category": rule["category"], "action": rule["action"],
                    "explanation": rule["explanation"], "field": rule.get("field")}
    return None


def _issue(item, explanation, action, source, field=None, category=None):
    return ExplainedIssue(
        sku=item.sku, style_id=item.style_id, scope=item.scope,
        source_type=item.source_type, raw_reason=item.raw_reason,
        explanation=explanation, action=action, field=field,
        category=category, source=source, cells=item.cells)


def explain_item(item, rules, store=None, gemini=None):
    """Turn one ErrorItem into an ExplainedIssue. Lookup order (spec §5):
    YAML rule (only source of auto-fix) -> plain pass-through for Listings Report ->
    learned store -> Gemini (explain-only, then learn) -> raw fallback."""
    raw = item.raw_reason
    m = match_rule(raw, rules)
    if m:
        return _issue(item, m["explanation"], m["action"], "yaml",
                      field=m["field"], category=m["category"])

    # Listings-Report reasons are already plain English -> never send to Gemini.
    if item.source_type == "listings_report":
        return _issue(item, raw, "explain_only", "plain")

    sig, _ = normalize(raw)
    if store is not None:
        entry = store_get(store, sig)
        if entry:
            return _issue(item, entry["explanation"], "explain_only", "learned",
                          category=entry.get("category"))

    if gemini and gemini.get("enabled"):
        text = gemini_explain(raw, api_key=gemini.get("api_key"),
                              model=gemini.get("model", "gemini-2.5-flash"),
                              client=gemini.get("client"))
        if text:
            if store is not None:
                store_learn(store, sig, text)
            return _issue(item, text, "explain_only", "gemini")

    return _issue(item, raw, "explain_only", "raw")
