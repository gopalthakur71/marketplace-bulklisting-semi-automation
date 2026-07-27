import pytest

from src.myntra.attribute_entry import (user_filled_attributes, attribute_vocab,
                                        validate_submitted, AttributeValueError)
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME, _resolve
from src.myntra.template_reader import read_template

V13 = _resolve(DEFAULT_TEMPLATE_NAME, "templates/myntra")


def test_user_filled_attributes_reads_the_twelve_from_rules():
    cols = user_filled_attributes()
    assert len(cols) == 12
    assert cols[0] == "Prominent Colour"
    assert "Usage" in cols


def test_attribute_vocab_only_from_template_and_na_where_the_sheet_has_it():
    t = read_template(V13)
    vocab = attribute_vocab(t, user_filled_attributes())
    assert set(vocab) == set(user_filled_attributes())
    # exact sizes read off V13 (2026-07-27)
    assert len(vocab["Prominent Colour"]) == 53
    assert len(vocab["Usage"]) == 9
    assert len(vocab["Border"]) == 10
    # NA is offered only where Myntra actually lists it — never injected
    for col in ["Prominent Colour", "Second Prominent Colour", "Third Prominent Colour",
                "Blouse Fabric", "Type", "Ornamentation", "Usage"]:
        assert any(v.strip().upper() == "NA" for v in vocab[col]), col
    for col in ["Saree Fabric", "Border", "Pattern", "Print or Pattern Type", "Wash Care"]:
        assert not any(v.strip().upper() == "NA" for v in vocab[col]), col


def test_validate_submitted_blank_becomes_none():
    vocab = {"Border": ["Zari", "Solid"], "Pattern": ["Solid"]}
    out = validate_submitted({"Border": "", "Pattern": "   "}, vocab)
    assert out == {"Border": None, "Pattern": None}


def test_validate_submitted_passes_exact_vocab_values():
    vocab = {"Border": ["Zari", "Solid"]}
    assert validate_submitted({"Border": "Zari"}, vocab) == {"Border": "Zari"}


def test_validate_submitted_rejects_off_vocab_value():
    vocab = {"Border": ["Zari", "Solid"]}
    with pytest.raises(AttributeValueError) as exc:
        validate_submitted({"Border": "Salmon Pink"}, vocab)
    assert "Border" in str(exc.value)
    assert "Salmon Pink" in str(exc.value)


def test_validate_submitted_rejects_unknown_column():
    with pytest.raises(AttributeValueError):
        validate_submitted({"Nonexistent Column": "x"}, {"Border": ["Zari"]})
