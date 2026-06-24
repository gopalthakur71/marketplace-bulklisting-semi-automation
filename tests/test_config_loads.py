import yaml


def test_column_map_has_core_fields():
    m = yaml.safe_load(open("config/column_map.yaml"))
    assert m["title"] == "vendorArticleName"
    assert m["sku"] == "vendorSkuCode"
    assert m["color"] == "Prominent Colour"
    assert m["fabric"] == "Saree Fabric"


def test_constants_and_specs():
    c = yaml.safe_load(open("config/constants.yaml"))
    assert c["articleType"] == "Sarees"
    assert c["Country Of Origin"] == "India"
    s = yaml.safe_load(open("config/image_specs.yaml"))
    assert s["quality"] == 90
    assert s["max_images"] == 7
