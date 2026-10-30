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
import re
import time
from pathlib import Path

from .validate import schema_errors

DEFAULT_MODEL = os.environ.get("TITLEPILOT_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = 16000             # one recorded instrument
RUNSHEET_MAX_TOKENS = 32000    # one runsheet page can list many instruments
RUNSHEET_ROWS_PER_CHUNK = 120  # spreadsheet/CSV runsheets are sent in chunks of this many rows

RUNSHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "runsheet_kind": {"type": "string", "enum": ["tract", "name"],
                          "description": "tract: instruments affecting one tract of land; "
                                         "name: everything recorded under one or more people's names"},
        "owners_searched": {"type": "array", "items": {"type": "string"}},
        "subject_acreage": {"type": "string", "description": "Acreage of the tract the runsheet covers, if shown"},
        "subject_description": {"type": "string", "description": "Tract description at the top, if shown"},
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
                    "legal": {"type": "string", "description": "Legal description or survey/abstract, as shown"},
                    "agent_note": {"type": "string", "description":
                                   "The agent's own mark or note on whether the entry pertains to the tract, as "
                                   "shown (e.g. a C/X/* column, 'not subject property', 'pertains, not included'); "
                                   "'' if none"},
                },
                "required": ["recording_ref", "instrument_type", "filing_date", "grantor", "grantee", "legal",
                             "agent_note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["runsheet_kind", "owners_searched", "subject_acreage", "subject_description", "entries"],
    "additionalProperties": False,
}

RUNSHEET_PROMPT = """This is a title agent's runsheet (document index) for a tract of land in Texas.

- In entries, list every recorded instrument it shows, one per line item, copying the recording reference
  (volume/page or instrument number), type, filing date, grantor, grantee and legal description (survey,
  abstract, acres) as written. Use "" for anything not shown.
  Keep each value short: the first grantor and first grantee only, no "et al" lists or legal descriptions.
- In owners_searched, list the names the runsheet says were searched by name (for liens, judgments and the like).
  Use an empty list if it doesn't say. Do not guess from the grantor and grantee columns.
- Do not add instruments that aren't on the runsheet.
- agent_note: if the agent marked whether an entry pertains (a Copied column such as C = copied, X = not
  pertaining, * = pertains but not included, or notes such as "not subject property" or "instrument pertains,
  but was not included"), write what the mark means in words, e.g. "copied", "not pertaining",
  "not subject property", "pertains, not included". Use the runsheet's own legend if it has one. Else "".
- runsheet_kind: "tract" if it lists the chain for one piece of land; "name" if it lists what was found by
  searching one or more people's names (then put those names in owners_searched).
- subject_acreage and subject_description: the tract's acreage and description if the runsheet shows them, else ""."""


class ExtractionError(RuntimeError):
    pass


def salvage_runsheet(text: str) -> dict:
    """Recover what a cut-off runsheet reply contained: every complete entry, plus the small
    fields that come before the entries."""
    out = {"runsheet_kind": "tract", "owners_searched": [], "subject_acreage": "", "subject_description": "",
           "entries": []}
    m = re.search(r'"runsheet_kind"\s*:\s*"(tract|name)"', text)
    if m:
        out["runsheet_kind"] = m.group(1)
    m = re.search(r'"owners_searched"\s*:\s*(\[[^\]]*\])', text)
    if m:
        try:
            out["owners_searched"] = [str(x) for x in json.loads(m.group(1))]
        except json.JSONDecodeError:
            pass
    for key in ("subject_acreage", "subject_description"):
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            out[key] = m.group(1)
    start = text.find('"entries"')
    for m in re.finditer(r"\{[^{}]*\}", text[start:] if start >= 0 else ""):
        try:
            e = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict) and e.get("recording_ref"):
            out["entries"].append({k: str(e.get(k, "")) for k in
                                   ("recording_ref", "instrument_type", "filing_date", "grantor", "grantee", "legal",
                                    "agent_note")})
    return out


def merge_runsheet_parts(parts: list[dict]) -> dict:
    """Combine page-by-page (or chunk-by-chunk) runsheet readings into one."""
    merged = {"runsheet_kind": "tract", "owners_searched": [], "subject_acreage": "", "subject_description": "",
              "entries": [], "_partial": []}
    kinds = [p.get("runsheet_kind") for p in parts if p.get("runsheet_kind")]
    if kinds:
        merged["runsheet_kind"] = max(set(kinds), key=kinds.count)
    seen = set()
    for p in parts:
        for o in p.get("owners_searched", []):
            if o and o not in merged["owners_searched"]:
                merged["owners_searched"].append(o)
        for key in ("subject_acreage", "subject_description"):
            merged[key] = merged[key] or p.get(key, "")
        for e in p.get("entries", []):
            sig = (e.get("recording_ref"), e.get("instrument_type"), e.get("grantor"), e.get("grantee"))
            if sig not in seen:
                seen.add(sig)
                merged["entries"].append(e)
        merged["_partial"] += p.get("_partial", [])
    return merged


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

    def _call(self, messages, max_tokens=MAX_TOKENS):
        """One API call, streamed so long replies don't time out. Returns (text, usage, stop_reason)."""
        last_error = None
        for attempt in range(3):
            try:
                with self.client.messages.stream(model=self.model, max_tokens=max_tokens, messages=messages) as stream:
                    resp = stream.get_final_message()
            except self._retryable as e:
                last_error = e
                time.sleep(2 ** attempt * 5)
                continue
            if resp.stop_reason == "refusal":
                raise ExtractionError("model declined to process this document")
            text = "".join(b.text for b in resp.content if b.type == "text")
            return text, resp.usage.model_dump(), resp.stop_reason
        raise ExtractionError(f"API call failed after 3 attempts: {last_error}")

    def _ask(self, content: list, schema: dict, instructions: str, max_tokens=MAX_TOKENS, salvage=None) -> dict:
        """Send content + instructions, check the JSON against schema, allow one correction round.
        salvage: optional function that recovers a usable result from a cut-off reply."""
        messages = [{"role": "user", "content": content + [{"type": "text", "text": instructions}]}]
        text, usage, stop = self._call(messages, max_tokens)
        usages, repaired = [usage], False
        if stop == "max_tokens":
            if salvage is None:
                raise ExtractionError("reply was cut off (max_tokens); the document may need splitting")
            record = salvage(text)
            record["_meta"] = {"model": self.model, "usage": usages, "repaired": False, "truncated": True}
            return record
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
            text, usage, stop = self._call(messages, max_tokens)
            usages.append(usage)
            if stop == "max_tokens" and salvage is not None:
                record = salvage(text)
                record["_meta"] = {"model": self.model, "usage": usages, "repaired": True, "truncated": True}
                return record
            if stop == "max_tokens":
                raise ExtractionError("reply was cut off (max_tokens); the document may need splitting")
            record, repaired = parse_json(text), True

        record["_meta"] = {"model": self.model, "usage": usages, "repaired": repaired}
        return record

    def extract(self, doc_path, page_paths) -> dict:
        """One recorded instrument -> record matching the extraction schema."""
        if self.mock:
            return _mock(doc_path)
        return self._ask(_image_blocks(page_paths), self.schema, self.instructions)

    def read_runsheet(self, path, pages_dir) -> dict:
        """The agent's runsheet -> {runsheet_kind, owners_searched, subject_*, entries: [...]}.

        Read one page (or one chunk of spreadsheet rows) at a time and merged, so a long name
        runsheet can't overflow a single reply. A page that is still cut off keeps every complete
        entry, and is listed in '_partial' so the run can say so."""
        from .intake import runsheet_rows
        from .pages import to_pages

        path = Path(path)
        if self.mock:
            return _mock(path)
        instructions = _with_schema(RUNSHEET_PROMPT, RUNSHEET_SCHEMA)
        if path.suffix.lower() in (".xlsx", ".csv"):
            rows = runsheet_rows(path)
            header, body = rows[:8], rows[8:]          # titles and column headings repeat on every chunk
            chunks = [body[i:i + RUNSHEET_ROWS_PER_CHUNK] for i in range(0, len(body), RUNSHEET_ROWS_PER_CHUNK)] or [[]]
            contents = [[{"type": "text", "text": f"Runsheet contents (part {n} of {len(chunks)}):\n"
                                                  + "\n".join(header + c)}] for n, c in enumerate(chunks, 1)]
        else:
            contents = [_image_blocks([p]) for p in to_pages(path, pages_dir)]

        parts = []
        for n, content in enumerate(contents, 1):
            part = self._ask(content, RUNSHEET_SCHEMA, instructions, RUNSHEET_MAX_TOKENS, salvage_runsheet)
            if part.get("_meta", {}).get("truncated"):
                part["_partial"] = [f"page/part {n}: reply was cut off; kept {len(part['entries'])} complete entries"]
            parts.append(part)
        return merge_runsheet_parts(parts)
