import os

import yaml

from src.myntra.template_reader import read_template
from src.core.shopify_reader import read_products
from src.myntra.mapper import map_product
from src.core.images import process_images
from src.myntra.fill import fill_template
from src.myntra.report import write_report


def _resolve(name, subdir="input"):
    """Prefer <subdir>/<name>, else repo-root <name>."""
    cand = os.path.join(subdir, name)
    return cand if os.path.exists(cand) else name


def main(template_path=None, csv_path=None, out_dir="output", config_dir="config/myntra",
         fetch=None, upload=None, style_group_id_start=None, hsn_by_signature=None):
    template_path = template_path or _resolve(
        "Myntra-Sku-Template-2026-06-16.xlsx", "templates/myntra")
    csv_path = csv_path or _resolve("products_export.csv")

    column_map = yaml.safe_load(open(os.path.join(config_dir, "column_map.yaml")))
    constants = yaml.safe_load(open(os.path.join(config_dir, "constants.yaml")))
    specs = yaml.safe_load(open(os.path.join(config_dir, "image_specs.yaml")))
    rules = yaml.safe_load(open(os.path.join(config_dir, "rules.yaml")))

    template = read_template(template_path)
    products = read_products(csv_path)

    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # One decision drives both whether we upload images to S3 and whether the sheet
    # references S3 URLs. If we won't upload, don't write S3 URLs the images aren't
    # hosted at — fall back to the source CDN URL instead.
    do_upload = specs.get("s3_upload") if upload is None else upload
    use_s3 = bool(do_upload and specs.get("public_base_url"))
    if not use_s3:
        specs = {**specs, "public_base_url": None}

    rows = []
    for i, p in enumerate(products, start=1):
        mapped = map_product(p, template, column_map, constants, rules,
                             hsn_by_signature=hsn_by_signature)
        # Sequential styleGroupId (each product its own group), continuing from
        # the seller's existing catalog so ids don't collide with listed products.
        if rules.get("auto_style_group_id") and "styleGroupId" in template.col_index_by_header:
            start = (style_group_id_start if style_group_id_start is not None
                     else rules.get("style_group_id_start", 1))
            mapped.cells["styleGroupId"] = str(start + i - 1)
        if fetch is None:
            img = process_images(p, specs, images_dir)
        else:
            img = process_images(p, specs, images_dir, fetch=fetch)
        rows.append((mapped, img))

    filled_path = os.path.join(out_dir, "myntra_filled.xlsx")
    fill_template(template_path, template, rows, filled_path)

    report_path = os.path.join(out_dir, "report.txt")
    write_report(rows, report_path)

    # Upload exactly this run's validated JPGs (not the whole images dir, which may
    # still hold images from earlier batches) so the sheet's S3 URLs resolve.
    uploaded = 0
    if use_s3:
        from src.core.s3_upload import upload_images
        run_jpgs = [path for _, img in rows for path in img.passed]
        uploaded = len(upload_images(
            run_jpgs, specs["s3_bucket"], specs.get("s3_prefix", ""),
            base_dir=images_dir, region=specs.get("s3_region"),
        ))

    return {"filled": filled_path, "report": report_path,
            "products": len(products), "uploaded": uploaded}


def cli():
    res = main()
    print(f"Filled: {res['filled']}")
    print(f"Report: {res['report']}")
    print(f"Products: {res['products']}")
    print(f"Images uploaded to S3: {res['uploaded']}")
    return res


if __name__ == "__main__":
    cli()
