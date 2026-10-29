"""Build the extraction JSON Schema from config/fields.yaml.

Every field is returned as {value, source_text, confidence} so reviewers can
see what the model read and how sure it was.

The schema is sent as a structured output (output_config.format), which
requires additionalProperties: false on every object and allows only a few
nullable fields per request. So nothing here is nullable: a field that is not
on the page comes back as "" (or [] for lists), and validate.val() turns that
into None.
"""

PARTY = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "marital_status": {"type": "string"},
        "spouse": {"type": "string"},
        "capacity": {"type": "string"},
    },
    "required": ["name", "marital_status", "spouse", "capacity"],
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


def build_schema(fields: list[dict]) -> dict:
    return {
        "type": "object",
        "properties": {f["name"]: field_schema(f) for f in fields},
        "required": [f["name"] for f in fields],
        "additionalProperties": False,
    }
