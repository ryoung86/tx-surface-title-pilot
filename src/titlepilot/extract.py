"""Send documents to Claude and get back schema-shaped JSON.

The schema is given to Claude in the prompt and every reply is checked against
it here. If the reply doesn't match, the problems are sent back once for a
corrected version. This avoids the size limits of strict structured outputs
(a schema with dozens of fields is too large to compile) and of forced
tool_choice (not supported by the 5.5 models), so the field list can keep
growing.

Mock mode reads `<file>.mock.json` beside each file, so the pipeline can be
built and tested before real documents or an API key are available.
"""
import base64
import json
import os
import time
from pathlib import Path

from .validate import schema_errors

DEFAULT_MODEL = os.environ.get("TITLEPILOT_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = 16000

RUNSHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "recording_ref": {"type": "string", "description": "Volume/page or instrument number, as shown"},
                    "instrument_type": {"type": "string"},
                    "filing_date": {"type": "string"},
                    "grantor": {"type": "string"},
                    "grantee": {"type": "string"},
                },
                "required": ["recording_ref", "instrument_type", "filing_date", "grantor", "grantee"],
                "additionalProperties": False,
            },
        },
        "owners_searched": {"type": "array", "items": {"type": "string"}},
        "runsheet_kind": {"type": "string", "enum": ["tract", "name"],
                          "description": "tract: instruments affecting one tract of land; "
                                         "name: everything recorded under one or more people's names"},
        "subject_acreage": {"type": "string", "description": "Acreage of the tract the runsheet covers, if shown"},
        "subject_description": {"type": "string", "description": "Tract description at the top, if shown"},
    },
    "required": ["entries", "owners_searched", "runsheet_kind", "subject_acreage", "subject_description"],
    "additionalProperties": False,
}

RUNSHEET_PROMPT = """This is a title agent's runsheet (document index) for a tract of land in Texas.

- In entries, list every recorded instrument it shows, one per line item, copying the recording reference
  (volume/page or instrument number), type, filing date, grantor and grantee as written. Use "" for anything not shown.
- In owners_searched, list the names the runsheet says were searched by name (for liens, judgments and the like).
  Use an empty list if it doesn't say. Do not guess from the grantor and grantee columns.
- Do not add instruments that aren't on the runsheet.
- runsheet_kind: "tract" if it lists the chain for one piece of land; "name" if it lists what was found by
  searching one or more people's names (then put those names in owners_searched).
- subject_acreage and subject_description: the tract's acreage and description if the runsheet shows them, else ""."""


class ExtractionError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    """Pull the JSON object out of a reply, tolerating code fences or stray text around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ExtractionError("reply contained no JSON object")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ExtractionError(f"reply was not valid JSON: {e}") from e


def _with_schema(prompt: str, schema: dict) -> str:
    return (prompt + "\n\nReturn only one JSON object, with no other text before or after it, "
                     "that matches this JSON Schema exactly:\n" + json.dumps(schema, separators=(",", ":")))


def _image_blocks(page_paths):
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(Path(p).read_bytes()).decode()}}
            for p in page_paths]


def _mock(path) -> dict:
    mock = Path(str(path) + ".mock.json")
    if not mock.exists():
        raise FileNotFoundError(f"mock mode: {mock.name} not found")
    record = json.loads(mock.read_text())
    record["_meta"] = {"model": "mock"}
    return record


class Extractor:
    def __init__(self, schema: dict, prompt: str, model: str = DEFAULT_MODEL, mock: bool = False):
        self.schema, self.prompt, self.model, self.mock = schema, prompt, model, mock
        self.instructions = _with_schema(prompt, schema)
        self.client = None
        if not mock:
            import anthropic
            self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
            self._retryable = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)

    def _call(self, messages):
        last_error = None
        for attempt in range(3):
            try:
                resp = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, messages=messages)
            except self._retryable as e:
                last_error = e
                time.sleep(2 ** attempt * 5)
                continue
            if resp.stop_reason == "refusal":
                raise ExtractionError("model declined to process this document")
            if resp.stop_reason == "max_tokens":
                raise ExtractionError("reply was cut off (max_tokens); split the document or raise MAX_TOKENS")
            return "".join(b.text for b in resp.content if b.type == "text"), resp.usage.model_dump()
        raise ExtractionError(f"API call failed after 3 attempts: {last_error}")

    def _ask(self, content: list, schema: dict, instructions: str) -> dict:
        """Send content + instructions, check the JSON against schema, allow one correction round."""
        messages = [{"role": "user", "content": content + [{"type": "text", "text": instructions}]}]
        text, usage = self._call(messages)
        usages, repaired = [usage], False
        try:
            record = parse_json(text)
            errors = schema_errors(record, schema)
        except ExtractionError as e:
            record, errors = None, [str(e)]

        if errors:
            messages += [
                {"role": "assistant", "content": text or "(empty reply)"},
                {"role": "user", "content": "That reply does not match the schema:\n- "
                                            + "\n- ".join(errors[:25])
                                            + "\nReturn the complete corrected JSON object only."},
            ]
            text, usage = self._call(messages)
            usages.append(usage)
            record, repaired = parse_json(text), True

        record["_meta"] = {"model": self.model, "usage": usages, "repaired": repaired}
        return record

    def extract(self, doc_path, page_paths) -> dict:
        """One recorded instrument -> record matching the extraction schema."""
        if self.mock:
            return _mock(doc_path)
        return self._ask(_image_blocks(page_paths), self.schema, self.instructions)

    def read_runsheet(self, path, pages_dir) -> dict:
        """The agent's runsheet -> {entries: [...], owners_searched: [...]}."""
        from .intake import runsheet_text
        from .pages import to_pages

        path = Path(path)
        if self.mock:
            return _mock(path)
        if path.suffix.lower() in (".xlsx", ".csv"):
            content = [{"type": "text", "text": "Runsheet contents:\n" + runsheet_text(path)}]
        else:
            content = _image_blocks(to_pages(path, pages_dir))
        return self._ask(content, RUNSHEET_SCHEMA, _with_schema(RUNSHEET_PROMPT, RUNSHEET_SCHEMA))
