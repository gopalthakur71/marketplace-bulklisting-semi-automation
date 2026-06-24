import os

import yaml

from src.template_reader import read_template
from src.shopify_reader import read_products
from src.mapper import map_product
from src.images import process_images
from src.fill import fill_template
from src.report import write_report


def _resolve(name):
    """Prefer input/<name>, else repo-root <name>."""
    cand = os.path.join("input", name)
    return cand if os.path.exists(cand) else name


def main(template_path=None, csv_path=None, out_dir="output", config_dir="config", fetch=None):
    template_path = template_path or _resolve("Myntra-Sku-Template-2026-06-16.xlsx")
    csv_path = csv_path or _resolve("products_export.csv")

    column_map = yaml.safe_load(open(os.path.join(config_dir, "column_map.yaml")))
    constants = yaml.safe_load(open(os.path.join(config_dir, "constants.yaml")))
    specs = yaml.safe_load(open(os.path.join(config_dir, "image_specs.yaml")))

    template = read_template(template_path)
    products = read_products(csv_path)

    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    rows = []
    for p in products:
        mapped = map_product(p, template, column_map, constants)
        if fetch is None:
            img = process_images(p, specs, images_dir)
        else:
            img = process_images(p, specs, images_dir, fetch=fetch)
        rows.append((mapped, img))

    filled_path = os.path.join(out_dir, "myntra_filled.xlsx")
    fill_template(template_path, template, rows, filled_path)

    report_path = os.path.join(out_dir, "report.txt")
    write_report(rows, report_path)

    return {"filled": filled_path, "report": report_path, "products": len(products)}


if __name__ == "__main__":
    res = main()
    print(f"Filled: {res['filled']}")
    print(f"Report: {res['report']}")
    print(f"Products: {res['products']}")
