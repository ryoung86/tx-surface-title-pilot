"""Build the extraction JSON Schema from config/fields.yaml.

Every field is returned as {value, source_text, confidence} so reviewers can
see what the model read and how sure it was.

The schema is kept strict (additionalProperties: false, every property
required, nothing nullable) so replies are easy to check. A field that is not
on the page comes back as "" (or [] for lists), and validate.val() turns that
into None. Keeping this shape also leaves the door open to strict structured
outputs if a later schema is small enough to compile.
"""

PARTY = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "marital_status": {"type": "string"},
        "spouse": {"type": "string"},
        "capacity": {"type": "string"},
        "share": {"type": "string", "description": "This party's share of the interest conveyed, exactly as written "
                                                    "(e.g. '11.625%', 'an undivided one-half (1/2)'); '' if not stated"},
    },
    "required": ["name", "marital_status", "spouse", "capacity", "share"],
    "additionalProperties": False,
}

VALUE_TYPES = {
    "text": {"type": "string"},
    "date": {"type": "string", "description": "As written on the instrument"},
    "number": {"type": "string", "description": "As written, e.g. '160' or '160.25'"},
    "group": {"type": "string"},
    "list": {"type": "array", "items": {"type": "string"}},
    "list of parties": {"type": "array", "items": PARTY},
}


def field_schema(field: dict) -> dict:
    ftype = (field.get("type") or "text").strip().lower()
    return {
        "type": "object",
        "description": field.get("description", ""),
        "properties": {
            "value": VALUE_TYPES.get(ftype, VALUE_TYPES["text"]),
            "source_text": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["value", "source_text", "confidence"],
        "additionalProperties": False,
    }


PORE_SPACE_DEFINITION = (
    "Any language about pore space, subsurface or underground storage space, geologic storage, storage reservoirs "
    "or salt caverns, or carbon dioxide (CO2) injection, storage or sequestration rights: grants, reservations, "
    "exceptions, leases, easements or options. Verbatim. Empty if none. A plain mineral reservation "
    "('oil, gas and other minerals') is not pore space language: leave it in mineral_exception only")

TERM_DEFINITION = ("The term of a lease, easement or timber deed as written: primary term, renewals, extensions and options (e.g. 'thirty (30) years ... two renewal terms of five (5) years each'). Empty if none stated")

# Fields the code depends on, added to every schema whether or not the workbook lists them.
SYSTEM_FIELDS = [
    {"name": "document_kind", "type": "text",
     "description": "What this file is: 'recorded instrument' if it carries a county recording reference "
                    "(volume/page or instrument number); otherwise 'map or plat', 'tax or appraisal record', "
                    "or 'other supporting document'"},
    {"name": "pore_space", "type": "text", "description": PORE_SPACE_DEFINITION},
    {"name": "term", "type": "text", "description": TERM_DEFINITION},
]
RECORDED = "recorded instrument"


def all_fields(fields: list[dict]) -> list[dict]:
    names = {f["name"] for f in fields}
    return list(fields) + [f for f in SYSTEM_FIELDS if f["name"] not in names]


def build_schema(fields: list[dict]) -> dict:
    fields = all_fields(fields)
    return {
        "type": "object",
        "properties": {f["name"]: field_schema(f) for f in fields},
        "required": [f["name"] for f in fields],
        "additionalProperties": False,
    }
