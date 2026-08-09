from src.core.shopify_reader import read_products

# Synthetic fixture so the suite is self-contained (the real catalog is not in git).
CSV = "tests/fixtures/products_export.csv"


def test_groups_into_products():
    products = read_products(CSV)
    assert len(products) == 2
    p = next(x for x in products if x.handle == "test-cotton-saree-red")
    assert p.sku == "TST001"
    assert p.title == "Test Cotton Saree Red"
    assert p.price == 1200.0
    assert p.compare_at_price == 1500.0


def test_images_ordered_by_position():
    products = read_products(CSV)
    p = next(x for x in products if x.handle == "test-cotton-saree-red")
    assert len(p.images) >= 2
    assert "-1.webp" in p.images[0]
    assert all("http" in u for u in p.images)


def _write_csv(tmp_path, header_extra="", value_extra=""):
    """A one-product export. header_extra/value_extra append one trailing column."""
    p = tmp_path / "export.csv"
    p.write_text(
        "Handle,Title,Variant SKU,Image Src,Image Position" + header_extra + "\n"
        "h1,Product One,SKU1,https://cdn.example/a.webp,1" + value_extra + "\n",
        encoding="utf-8")
    return str(p)


def test_hsn_read_from_the_metafield_column(tmp_path):
    from src.core.shopify_reader import HSN_COL
    assert HSN_COL == "HSN Code (product.metafields.custom.hsn_code)"
    path = _write_csv(tmp_path, "," + HSN_COL, ",54075240")
    assert read_products(path)[0].hsn == "54075240"


def test_hsn_is_none_when_the_column_is_absent(tmp_path):
    # The older 61-column export has no HSN column at all; it must still read.
    assert read_products(_write_csv(tmp_path))[0].hsn is None


def test_hsn_is_returned_raw_not_normalised(tmp_path):
    # Two real products export as "52085990 " with a trailing space. The reader
    # hands the value over untouched; hsn_source.normalize owns the cleaning.
    from src.core.shopify_reader import HSN_COL
    path = _write_csv(tmp_path, "," + HSN_COL, ",52085990 ")
    assert read_products(path)[0].hsn == "52085990 "
