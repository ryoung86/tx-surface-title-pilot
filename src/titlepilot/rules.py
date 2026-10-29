"""Rules engine. Each rule in config/rules.yaml runs the Python check registered
under its ID. Rules in the config with no check yet are reported as 'skipped',
so the list can grow in the workbook before the code catches up.

To add a check:

    @rule("TS04")
    def deed_restrictions(ctx, params):
        for i in ctx.instruments:
            if ...:
                yield i["instrument_id"], "message for the reviewer"
"""
from dataclasses import dataclass

from .fuzzy import token_sort_ratio

from .validate import as_text, conf, val

DEFAULT_CRITICAL = ["instrument_id", "instrument_type", "filing_date", "grantors", "grantees", "legal_description_text"]

REGISTRY = {}


@dataclass
class Flag:
    rule_id: str
    severity: str
    instrument_id: str | None
    message: str


@dataclass
class Ctx:
    instruments: list
    chain: dict
    intake: object


def rule(rule_id):
    def deco(fn):
        REGISTRY[rule_id] = fn
        return fn
    return deco


def _same(a: str, b: str, cutoff=88) -> bool:
    return token_sort_ratio(a.lower(), b.lower()) >= cutoff


def _released(ctx):
    return {r for i in ctx.instruments if i["category"] == "release" for r in i["refers_to"]}


@rule("R01")
def grantor_not_in_chain(ctx, params):
    seen = []
    for n, c in enumerate(ctx.chain["conveyances"]):
        if n:
            for g in c["grantors"]:
                if not any(_same(g, s) for s in seen):
                    yield c["instrument_id"], f"Grantor '{g}' does not appear as a grantee earlier in the chain"
        seen += c["grantees"]


@rule("R06")
def unreleased_lien(ctx, params):
    released = _released(ctx)
    for i in ctx.instruments:
        if i["category"] == "lien" and not i["ref_keys"] & released:
            yield i["instrument_id"], f"{i['type'].capitalize()} has no recorded release in the package"


@rule("R12")
def cites_missing_instrument(ctx, params):
    known = set().union(*(i["ref_keys"] for i in ctx.instruments)) if ctx.instruments else set()
    for i in ctx.instruments:
        for r in i["refers_to"]:
            if r not in known:
                yield i["instrument_id"], f"Cites {r}, which is not in the document package"


@rule("R13")
def low_confidence(ctx, params):
    for i in ctx.instruments:
        for f in params.get("fields", DEFAULT_CRITICAL):
            if conf(i["record"], f) == "low":
                yield i["instrument_id"], f"Low confidence on {f}"


@rule("TS03")
def unreleased_judgment(ctx, params):
    released = _released(ctx)
    for i in ctx.instruments:
        if i["category"] == "judgment" and not i["ref_keys"] & released:
            yield i["instrument_id"], f"{i['type'].capitalize()} has no recorded release in the package"


@rule("TS06")
def no_warranty_deed(ctx, params):
    for i in ctx.instruments:
        ct = (as_text(val(i["record"], "conveyance_type")) or "").lower()
        if "quitclaim" in i["type"] or "without warranty" in i["type"] or "quitclaim" in ct or "without warranty" in ct:
            yield i["instrument_id"], "Quitclaim or no-warranty deed in the chain"


@rule("TS07")
def mineral_exception(ctx, params):
    for i in ctx.instruments:
        v = as_text(val(i["record"], "mineral_exception"))
        if v:
            yield i["instrument_id"], f"Mineral exception or reservation: {str(v)[:150]}"


@rule("PK01")
def owner_not_name_searched(ctx, params):
    searched = ctx.intake.owners if ctx.intake else []
    for o in ctx.chain["owners"]:
        if not any(_same(o, s, 85) for s in searched):
            yield None, f"Owner '{o}' in the chain was not listed as name-searched on the intake form"


def run_rules(rules_cfg: list[dict], ctx: Ctx):
    flags, skipped = [], []
    for r in rules_cfg:
        if not r.get("enabled", True):
            continue
        check = REGISTRY.get(r["id"])
        if check is None:
            skipped.append(r["id"])
            continue
        for instrument_id, message in check(ctx, r.get("params") or {}):
            flags.append(Flag(r["id"], str(r.get("severity", "Review")), instrument_id, message))
    return flags, skipped
