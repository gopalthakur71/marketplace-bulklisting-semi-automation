class TemplateIncompatibleError(Exception):
    """The active template is missing a header the pipeline writes to."""


# Headers the mapper/pipeline write directly (not via column_map/constants).
# Kept here so a template swap that renamed one is caught, per spec 3.5.
_PIPELINE_WRITTEN_HEADERS = frozenset({
    "Product Details", "SKUCode", "vendorArticleNumber", "productDisplayName",
    "MRP", "ISP", "HSN", "styleGroupId",
    "Front Image", "Side Image", "Back Image", "Detail Angle",
    "Look Shot Image", "Additional Image 1", "Additional Image 2",
})


def assert_template_compatible(template, column_map, constants):
    """Raise if any header written by the column map, constants, or the pipeline
    itself is absent from the template's Sarees header row. Fail loud on a
    template swap, never silent."""
    expected = set(column_map.values()) | set(constants.keys()) | _PIPELINE_WRITTEN_HEADERS
    missing = sorted(h for h in expected if h not in template.col_index_by_header)
    if missing:
        raise TemplateIncompatibleError(
            "Template is missing expected headers: " + ", ".join(missing))
