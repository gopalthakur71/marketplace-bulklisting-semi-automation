import warnings
import zipfile

import openpyxl
import pytest

from src.core.models import MappedRow, ImageResult
from src.myntra.attribute_entry import (user_filled_attributes, attribute_vocab,
                                        validate_submitted, AttributeValueError,
                                        write_attributes, SkuMismatchError)
from src.myntra.fill import fill_template
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


def _built(tmp_path, skus=("S1", "S2")):
    """A freshly built workbook: identity columns filled, the 12 attrs blank."""
    warnings.filterwarnings("ignore")
    t = read_template(V13)
    rows = [(MappedRow(sku=s, cells={"vendorSkuCode": s, "brand": "Ijor"}),
             ImageResult(sku=s)) for s in skus]
    out = tmp_path / "myntra_filled.xlsx"
    fill_template(V13, t, rows, str(out))
    return t, str(out)


def _validation_count(path):
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path)
    n = len(wb["Sarees"].data_validations.dataValidation)
    wb.close()
    return n


def _sarees_xml(path):
    from src.myntra.fill import sheet_xml_name
    with zipfile.ZipFile(path) as z:
        return z.read(sheet_xml_name(path, "Sarees")).decode("utf-8")


def _cell(path, template, row_ordinal, header):
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path, data_only=True)
    v = wb["Sarees"].cell(row=template.first_data_row + row_ordinal,
                          column=template.col_index_by_header[header]).value
    wb.close()
    return v


def test_write_attributes_writes_the_right_row_and_leaves_others_blank(tmp_path):
    t, path = _built(tmp_path)
    n = write_attributes(path, t, [
        {"ordinal": 1, "sku": "S2",
         "values": {"Border": "Zari", "Type": "Banarasi", "Pattern": None}}])
    assert n == 1
    assert _cell(path, t, 1, "Border") == "Zari"
    assert _cell(path, t, 1, "Type") == "Banarasi"
    assert _cell(path, t, 1, "Pattern") is None       # explicit blank stays blank
    assert _cell(path, t, 0, "Border") is None        # the other SKU is untouched
    assert _cell(path, t, 1, "vendorSkuCode") == "S2"  # identity columns preserved


def test_write_attributes_is_idempotent_and_can_clear(tmp_path):
    t, path = _built(tmp_path)
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Solid"}}])
    assert _cell(path, t, 0, "Border") == "Solid"
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": None}}])
    assert _cell(path, t, 0, "Border") is None


def test_write_attributes_preserves_dropdowns(tmp_path):
    """KEY INVARIANT: the downloaded file must still have live Excel dropdowns."""
    t, path = _built(tmp_path)
    before = _validation_count(path)
    assert before > 0
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    assert _validation_count(path) == before


def test_write_attributes_keeps_strings_inline(tmp_path):
    """Myntra's parser cannot resolve shared strings; a bare openpyxl save undoes
    fill.py's inline conversion, so the save path must re-apply it."""
    t, path = _built(tmp_path)
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    xml = _sarees_xml(path)
    assert 't="s"' not in xml
    assert "Zari" in xml


def test_write_attributes_rejects_sku_mismatch_without_writing(tmp_path):
    t, path = _built(tmp_path)
    with pytest.raises(SkuMismatchError):
        write_attributes(path, t, [
            {"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}},
            {"ordinal": 1, "sku": "WRONG", "values": {"Border": "Solid"}}])
    assert _cell(path, t, 0, "Border") is None   # nothing written at all
