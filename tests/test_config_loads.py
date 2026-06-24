import yaml


def test_column_map_has_core_fields():
    m = yaml.safe_load(open("config/column_map.yaml"))
    assert m["title"] == "vendorArticleName"
    assert m["sku"] == "vendorSkuCode"
    assert m["fabric"] == "Saree Fabric"
    # color is intentionally NOT mapped here; Prominent Colour is derived by rules.
    assert "color" not in m


def test_constants_and_specs():
    c = yaml.safe_load(open("config/constants.yaml"))
    assert c["articleType"] == "Sarees"
    assert c["Country Of Origin"] == "India"
    assert c["brand"] == "Ijor Ethnic Partners"
    assert c["AgeGroup"] == "Adults-Women"
    assert c["Standard Size"] == "Onesize"
    assert c["Year"] == "2026"
    s = yaml.safe_load(open("config/image_specs.yaml"))
    assert s["quality"] == 90
    assert s["max_images"] == 7


def test_rules_config():
    r = yaml.safe_load(open("config/rules.yaml"))
    assert r["hsn_by_name_keyword"]["cotton"] == "52081120"
    assert r["prominent_colour_from_name"] is True
