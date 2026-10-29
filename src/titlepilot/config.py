"""Load fields, rules and prompt from the config folder, with version hashes."""
import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .schema import build_schema


@dataclass
class Config:
    fields: list
    rules: list
    prompt: str
    schema: dict
    versions: dict


def _version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def load_config(config_dir="config") -> Config:
    d = Path(config_dir)
    fields_text = (d / "fields.yaml").read_text()
    rules_text = (d / "rules.yaml").read_text()
    prompt_text = (d / "prompt.md").read_text()

    fields = yaml.safe_load(fields_text)["fields"]
    rules = yaml.safe_load(rules_text)["rules"]
    definitions = "\n".join(
        f"- {f['name']} ({f.get('type', 'text')}): {f.get('description', '')}" for f in fields
    )
    return Config(
        fields=fields,
        rules=rules,
        prompt=prompt_text.replace("{{FIELD_DEFINITIONS}}", definitions),
        schema=build_schema(fields),
        versions={
            "fields": _version(fields_text),
            "rules": _version(rules_text),
            "prompt": _version(prompt_text),
        },
    )
