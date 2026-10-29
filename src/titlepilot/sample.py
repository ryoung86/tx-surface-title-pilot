"""Create a synthetic tract (fake documents, intake workbook, mock AI output and an
answer key) so the pipeline can be built and tested before real documents exist.
Every name, recording number and date here is invented.
"""
import csv
import json
from pathlib import Path

import openpyxl
from PIL import Image, ImageDraw, ImageFont

TRACT = "TX-SAMPLE-001"
ALL_FIELDS = [
    "instrument_id", "instrument_type", "county", "volume", "page", "instrument_no", "execution_date",
    "filing_date", "grantors", "grantees", "legal_description_text", "survey_name", "abstract_no", "block",
    "section", "acreage", "less_and_except", "refers_to", "consideration", "notary_witnesses", "notes",
    "conveyance_type", "surface_interest", "mineral_exception", "homestead_indicators", "easements",
    "restrictions", "liens", "plat_reference", "possession_notes",
]
LIST_FIELDS = ["instrument_type", "conveyance_type", "grantors", "grantees", "refers_to", "easements", "liens"]
LEGAL = "160 acres, Section 22, Block 4, H&GN RR Co. Survey, Abstract 1234, Sample County, Texas"


def F(value, confidence="high", source=None):
    """Field in the structured-output shape: "" means not on the page."""
    if value is None:
        value = ""
    return {"value": value, "source_text": source if source is not None else (value if isinstance(value, str) else ""),
            "confidence": confidence}


def P(*names):
    return [{"name": n, "marital_status": "", "spouse": "", "capacity": "individual"} for n in names]


# file, found_by, owner_searched, record overrides
DOCS = [
    ("SAMPLE_1978-001001.pdf", "Tract search", None, {
        "instrument_id": F("OPR 1978-001001"), "instrument_type": F("Warranty deed"), "filing_date": F("04/12/1978"),
        "grantors": F(P("Mary Doe")), "grantees": F(P("John A. Smith")), "conveyance_type": F("warranty"),
        "surface_interest": F("fee simple"), "consideration": F("$10.00 and other good and valuable consideration")}),
    ("SAMPLE_1980-000123.pdf", "Tract search", None, {
        "instrument_id": F("OPR 1980-000123"), "instrument_type": F("Deed of trust"), "filing_date": F("02/01/1980"),
        "grantors": F(P("John A. Smith")), "grantees": F(P("First State Bank")),
        "liens": F(["Deed of trust, First State Bank, $40,000"])}),
    ("SAMPLE_2004-002210.pdf", "Tract search", None, {
        "instrument_id": F("OPR 2004-002210"), "instrument_type": F("Release"), "filing_date": F("05/10/2004"),
        "grantors": F(P("First State Bank")), "grantees": F(P("John A. Smith")), "refers_to": F(["OPR 1980-000123"])}),
    ("SAMPLE_2001-004410.pdf", "Name search", "John A. Smith", {
        "instrument_id": F("OPR 2001-004410"), "instrument_type": F("Abstract of judgment"), "filing_date": F("06/02/2001"),
        "grantors": F(P("Acme Supply Co.")), "grantees": F(P("John A. Smith")),
        "liens": F(["Judgment, Acme Supply Co., $12,480"])}),
    ("SAMPLE_1994-003300.pdf", "Tract search", None, {
        "instrument_id": F("OPR 1994-003300"), "instrument_type": F("Warranty deed"), "filing_date": F("08/15/1994"),
        "grantors": F(P("John A. Smith")), "grantees": F(P("Smith Ranch LLC")), "conveyance_type": F("warranty"),
        "surface_interest": F("fee simple"),
        "mineral_exception": F("SAVE AND EXCEPT all oil, gas and other minerals, previously reserved"),
        "legal_description_text": F(LEGAL, "low", "160 ac Sec 22 Blk 4 H&GN ... (smudged)")}),
    ("SAMPLE_2005-000777.pdf", "Tract search", None, {
        "instrument_id": F("OPR 2005-000777"), "instrument_type": F("Easement / ROW"), "filing_date": F("09/20/2005"),
        "grantors": F(P("Smith Ranch LLC")), "grantees": F(P("Pecos Pipeline Co.")),
        "easements": F(["Pipeline, Pecos Pipeline Co., 30 ft, centerline per Exhibit A plat, perpetual, assignable"])}),
]


def _record(overrides):
    rec = {f: F(None) for f in ALL_FIELDS}
    rec.update({"county": F("Sample"), "legal_description_text": F(LEGAL), "survey_name": F("H&GN RR Co."),
                "abstract_no": F("1234"), "block": F("4"), "section": F("22"), "acreage": F("160")})
    rec.update(overrides)
    for k in LIST_FIELDS:  # list-typed fields are arrays in the structured output
        v = rec[k]["value"]
        rec[k] = {**rec[k], "value": [] if v == "" else ([v] if isinstance(v, str) else v)}
    return rec


def _pdf(path, rec):
    lines = ["SYNTHETIC TEST DOCUMENT - NOT A REAL RECORD", "",
             f"{'; '.join(rec['instrument_type']['value'])}   {rec['instrument_id']['value']}",
             f"Filed: {rec['filing_date']['value']}",
             "Grantor: " + ", ".join(p["name"] for p in rec["grantors"]["value"]),
             "Grantee: " + ", ".join(p["name"] for p in rec["grantees"]["value"]),
             f"Property: {LEGAL}"]
    img = Image.new("RGB", (1700, 2200), "white")
    try:
        font = ImageFont.load_default(size=32)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    ImageDraw.Draw(img).multiline_text((150, 150), "\n".join(lines), fill="black", font=font, spacing=14)
    img.save(path, "PDF", resolution=200)


def _intake(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Intake Form"
    ws.append(["Tract intake form"])
    ws.append([])
    ws.append(["Item", "Entry (agent fills in)", "Guidance"])
    for row in [
        ("Tract ID", TRACT), ("Project / client", "Sample pipeline project"), ("County", "Sample"),
        ("Legal description", LEGAL), ("Search period", "Sovereignty to 2026-10-15"),
        ("Owners searched by name (with ownership years)", "Mary Doe (to 1978); John A. Smith (1978-1994); Smith Ranch LLC (1994-present)"),
        ("Records searched", "OPR, deed records, judgment and lien indexes, probate"),
        ("Title agent (name and company)", "Test Agent, Sample Title Services"), ("Delivery date", "2026-10-29"),
        ("Number of documents uploaded", len(DOCS)), ("Agent notes", None)]:
        ws.append(list(row))
    dl = wb.create_sheet("Document List")
    dl.append(["File name", "Instrument type", "Volume/page or instrument no.", "Filing date", "Grantor", "Grantee",
               "Found by", "Owner name searched (name searches)", "Agent notes"])
    for name, found_by, owner, o in DOCS:
        dl.append([name, o["instrument_type"]["value"], o["instrument_id"]["value"], o["filing_date"]["value"],
                   o["grantors"]["value"][0]["name"], o["grantees"]["value"][0]["name"], found_by, owner, None])
    wb.save(path)


def make_sample_tract(dest) -> Path:
    """Creates <dest>/TX-SAMPLE-001/ and <dest>/answer_key/. Returns dest."""
    dest = Path(dest)
    tract = dest / TRACT
    tract.mkdir(parents=True, exist_ok=True)
    for name, _, _, overrides in DOCS:
        rec = _record(overrides)
        _pdf(tract / name, rec)
        (tract / f"{name}.mock.json").write_text(json.dumps(rec, indent=2))
    _intake(tract / f"{TRACT}_intake.xlsx")

    key = dest / "answer_key"
    key.mkdir(exist_ok=True)
    with (key / "ground_truth.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["tract_id", "current_owner"], [TRACT, "Smith Ranch LLC"]])
    with (key / "encumbrances.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["tract_id", "instrument_id", "status"],
                                 [TRACT, "OPR 1980-000123", "released"],
                                 [TRACT, "OPR 2001-004410", "open"],
                                 [TRACT, "OPR 2005-000777", "open"]])
    with (key / "known_issues.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["tract_id", "rule_id"], [TRACT, "TS03"], [TRACT, "TS07"]])
    return dest
