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
