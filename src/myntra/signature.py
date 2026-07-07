import re

_URL = re.compile(r"https?://\S+")
# An alphanumeric token >= 6 chars containing BOTH a letter and a digit = a Myntra
# SKU/article code (e.g. 169SDE326SFSF). Pure-digit ids fall through to <NUM>;
# pure-letter words (getBrandCodeFromBrandName) are kept as-is.
_SKU = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b")
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def normalize(clause):
    """Return (signature, captured). Strip only obvious variable data (URLs, SKU
    codes, digit runs); keep every English word; lowercase; collapse whitespace."""
    text = str(clause or "")
    captured = {"URL": [], "SKU": [], "NUM": []}

    def _cap(tag):
        def _sub(m):
            captured[tag].append(m.group(0))
            return "<%s>" % tag
        return _sub

    text = _URL.sub(_cap("URL"), text)
    text = _SKU.sub(_cap("SKU"), text)
    text = _NUM.sub(_cap("NUM"), text)
    text = _WS.sub(" ", text).strip().lower()
    return text, captured
