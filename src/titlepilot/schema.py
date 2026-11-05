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

CLAUSE_CONFLICTS_DEFINITION = (
    "Any conflict between the granting clause, the habendum and the reservation or exception clauses (e.g. "
    "'grants all of the land' but later reserves an undivided fraction of the surface, or the habendum names a "
    "different estate), described in one or two sentences with the words quoted. Empty if the clauses agree")

# Fields the code depends on, added to every schema whether or not the workbook lists them.
SYSTEM_FIELDS = [
    {"name": "document_kind", "type": "text",
     "description": "What this file is: 'recorded instrument' if it carries a county recording reference "
                    "(volume/page or instrument number); otherwise 'map or plat', 'tax or appraisal record', "
                    "or 'other supporting document'"},
    {"name": "pore_space", "type": "text", "description": PORE_SPACE_DEFINITION},
    {"name": "term", "type": "text", "description": TERM_DEFINITION},
    {"name": "clause_conflicts", "type": "text", "group": "core", "description": CLAUSE_CONFLICTS_DEFINITION},
]

# Groups asked of every document; the others only of the documents they fit (GROUP_SCOPE), so a deed doesn't pay
# for forty empty lien and easement fields. When the type is unclear the model fills every group that might apply.
ALWAYS = {"", "core", "surface"}
GROUP_SCOPE = {
    "liens": "liens: deeds of trust, mortgages, vendor's liens, mechanic's and tax liens, judgments, UCC filings, "
             "and releases or partial releases of any of them",
    "easements & leases": "easements and rights-of-way (pipeline, road, utility, access), and surface, solar, wind, "
                          "ground, grazing, hunting, agricultural, CO2 storage and oil and gas leases, surface use, "
                          "damage and accommodation agreements",
}
# Worked out by the program, never asked of the model.
NOT_ASKED_GROUPS = {"tract output", "taxes"}
CALCULATED = {"estimated_remaining_term", "estimated_remaining_balance", "release_status", "release_reference",
              "remaining_acreage"}


def asked_fields(fields: list[dict]) -> list[dict]:
    """Fields sent to the model: all fields less the calculated ones and the groups the program fills itself."""
    return [f for f in all_fields(fields)
            if f["name"] not in CALCULATED and (f.get("group") or "").lower() not in NOT_ASKED_GROUPS]


def is_scoped(field: dict) -> bool:
    return (field.get("group") or "").lower() not in ALWAYS
RECORDED = "recorded instrument"


def all_fields(fields: list[dict]) -> list[dict]:
    names = {f["name"] for f in fields}
    return list(fields) + [f for f in SYSTEM_FIELDS if f["name"] not in names]


def build_schema(fields: list[dict]) -> dict:
    """Core and surface fields are required; the scoped groups (liens, easements & leases) may be left out
    when they don't fit the document."""
    fields = asked_fields(fields)
    return {
        "type": "object",
        "properties": {f["name"]: field_schema(f) for f in fields},
        "required": [f["name"] for f in fields if not is_scoped(f)],
        "additionalProperties": False,
    }


def field_definitions(fields: list[dict]) -> str:
    """The field list for the prompt, with each scoped group under its own heading."""
    fields = asked_fields(fields)
    lines = [f"- {f['name']} ({f.get('type', 'text')}): {f.get('description', '')}" for f in fields if not is_scoped(f)]
    groups = []
    for f in fields:
        g = (f.get("group") or "").lower()
        if is_scoped(f) and g not in groups:
            groups.append(g)
    for g in groups:
        scope = GROUP_SCOPE.get(g, g)
        lines.append(f"\n{g.upper()} fields: fill these only when the instrument is one of these ({scope}), or when "
                     "you are unsure whether it is. Leave the whole group out otherwise.")
        lines += [f"- {f['name']} ({f.get('type', 'text')}): {f.get('description', '')}"
                  for f in fields if (f.get("group") or "").lower() == g]
    return "\n".join(lines)
