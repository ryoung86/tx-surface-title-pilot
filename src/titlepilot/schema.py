"""Build the extraction JSON Schema from config/fields.yaml.

Every field is returned as {value, source_text, confidence} so reviewers can
see what the model read and how sure it was.
"""

PARTY = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "marital_status": {"type": ["string", "null"]},
        "spouse": {"type": ["string", "null"]},
        "capacity": {"type": ["string", "null"]},
    },
    "required": ["name"],
}

VALUE_TYPES = {
    "text": {"type": ["string", "null"]},
    "date": {"type": ["string", "null"]},
    "number": {"type": ["number", "null"]},
    "list": {"type": ["array", "null"], "items": {"type": "string"}},
    "list of parties": {"type": ["array", "null"], "items": PARTY},
    "group": {"type": ["object", "null"], "additionalProperties": {"type": ["string", "null"]}},
}


def field_schema(field: dict) -> dict:
    ftype = (field.get("type") or "text").strip().lower()
    value = VALUE_TYPES.get(ftype, VALUE_TYPES["text"])
    return {
        "type": "object",
        "description": field.get("description", ""),
        "properties": {
            "value": value,
            "source_text": {"type": ["string", "null"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["value", "confidence"],
    }


def build_schema(fields: list[dict]) -> dict:
    return {
        "type": "object",
        "properties": {f["name"]: field_schema(f) for f in fields},
        "required": [f["name"] for f in fields],
    }
