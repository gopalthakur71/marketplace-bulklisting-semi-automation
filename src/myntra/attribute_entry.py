"""The attributes the seller decides by hand (colour, fabric, type, border, ...).

The pipeline never guesses these; they are offered as dropdowns whose options come
strictly from the Myntra template's own vocabulary, and are written into the built
workbook only after an exact-membership check."""
import os

import yaml

CONFIG_DIR = os.path.join("config", "myntra")

# Used only if rules.yaml somehow lacks the key; the YAML is the source of truth.
FALLBACK_USER_FILLED = [
    "Prominent Colour", "Second Prominent Colour", "Third Prominent Colour",
    "Saree Fabric", "Blouse Fabric", "Type", "Ornamentation", "Border",
    "Pattern", "Print or Pattern Type", "Wash Care", "Usage"]


class AttributeValueError(Exception):
    """A submitted value is not an exact member of that column's Myntra vocabulary."""


def user_filled_attributes(config_dir=CONFIG_DIR):
    with open(os.path.join(config_dir, "rules.yaml"), encoding="utf-8") as fh:
        rules = yaml.safe_load(fh) or {}
    return rules.get("user_filled_attributes") or list(FALLBACK_USER_FILLED)


def attribute_vocab(template, columns):
    """{column: [accepted values]} straight from the template. Nothing is added."""
    return {c: list(template.vocab_by_header.get(c) or []) for c in columns}


def validate_submitted(values, vocab):
    """Blank -> None (clears the cell). Non-blank must be exactly in vocab, else raise."""
    out = {}
    for column, value in values.items():
        if column not in vocab:
            raise AttributeValueError(f"Unknown attribute column: {column}")
        if value is None or str(value).strip() == "":
            out[column] = None
            continue
        v = str(value).strip()
        if v not in vocab[column]:
            raise AttributeValueError(
                f"{column}: '{v}' is not one of Myntra's accepted values")
        out[column] = v
    return out
