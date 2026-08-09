import pytest

from src.myntra.hsn_source import normalize


@pytest.mark.parametrize("raw, expected", [
    ("54075240", "54075240"),          # the common saree code
    ("52085990 ", "52085990"),         # two real products export a trailing space
    ("  52084121  ", "52084121"),      # leading space too
])
def test_usable_codes_are_stripped_and_returned(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", [
    None,                # column absent
    "",                  # metafield not filled
    "   ",               # whitespace only
    "5407",              # 4-digit chapter heading, not a full HSN
    "540752401",         # 9 digits
    "6211.42.90",        # punctuated
    "5407524a",          # letter
    "abc",               # stray text
])
def test_unusable_values_are_treated_as_missing(raw):
    assert normalize(raw) is None


def test_a_non_string_is_accepted_and_normalised():
    # openpyxl hands back HSN as an int, because it is in fill.NUMERIC_HEADERS.
    assert normalize(54075240) == "54075240"


def test_missing_is_none_not_an_exception():
    # A malformed code is a gap to fill on the attribute screen, never a crash
    # mid-build. This is why normalize returns rather than raises.
    assert normalize("nonsense") is None
