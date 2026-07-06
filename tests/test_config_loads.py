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
    assert c["brand"] == "Ijor Ethnic Partners"
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
    assert r["prominent_colour_from_name"] is True
