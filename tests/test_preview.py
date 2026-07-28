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
    assert dd == ["Blue Banarasi sarees", "Solid saree with Solid Border border",
                  "Has Zari detail"]


def test_design_details_l1_joins_the_second_colour():
    # Ground truth, SKU 164SDE226RPPG (2026-07-28): Green + Gold was published as
    # "Green and Gold-Toned Khadi sarees" — Myntra names metallics "<colour>-Toned".
    attrs = {"Prominent Colour": "Green", "Second Prominent Colour": "Gold",
             "Type": "Khadi"}
    assert reconstruct_design_details(attrs)[0] == "Green and Gold-Toned Khadi sarees"


def test_design_details_l1_second_colour_non_metallic_is_plain():
    attrs = {"Prominent Colour": "Black", "Second Prominent Colour": "Yellow"}
    assert reconstruct_design_details(attrs)[0] == "Black and Yellow sarees"


def test_design_details_l1_metallic_first_colour_is_toned():
    assert reconstruct_design_details({"Prominent Colour": "Gold"})[0] == \
        "Gold-Toned sarees"


def test_design_details_l1_ignores_unset_second_colour():
    attrs = {"Prominent Colour": "Blue", "Second Prominent Colour": "NA"}
    assert reconstruct_design_details(attrs)[0] == "Blue sarees"


def test_reconstruct_design_details_minimal():
    attrs = {"Prominent Colour": "Red"}
    assert reconstruct_design_details(attrs) == ["Red sarees"]


def test_design_details_l2_only_border_single_space():
    # Pattern unset, Border set -> leading space stripped, no double space
    assert reconstruct_design_details({"Border": "Zari"}) == \
        ["saree with Zari Border border"]


def test_design_details_l2_without_border_drops_the_border_clause():
    # Border unset -> no border words at all, rather than a dangling "with Border"
    assert reconstruct_design_details({"Pattern": "Solid"}) == ["Solid saree"]


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


def test_build_card_shapes_one_card():
    from src.myntra.preview import build_card
    attrs = {"vendorSkuCode": "S1", "Prominent Colour": "Blue", "Type": "Banarasi",
             "Saree Fabric": "Pure Silk", "Border": "NA"}
    uf = ["Prominent Colour", "Type", "Saree Fabric", "Border"]
    card = build_card(attrs, uf)
    assert card["sku"] == "S1"
    assert card["title"] == "Pure Silk Banarasi Saree"
    assert card["design_details"][0] == "Blue Banarasi sarees"
    assert card["specs"] == [("Prominent Colour", "Blue"), ("Type", "Banarasi"),
                             ("Saree Fabric", "Pure Silk"), ("Border", "NA")]
    assert card["missing"] == ["Border"]          # NA counts as not filled


def test_build_card_falls_back_to_skucode():
    from src.myntra.preview import build_card
    assert build_card({"SKUCode": "S9"}, [])["sku"] == "S9"
    assert build_card({}, [])["sku"] == ""
