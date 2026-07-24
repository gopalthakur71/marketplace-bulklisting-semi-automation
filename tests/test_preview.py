from src.myntra.preview import (is_set, reconstruct_title,
                                reconstruct_design_details, missing_attributes,
                                read_filled_rows)


def test_is_set_treats_blank_and_na_as_unset():
    assert is_set("Blue")
    assert not is_set("")
    assert not is_set(None)
    assert not is_set("NA")
    assert not is_set(" na ")


def test_reconstruct_title_full_with_blouse_piece():
    attrs = {"Print or Pattern Type": "Floral", "Ornamentation": "Zari",
             "Saree Fabric": "Pure Silk", "Type": "Banarasi",
             "Blouse Fabric": "Pure Silk"}
    assert reconstruct_title(attrs) == \
        "Floral Zari Pure Silk Banarasi Saree With Unstitched Blouse Piece"


def test_reconstruct_title_skips_unset_and_na():
    attrs = {"Type": "NA", "Saree Fabric": "", "Ornamentation": None,
             "Blouse Fabric": ""}
    assert reconstruct_title(attrs) == "Saree"


def test_reconstruct_design_details_three_lines():
    attrs = {"Prominent Colour": "Blue", "Type": "Banarasi", "Pattern": "Solid",
             "Border": "Solid", "Ornamentation": "Zari"}
    dd = reconstruct_design_details(attrs)
    assert dd == ["Blue Banarasi sarees", "Solid saree with Solid Border",
                  "Has Zari detail"]


def test_reconstruct_design_details_minimal():
    attrs = {"Prominent Colour": "Red"}
    assert reconstruct_design_details(attrs) == ["Red sarees"]


def test_design_details_l2_only_border_single_space():
    # Pattern unset, Border set -> leading space stripped, no double space
    assert reconstruct_design_details({"Border": "Zari"}) == ["saree with Zari Border"]


def test_design_details_l2_only_pattern_collapses_double_space():
    # Border unset, Pattern set -> the "  Border" double space is collapsed
    assert reconstruct_design_details({"Pattern": "Solid"}) == ["Solid saree with Border"]


def test_missing_attributes_flags_blank_and_na():
    uf = ["Type", "Border", "Prominent Colour"]
    attrs = {"Type": "Banarasi", "Border": "NA", "Prominent Colour": ""}
    assert missing_attributes(attrs, uf) == ["Border", "Prominent Colour"]


def test_read_filled_rows_reads_by_header(tmp_path):
    from src.myntra.template_reader import read_template
    from src.myntra.fill import fill_template
    from src.core.models import MappedRow, ImageResult
    V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"
    t = read_template(V13)
    row = MappedRow(sku="S1", cells={"vendorSkuCode": "S1", "Type": "Banarasi",
                                     "Prominent Colour": "Blue"})
    out = tmp_path / "filled.xlsx"
    fill_template(V13, t, [(row, ImageResult(sku="S1"))], str(out))
    rows = read_filled_rows(str(out), t)
    assert len(rows) == 1
    assert rows[0]["vendorSkuCode"] == "S1"
    assert rows[0]["Type"] == "Banarasi"
    assert rows[0]["Prominent Colour"] == "Blue"
