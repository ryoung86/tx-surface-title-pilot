"""Checks from the broker's sheet (DR, LN, TS08-TS13, EA) and the calculations behind them."""
from datetime import date
from types import SimpleNamespace

from titlepilot.calc import bearing_degrees, closure, expiration, lien_figures, months_in
from titlepilot.chain import build_chain
from titlepilot.intake import DocRow
from titlepilot.rules import Ctx, run_rules
from titlepilot.validate import normalize


def F(v, confidence="high", source=None):
    return {"value": v, "source_text": source if source is not None else (v if isinstance(v, str) else ""),
            "confidence": confidence}


def P(name, capacity="individual", marital="a married man"):
    return {"name": name, "marital_status": marital, "spouse": "", "capacity": capacity, "share": ""}


def inst(file, iid, itype, filed, grantors, grantees, **fields):
    rec = {"instrument_id": F(iid), "instrument_type": F(itype), "filing_date": F(filed),
           "execution_date": F(fields.pop("executed", filed)), "grantors": F(grantors), "grantees": F(grantees),
           "document_kind": F("recorded instrument"),
           "legal_description_text": F(fields.pop("legal", "10 acres out of the J. Smith Survey, Abstract 12"))}
    for k, v in fields.items():
        if isinstance(v, dict):
            rec[k] = v
        else:
            rec[k] = F(v)
    n = normalize(rec, DocRow(file_name=file))
    n["pages"] = fields.get("_pages", 3)
    return n


def flags(instruments, rule_ids, runsheet=None, params=None, acres=None):
    chain = build_chain(instruments, acres)
    instruments = [i for i in instruments if i.get("recorded", True)]
    intake = SimpleNamespace(runsheet_entries=runsheet or [], owners=[], file_checks=[], legal_description=None)
    cfg = [{"id": r, "severity": "High", "params": (params or {}).get(r, {})} for r in rule_ids]
    out, skipped = run_rules(cfg, Ctx(instruments, chain, intake))
    assert not skipped
    return out


def ids(fl):
    return [f.rule_id for f in fl]


# ------------------------------------------------------------------------------------------ calculations

def test_periods_and_lien_balance():
    assert months_in("thirty (30) years") == 360
    assert months_in("365 days") == 12
    assert months_in("360 monthly installments") == 360
    assert months_in("a period of two years") == 24
    c = lien_figures("ONE HUNDRED THOUSAND AND NO/100 DOLLARS ($100,000.00)", "8.5% per annum", "30 years", None,
                     "2015-06-01", None, today=date(2026, 11, 5))
    assert c["maturity"] == "2045-06-01" and c["maturity_computed"] and not c["matured"]
    assert 85000 < c["balance"] < 87500
    old = lien_figures("$50,000", "6%", "10 years", None, "2005-03-01", None, today=date(2026, 11, 5))
    assert old["matured"] and old["balance"] == 0
    assert lien_figures("$50,000", "", "", None, "2005-03-01", None)["missing"] == ["interest rate", "term"]


def test_expiration():
    assert expiration("thirty (30) years", "", "", "2010-01-15") == ("2040-01-15", "calculated")
    assert expiration("so long as used for pipeline purposes", "", "", "2010-01-15")[1] == "perpetual"
    assert expiration("", "", "2031-12-31", "2010-01-15") == ("2031-12-31", "stated")


def test_bearings_and_closure():
    assert bearing_degrees("S 10 deg W") == 190
    assert round(bearing_degrees("N45-30-00W"), 2) == 314.5
    square = ["POB | | iron rod", "N 00°00'00\" E | 208.71 feet | rod", "S 89°59'59\" E | 208.71 ft | rod",
              "S 00°00'00\" W | 208.71 feet | rod", "N 89°59'59\" W | 208.71 feet | rod"]
    c = closure(square)
    assert c["misclosure_ft"] < 0.01 and abs(c["acres"] - 1.0) < 0.001
    off = closure(["N 0 E | 100 varas", "East | 100 vrs", "South | 100 vrs", "W | 110 vrs"])
    assert off["misclosure_ft"] > 20


# ---------------------------------------------------------------------------------------------- defects

def test_signatures_acknowledgment_and_names():
    deed = inst("a.pdf", "Vol. 10, Page 1", "Warranty Deed", "2001-05-01",
                [P("John A. Smith"), P("Mary Smith")], [P("Buyer LLC", marital="")],
                signatures=["John A. Smith | individual | signed", "Mary Smith | individual | NOT SIGNED"],
                acknowledgment=["Jon Smyth | individual | venue: | notary: Pat Roe | seal: no | commission expires: 1999-01-01 | 2001-04-30"])
    out = flags([deed], ["DR01", "DR02", "DR06"])
    msgs = " ".join(f.message for f in out)
    assert "Not signed by: Mary Smith" in msgs
    assert "no notary seal" in msgs and "venue blank" in msgs and "commission expired" in msgs
    assert "DR06" in ids(out)


def test_capacity_marital_alterations_pages():
    deed = inst("b.pdf", "Vol. 11, Page 2", "Executor's Deed", "2003-01-01",
                [P("Ann Lee", capacity="Independent Executor", marital=""), P("Bob Ray", marital="")],
                [P("C Corp", marital="")],
                signatures=["Ann Lee | individual | signed", "Bob Ray | individual | signed"],
                alterations="Acreage '10' struck and '12' written above, not initialed",
                page_completeness="Page 2 of 5 shown; Exhibit A referenced but not attached")
    out = flags([deed], ["DR04", "DR05", "DR07", "DR08"])
    assert set(ids(out)) == {"DR04", "DR05", "DR07", "DR08"}
    dr08 = next(f for f in out if f.rule_id == "DR08")
    assert "Bob Ray" in dr08.message and "Ann Lee" not in dr08.message   # executors sign in capacity


def test_descriptions_and_closure_rule():
    vague = inst("c.pdf", "Vol. 12, Page 3", "Warranty Deed", "1950-01-01", [P("A")], [P("B")],
                 legal="all of my land known as the home place")
    blanket = inst("d.pdf", "Vol. 12, Page 9", "Pipeline Easement", "1960-01-01", [P("B")], [P("Gas Co", marital="")],
                   legal="a right of way over and across the land of Grantor, the route to be selected by Grantee")
    bad = inst("e.pdf", "Vol. 13, Page 4", "Warranty Deed", "1970-01-01", [P("A")], [P("B")], acreage="40",
               metes_and_bounds_calls=["N 0 E | 100 varas", "East | 100 vrs", "South | 100 vrs", "W | 110 vrs"])
    out = flags([vague, blanket, bad], ["DR09", "DR10"])
    assert [f.instrument_id for f in out if f.rule_id == "DR09"] == ["Vol. 12, Page 3", "Vol. 12, Page 9"]
    assert "Blanket" in next(f.message for f in out if f.instrument_id == "Vol. 12, Page 9")
    assert any(f.rule_id == "DR10" and "don't close" in f.message for f in out)


def test_reservations_and_duhig():
    first = inst("f.pdf", "Vol. 20, Page 1", "Warranty Deed", "1940-01-01", [P("Old Owner")], [P("Middle Owner")],
                 mineral_exception="Grantor reserves an undivided one-half (1/2) of the minerals")
    second = inst("g.pdf", "Vol. 30, Page 1", "Warranty Deed", "1960-01-01", [P("Middle Owner")], [P("New Owner")],
                  mineral_exception="Grantor reserves an undivided one-half (1/2) of the minerals",
                  reserved_fractions=["one-half (1/2) | of the minerals"],
                  habendum_and_reservations_text="SAVE AND EXCEPT all prior reservations of record",
                  clause_conflicts="Grants 'all of said land' but reserves 1/2 of the surface in the habendum")
    out = flags([first, second], ["DR12", "DR13", "DR14"])
    assert {f.rule_id for f in out if f.instrument_id == "Vol. 30, Page 1"} == {"DR12", "DR13", "DR14"}


def test_liens_releases_and_recitals():
    deed = inst("h.pdf", "Vol. 40, Page 1", "Warranty Deed with Vendor's Lien", "2015-06-01", [P("Seller")],
                [P("Buyer")], consideration="$10 and a note of even date secured by a vendor's lien",
                recited_liens=["vendor's lien | First Bank | $100,000 | 2015-06-01"])
    dt = inst("i.pdf", "Vol. 40, Page 9", "Deed of Trust", "2015-06-01", [P("Buyer")], [P("First Bank", marital="")],
              executed="2015-06-01", lien_amount="($100,000.00)", interest_rate="8.5%", lien_term="30 years")
    old = inst("j.pdf", "Vol. 10, Page 9", "Deed of Trust", "1990-01-01", [P("Buyer")], [P("Old Bank", marital="")],
               executed="1990-01-01", lien_amount="$20,000", interest_rate="9%", lien_term="10 years")
    rel = inst("k.pdf", "Vol. 41, Page 1", "Release of Lien", "2001-01-01", [P("Other Lender", marital="")], [P("Buyer")],
               refers_to=["Vol. 10, Page 9"], released_lien="Other Lender, $25,000, dated 1990-01-01")
    nolien = inst("l.pdf", "Vol. 42, Page 1", "Warranty Deed", "2019-01-01", [P("Buyer")], [P("Next")],
                  recited_liens=["deed of trust | Some Bank | $50,000 | 2019-01-01"])
    out = flags([deed, dt, old, rel, nolien], ["DR15", "DR16", "DR17", "LN01", "LN02", "LN03"])
    got = {(f.rule_id, f.instrument_id) for f in out}
    assert ("LN01", "Vol. 40, Page 9") in got and "estimated balance" in next(
        f.message for f in out if f.rule_id == "LN01")
    assert ("DR15", "Vol. 42, Page 1") in got and ("DR15", "Vol. 40, Page 1") not in got   # its DT is in the package
    assert ("DR17", "Vol. 40, Page 1") in got
    dr16 = next(f.message for f in out if f.rule_id == "DR16")
    assert "Old Bank" in dr16 and "$25,000" in dr16
    assert not any(f.rule_id in ("LN01", "LN02") and f.instrument_id == "Vol. 10, Page 9" for f in out)  # released


def test_easement_terms():
    esmt = inst("m.pdf", "Vol. 50, Page 1", "Pipeline Easement", "2000-01-01", [P("Owner")], [P("Pipe Co", marital="")],
                executed="2000-01-01", legal="a 50 ft strip along the centerline described in Exhibit A",
                easement_exclusive=F("Exclusive", source="the exclusive right to use the Premises"),
                crossing_restrictions="No crossing without Grantee's prior written consent",
                crossing_control="Crossing agreement and indemnity required",
                assignment_restrictions="May not be assigned without Grantor's consent",
                preferential_rights="Right of first refusal on any sale of the tract",
                term="twenty (20) years", abandonment_period_months="24",
                abandonment_trigger="nonuse for two (2) consecutive years",
                abandonment_effect="terminates automatically")
    out = flags([esmt], ["TS08", "TS10", "TS11", "EA01", "EA02", "EA03", "EA04", "EA05"])
    assert set(ids(out)) == {"TS08", "TS10", "TS11", "EA01", "EA02", "EA03", "EA04", "EA05"}
    assert "expired 2020-01-01" in next(f.message for f in out if f.rule_id == "EA04")


def test_type_checks():
    fee = inst("n.pdf", "Vol. 60, Page 1", "Easement", "2000-01-01", [P("A")], [P("B")],
               operative_language="does grant, sell and convey unto Grantee")
    runsheet = [{"recording_ref": "Vol. 61, Page 1", "instrument_type": "DT", "_kind": "tract"}]
    deed = inst("o.pdf", "Vol. 61, Page 1", "Warranty Deed", "2001-01-01", [P("A")], [P("B")],
                operative_language="grant, sell and convey")
    unclear = inst("p.pdf", "Vol. 62, Page 1", "Agreement", "2002-01-01", [P("A")], [P("B")],
                   instrument_type=F("Agreement", "low"), operative_language="grant, bargain, sell and convey")
    out = flags([fee, deed, unclear], ["DR19"], runsheet=runsheet)
    got = {f.instrument_id for f in out}
    assert got == {"Vol. 60, Page 1", "Vol. 61, Page 1", "Vol. 62, Page 1"}
    chain = build_chain([fee, deed, unclear])
    assert any("Type unclear" in m for _, m in chain["ownership_notes"])


def test_quiet_when_fields_absent():
    """A deed read without the new fields raises none of the new flags."""
    deed = inst("q.pdf", "Vol. 70, Page 1", "Warranty Deed", "2001-01-01", [P("A")], [P("B")])
    rules = ["DR01", "DR02", "DR04", "DR05", "DR06", "DR07", "DR10", "DR11", "DR12", "DR13", "DR14", "DR15",
             "DR16", "DR17", "LN01", "LN02", "LN03", "TS08", "TS10", "TS11", "TS12", "TS13", "EA01", "EA02",
             "EA03", "EA04", "EA05"]
    assert flags([deed], rules) == []


def test_instructions_sent_as_cached_system_prompt(monkeypatch):
    import json
    from titlepilot.extract import Extractor
    seen = {}

    class Resp:
        stop_reason = "end_turn"
        content = [SimpleNamespace(type="text", text=json.dumps({"x": {"value": "", "source_text": "", "confidence": "high"}}))]
        usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 1})

    class Stream:
        def __init__(self, **kw):
            seen.update(kw)
        def __enter__(self):
            return SimpleNamespace(get_final_message=lambda: Resp())
        def __exit__(self, *a):
            return False

    ex = Extractor.__new__(Extractor)
    ex.model, ex.client, ex._retryable = "m", SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream(**kw))), ()
    schema = {"type": "object", "properties": {"x": {"type": "object"}}, "required": ["x"]}
    ex._ask([{"type": "text", "text": "page"}], schema, "INSTRUCTIONS")
    assert seen["system"][0]["text"] == "INSTRUCTIONS" and seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "INSTRUCTIONS" not in json.dumps(seen["messages"])



# --------------------------------------------------------------------------------------------- Phase 2

def _tract(cad_owner="SMITH RANCH LLC", current="Yes", owed="", ag="1-d-1 agricultural valuation",
           deed_addr="PO Box 12, Pecos, TX 79772", cad_addr="P.O. Box 12, Pecos, Texas 79772"):
    vest = inst("v.pdf", "Vol. 100, Page 1", "Warranty Deed", "1994-08-15", [P("John Smith")],
                [P("Smith Ranch LLC", marital="")], acreage="160",
                legal="160 acres, Section 22, Block 4, H&GN RR Co. Survey, Abstract 1234",
                grantee_mailing_address=deed_addr)
    out = inst("w.pdf", "Vol. 120, Page 5", "Warranty Deed", "2005-02-01", [P("Smith Ranch LLC", marital="")],
               [P("Neighbor", marital="single")], acreage="5.25")
    tax = inst("cad.pdf", "", "", "", [], [], document_kind="tax or appraisal record", cad_property_id="R12345",
               cad_owner_of_record=cad_owner + ", " + cad_addr, cad_mailing_address=cad_addr, taxes_current=current,
               last_tax_year_paid="2025", last_amount_paid="$1,234.56", delinquent_amount=owed,
               exemptions_special_valuation=ag, tax_check_source="Reeves CAD website, printed 2026-11-01")
    return [vest, out, tax]


def test_taxes_address_and_legal_description():
    docs = _tract()
    chain = build_chain(docs, 160)
    assert chain["tax"]["notice_address"] == "P.O. Box 12, Pecos, Texas 79772"
    ld = chain["legal_description"]
    assert ld["remaining_acreage"] == 154.75 and ld["status"] == "Preliminary"
    assert "PRELIMINARY" in ld["text"] and "Vol. 120, Page 5" in ld["text"] and "LEAVING A TOTAL of 154.75 acres" in ld["text"]
    out = flags(docs, ["TX01", "AD01", "LD01"], acres=160)
    sev = {(f.rule_id, f.severity) for f in out}
    assert ("TX01", "Note") in sev and ("LD01", "High") in sev        # LD01 takes the rule's severity
    assert not any(f.rule_id == "AD01" for f in out)                 # same address, written differently
    assert any("rollback" in f.message for f in out)


def test_tax_problems():
    docs = _tract(cad_owner="JONES FAMILY TRUST", current="No", owed="$3,210.00", ag="",
                  deed_addr="123 Main Street, Odessa, TX")
    out = flags(docs, ["TX01", "AD01"], acres=160)
    msgs = {(f.rule_id, f.severity): f.message for f in out}
    assert "delinquent" in msgs[("TX01", "High")] and "3,210.00" in msgs[("TX01", "High")]
    assert "JONES FAMILY TRUST" in msgs[("TX01", "Review")]
    assert "appraisal district address is used" in msgs[("AD01", "High")]


def test_no_tax_printout():
    vest = _tract()[0]
    out = flags([vest], ["TX01"])
    assert [(f.severity, "ask the agent" in f.message) for f in out] == [("Review", True)]
    assert build_chain([vest])["tax"]["notice_address"] is None
