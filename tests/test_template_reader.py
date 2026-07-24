from src.myntra.template_reader import read_template

TEMPLATE = "templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx"
V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def test_headers_and_data_row():
    t = read_template(TEMPLATE)
    assert t.headers[0] == "styleId"
    assert t.header_row == 3
    assert t.first_data_row == 4
    assert t.col_index_by_header["brand"] == 6
    assert t.col_index_by_header["Front Image"] == 74


def test_vocab_extracted_from_x14():
    t = read_template(TEMPLATE)
    occ = t.vocab_by_header["Occasion"]
    assert "Party" in occ and "Festive" in occ
    assert "India" in t.vocab_by_header["Country Of Origin"]
    assert t.vocab_by_header["articleType"] == ["Sarees"]


def test_vocab_extracted_from_plain_validations_v13():
    t = read_template(V13)
    assert t.headers[0] == "styleId"
    assert t.col_index_by_header["brand"] == 6
    # dropdowns resolved from the masterdata sheet via plain (non-x14) validations
    assert "Banarasi" in t.vocab_by_header["Type"]
    assert "Solid" in t.vocab_by_header["Border"]
    assert "Zari" in t.vocab_by_header["Ornamentation"]
    assert "Blue" in t.vocab_by_header["Prominent Colour"]
