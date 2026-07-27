import yaml


def test_column_map_has_core_fields():
    m = yaml.safe_load(open("config/myntra/column_map.yaml"))
    assert m["title"] == "vendorArticleName"
    assert m["sku"] == "vendorSkuCode"
    # color and fabric are NOT mapped here; they are derived by rules.
    assert "color" not in m
    assert "fabric" not in m


def test_constants_and_specs():
    c = yaml.safe_load(open("config/myntra/constants.yaml"))
    assert c["articleType"] == "Sarees"
    assert c["Country Of Origin"] == "India"
    assert c["brand"] == "Ijor"
    assert c["AgeGroup"] == "Adults-Women"
    assert c["Standard Size"] == "Onesize"
    assert c["Year"] == "2026"
    s = yaml.safe_load(open("config/myntra/image_specs.yaml"))
    assert s["quality"] == 90
    assert s["max_images"] == 7


def test_rules_config():
    r = yaml.safe_load(open("config/myntra/rules.yaml"))
    assert "HSN" not in r["fabric_detection"]["cotton"]
    assert "HSN" not in r["fabric_detection"]["silk"]
    assert r["fabric_detection"]["silk"]["Saree Fabric"] == "Pure Silk"
    # Colour/fabric/etc. are no longer auto-derived; the user fills them in Excel.
    assert "prominent_colour_from_name" not in r
    assert "colour_scan_exclude" not in r
    assert "colour_synonyms" not in r
    assert "brand_colour_remarks_from_prominent" not in r
    for header in ["Prominent Colour", "Saree Fabric", "Blouse Fabric", "Type",
                   "Ornamentation", "Border", "Pattern", "Print or Pattern Type",
                   "Wash Care"]:
        assert header in r["user_filled_attributes"]


def test_user_filled_attributes_are_the_twelve_with_v13_vocab():
    import yaml
    from src.myntra.template_reader import read_template
    from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME, _resolve

    rules = yaml.safe_load(open("config/myntra/rules.yaml", encoding="utf-8"))
    cols = rules["user_filled_attributes"]
    assert cols == [
        "Prominent Colour", "Second Prominent Colour", "Third Prominent Colour",
        "Saree Fabric", "Blouse Fabric", "Type", "Ornamentation", "Border",
        "Pattern", "Print or Pattern Type", "Wash Care", "Usage"]

    t = read_template(_resolve(DEFAULT_TEMPLATE_NAME, "templates/myntra"))
    for c in cols:
        assert c in t.col_index_by_header, f"{c} missing from the template header row"
        assert t.vocab_by_header.get(c), f"{c} has no dropdown vocabulary"
    # none of the 12 may also be a forced constant (that would fight the blanking)
    consts = yaml.safe_load(open("config/myntra/constants.yaml", encoding="utf-8"))
    assert not (set(cols) & set(consts)), "a user-filled column is also a constant"
