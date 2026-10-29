"""Send an instrument's pages to Claude and get back schema-shaped JSON.

Mock mode reads `<document>.mock.json` beside each file, so the pipeline can be
built and tested before real documents or an API key are available.
"""
import base64
import json
import os
import time
from pathlib import Path

DEFAULT_MODEL = os.environ.get("TITLEPILOT_MODEL", "claude-sonnet-5-5")


class Extractor:
    def __init__(self, schema: dict, prompt: str, model: str = DEFAULT_MODEL, mock: bool = False):
        self.schema, self.prompt, self.model, self.mock = schema, prompt, model, mock
        self.client = None
        if not mock:
            import anthropic
            self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
            self._retryable = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)

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
        content.append({"type": "text", "text": self.prompt})

        last_error = None
        for attempt in range(3):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=8000,
                    tools=[{"name": "record_instrument",
                            "description": "Record the abstracted instrument",
                            "input_schema": self.schema}],
                    tool_choice={"type": "tool", "name": "record_instrument"},
                    messages=[{"role": "user", "content": content}],
                )
                record = next(b.input for b in resp.content if b.type == "tool_use")
                record["_meta"] = {"model": self.model, "usage": resp.usage.model_dump()}
                return record
            except self._retryable as e:
                last_error = e
                time.sleep(2 ** attempt * 5)
        raise RuntimeError(f"extraction failed after 3 attempts: {last_error}")
