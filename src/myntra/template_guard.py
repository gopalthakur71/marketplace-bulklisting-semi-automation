class TemplateIncompatibleError(Exception):
    """The active template is missing a header the pipeline writes to."""


def assert_template_compatible(template, column_map, constants):
    """Raise if any header written by the column map or constants is absent from
    the template's Sarees header row. Fail loud on a template swap, never silent."""
    expected = set(column_map.values()) | set(constants.keys())
    missing = sorted(h for h in expected if h not in template.col_index_by_header)
    if missing:
        raise TemplateIncompatibleError(
            "Template is missing expected headers: " + ", ".join(missing))
