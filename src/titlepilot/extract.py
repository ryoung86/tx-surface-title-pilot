"""Send an instrument's pages to Claude and get back schema-shaped JSON.

The schema is given to Claude in the prompt and every reply is checked against
it here. If the reply doesn't match, the problems are sent back once for a
corrected version. This avoids the size limits of strict structured outputs
(a schema with dozens of fields is too large to compile) and of forced
tool_choice (not supported by the 5.5 models), so the field list can keep
growing.

Mock mode reads `<document>.mock.json` beside each file, so the pipeline can be
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


class Extractor:
    def __init__(self, schema: dict, prompt: str, model: str = DEFAULT_MODEL, mock: bool = False):
        self.schema, self.prompt, self.model, self.mock = schema, prompt, model, mock
        self.instructions = (
            prompt
            + "\n\nReturn only one JSON object, with no other text before or after it, "
              "that matches this JSON Schema exactly:\n"
            + json.dumps(schema, separators=(",", ":"))
        )
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

    def extract(self, doc_path, page_paths) -> dict:
        if self.mock:
            mock = Path(str(doc_path) + ".mock.json")
            if not mock.exists():
                raise FileNotFoundError(f"mock mode: {mock.name} not found")
            record = json.loads(mock.read_text())
            record["_meta"] = {"model": "mock"}
            return record

        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(Path(p).read_bytes()).decode()}}
            for p in page_paths
        ]
        content.append({"type": "text", "text": self.instructions})
        messages = [{"role": "user", "content": content}]

        text, usage = self._call(messages)
        usages, repaired = [usage], False
        try:
            record = parse_json(text)
            errors = schema_errors(record, self.schema)
        except ExtractionError as e:
            record, errors = None, [str(e)]

        if errors:  # one correction round
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
