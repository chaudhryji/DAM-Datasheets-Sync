"""How SharePoint values map to DAM metadata fields.

The DAM team renamed the datasheet fields on 19 Oct 2026 (dsitemcode -> productcode, ...).
Mapping confirmed on all 65,080 files with src/metadata_map_report.py:
every SharePoint value is already stored under the new key, so this only changes names
and a few value formats (language code, Yes/No, Letter).

SPO fields with no field in the new schema (item no, LCode, LMLID, type code, product group,
assetid) are no longer written or compared. Their old values stay on the files untouched.
"""
from datetime import datetime, timezone

# Old key (SharePoint view in the plan) -> DAM key, values copied as they are.
RENAMED = {
    "dsitemcode": "productcode",
    "dsitemdescription": "description",
    "dstemplate": "templatecode",
    "dsid": "sourcerecordid",
    "dscreated": "sourcecreated",
    "dsmodified": "sourcemodified",
    "dssubproductgroup": "productfamily",
}
# SharePoint LCode -> DAM language option.
LANGUAGE = {"ENU": "en", "NLD": "nl", "ESP": "es", "DEU": "de", "FRA": "fr", "CHS": "zh-Hans", "ITA": "it"}
# Only the 7 codes seen in the data; a new code shows up as unmapped in the s3 summary.
# SharePoint stores Proofread in the sheet's own language.
PROOFREAD = {**{v: "No" for v in ("no", "nee", "否", "non", "nein", "nei")},
             **{v: "Yes" for v in ("yes", "ja", "是", "sí", "si", "oui")}}
PAGEFORMAT = {"A4": "A4", "LTR": "Letter", "LETTER": "Letter", "A3": "A3"}

DATE_KEYS = {"sourcecreated", "sourcemodified"}
DAM_KEYS = ["productcode", "description", "language", "templatecode", "proofread", "sourcerecordid",
            "sourcecreated", "sourcemodified", "productfamily", "pageformat"]
# Fields the DAM team fills. We never overwrite them on existing files; a new upload can
# copy them from DAM files of the same item code (s4 --copy-dam-fields).
DAM_MANAGED = ["accesslevel", "assettype", "documenttype", "lifecyclestatus", "rightsstatus",
               "productcategories", "brand", "certification"]
DATE_TOLERANCE_S = 2 * 3600  # earlier loads shifted UTC by 1-2 h (CET/CEST); not a real change


def to_dam(expected: dict) -> tuple[dict, list]:
    """SharePoint values (ds* keys) -> DAM keys and values. Returns (values, unmapped) where
    unmapped lists 'key=value' for values that have no DAM option (those are not written)."""
    out, unmapped = {}, []
    for old, new in RENAMED.items():
        if expected.get(old):
            out[new] = expected[old]
    for old, new, table, raw in (("dslcode", "language", LANGUAGE, lambda v: v.upper()),
                                 ("dsproofread", "proofread", PROOFREAD, lambda v: v.casefold()),
                                 ("dspageformat", "pageformat", PAGEFORMAT, lambda v: v.upper())):
        v = expected.get(old)
        if v:
            if raw(v) in table:
                out[new] = table[raw(v)]
            else:
                unmapped.append(f"{old}={v}")
    return out, unmapped


def parse_dt(v: str):
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def same_value(k: str, expected: str, actual: str) -> bool:
    if k in DATE_KEYS:
        a, b = parse_dt(expected), parse_dt(actual)
        return bool(a and b and abs((a - b).total_seconds()) <= DATE_TOLERANCE_S)
    return expected == actual


def dam_value(k: str, value: str) -> str:
    """Format a value the way the DAM stores it."""
    if k in DATE_KEYS:
        return parse_dt(value).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return value
