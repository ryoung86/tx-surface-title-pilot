# Texas Surface Title Extraction Pilot

Turns a title agent's document package for one tract into a draft surface ownership and encumbrance report, a flag list for landman review, and a permanent run history.

Everything that will change as the pilot firms up lives in `config/`. The code does not need editing when fields or rules change in the workbook. It only needs a new check when a rule needs one.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                     # then add your Claude API key
```

The API key is only needed for live runs. Mock mode and the tests run without it.

## Try it with no real documents

```bash
titlepilot sample-tract sample_data                      # synthetic tract, intake workbook, answer key
                                                         # add --mode runsheet or --mode folder to try the others
titlepilot run sample_data/TX-SAMPLE-001 --mock          # uses <file>.mock.json instead of the API
titlepilot score sample_data/answer_key
pytest
```

The sample tract is invented: a 1978 deed, a deed of trust and its release, an unreleased abstract of judgment found by name search, a 1994 deed with a mineral exception, and a pipeline easement. It should produce three flags (TS03, TS07, R13) and score 100% against its answer key.

## Day-to-day workflow

1. **Update fields and rules.** Landmen edit `docs/TX_Surface_Fields_and_Rules.xlsx`. The pilot lead sets Status to Approved, then runs:
   `titlepilot import-config docs/TX_Surface_Fields_and_Rules.xlsx`
   Only Starter and Approved rows are imported. `enabled` and `params` in `rules.yaml` are kept across imports.
2. **Receive a package.** One folder per tract, named with the tract ID, holding the documents. See "What the title agent delivers" below.
3. **Run.** `titlepilot run path/to/TX-REEVES-014` (or `run-all path/to/parent`). With an intake workbook, the package check runs first and problems go back to the agent unless `--force` is used.
4. **Review.** Each run writes two workbooks to `output/<tract>/run_<n>/`:
   - `<tract>_title_report.xlsx`: the title report in SEG's format (Ownership, Ownership BT Data, Encumbrances, Flowchart, Names Examined, Index to All Documents, Runsheet), plus AI Review, AI Flags and AI Supporting Docs sheets at the end. It's a draft for a landman to check and sign.
   - `<tract>_review.xlsx`: the plainer working view (Summary, Chain, Encumbrances, Flags).
5. **Score (pilot only).** Put the verified results in an answer key folder and run `titlepilot score path/to/answer_key`.

## What the title agent delivers

The intake workbook is optional. Each tract folder is handled the first way that applies:

| Folder contains | Package source | Checks |
| --- | --- | --- |
| Intake workbook (Intake Form + Document List sheets) | intake form | Before extraction: every listed file uploaded, no extra files, every name search tied to a listed owner. After: PK01 |
| The agent's own runsheet or index (file name contains "runsheet" or "index"; PDF, image, .xlsx or .csv) | agent runsheet | Claude reads the runsheet, then after extraction: PK02 (on the runsheet, not uploaded), PK03 (uploaded, not on the runsheet), PK01 using the names the runsheet says were searched |
| Documents only | folder only | PK01 becomes one review flag listing every owner in the chain, so the landman confirms name searches were run |

In every case the tract ID is the folder name unless the intake form says otherwise. Documents can sit in any subfolders. Runsheets are never treated as recorded instruments, and files the AI identifies as maps, plats, appraisal cards or other supporting documents are listed separately and kept out of the chain.

A runsheet can be a **tract runsheet** (the chain for one piece of land) or a **name runsheet** (everything recorded under a person's name). Claude decides which. Only tract runsheets are used for PK03, and entries on a name runsheet that aren't in the package become review items rather than "not uploaded" errors.

## Tract list (tracts.csv)

Put a `tracts.csv` in the folder that holds the tract folders (`tracts\tracts.csv`), in a tract folder itself, or in the project folder. Each run prints which file it used:

```
tract_id,acreage,county,legal_description
CCS.TX.SDY.0143,4.6,Liberty,"4.60 acres, more or less, in the I & G N Railroad Company Survey #4, Abstract 289..."
```

The acreage matters: it is how the code tells an out-sale (a deed covering part of the tract, like a 1-acre sale out of a 5.6-acre deed) from a conveyance of the whole tract. Without it, the last conveyance is taken as vesting and OW01 asks the reviewer to confirm. Acreage can also come from the intake form's legal description or a tract runsheet.

## How the current owner is worked out

The current owner is the grantee of the latest conveyance that covers the whole tract and conveys the full interest. Conveyances after it that cover less land than the tract (out-sales) or convey an undivided fraction (for example "an undivided 5% of the surface") don't replace the owner; they raise OW01 so a landman confirms. Fractional ownership is not yet totaled (R07).

## The title report template

`src/titlepilot/templates/surface_title_report.xlsx` is a blank copy of a completed SEG report with the tract's data cleared. To refresh it from a newer report: `python tools/make_report_template.py "path/to/report.xlsx"`. The tracts.csv columns `aoi`, `researcher`, `researched_from` and `researched_to` fill those header fields when present. Not carried over: the Runsheet's header logo (it was the title vendor's) and the hand-drawn Flowchart shapes, which are replaced by a chain drawn in cells. On the Index and Runsheet, a red file date is earlier than the effective date.

## Layout

```
config/          fields.yaml, rules.yaml (generated from the workbook), prompt.md (edit directly)
docs/            Fields and Rules workbook and intake template
src/titlepilot/
  intake.py        intake workbook, agent runsheet or folder-only package; completeness check
  pages.py         PDF/TIFF/image -> PNG pages
  extract.py       Claude API call; reply checked against the schema, one correction round; mock mode
  schema.py        JSON Schema built from fields.yaml ({value, source_text, confidence} per field)
  validate.py      schema errors, normalized dates, references, names
  chain.py         ordering, current owner, encumbrances open/released
  rules.py         rules engine; one Python check per rule ID
  db.py            SQLite run history, append-only by trigger
  export.py        review workbook
  report.py        title report in SEG's format, from templates/surface_title_report.xlsx
  score.py         compare against an answer key
  pipeline.py      one tract end to end
  cli.py           commands
tests/           end-to-end tests on the synthetic tract
```

## Status of the rules

Implemented: R01, R06, R12, R13, TS03, TS06, TS07, plus code-only rules PK01 (owner with no record of a name search), PK02/PK03 (runsheet vs uploads) PK04 (same instrument uploaded twice, or a file name whose recording reference doesn't match the document), OW01 (current owner needs review), MP01 (needs mapping, below) and PS01 (pore space, below).

In config but no check yet (reported as "skipped" on every run): R07, R09, R10, R11, T01, T02, TS01, TS02, TS04, TS05. Add each as a `@rule("ID")` function in `rules.py` once the landmen settle the wording. Some need data the pilot will bring: TS02 needs the project route file, and T01 and T02 need decisions on how homestead and heirship facts are recorded.

## What to do once real documents arrive

- Add two worked examples to `config/prompt.md` from the development set.
- Check the instrument-type names the model returns and align `VESTING_TYPES` and `ENCUMBRANCE_TYPES` in `chain.py` and the type sets in `rules.py`.
- Add deskew and border cropping in `pages.py` if scans need it.
- Tune only on the development set; freeze `config/` before the test run. Every run records the config versions it used.

## Notes

- Model: `claude-sonnet-5-5` by default; override with `--model` or `TITLEPILOT_MODEL` (for example `claude-opus-5-5` for hard handwriting).
- The run history refuses updates and deletes. A re-run is a new run linked to the previous one.
- Keep document images and `runs.sqlite` on SEG-controlled storage. They are excluded from git by `.gitignore`.
- AI output is never a title opinion; a landman signs off every tract.


## Mapping notifications (MP01)

Every run checks for land that needs to be mapped:

- **Out-sales**: a conveyance after the vesting deed that covers less than the tract (for example a 1-acre sale out of a 4.6-acre tract), or the deed that created the tract out of a larger parent tract.
- **Metes and bounds**: a legal description with courses and distances ("THENCE N 45° 30' E 210 feet"), a point of beginning, field notes or the words "metes and bounds".
- **Exceptions**: a "LESS AND EXCEPT" or "SAVE AND EXCEPT" in the tract's legal description (tracts.csv or intake form). If the excepted instrument isn't in the package, that is listed too.

Each item shows up in four places:

1. An **MP01 flag** (severity "Mapping", purple) on the AI Flags sheet and in the review workbook.
2. An **AI Mapping** sheet in the title report (purple tab), with the instrument, reason, acres, parties, legal description and file, plus blank Mapped by / Date mapped / Note columns.
3. **output/mapping_queue.csv**, one list of open mapping work across all tracts. The mapping person sets `status` to `mapped` (or fills in `mapped_by`) when done; later runs of the same tract keep those rows and only refresh the open ones.
4. A **"MAPPING NEEDED"** block at the end of the run in the command window, and, if set up, an **email** and/or a **Teams or Slack message**.

### Email

Add the address (or several, separated by commas) to `.env`:

```
TITLEPILOT_MAPPING_EMAIL=mapping@segland.com
```

The email lists each item and attaches the title report. By default it is sent from the Outlook account signed in on the PC running titlepilot, so no password is stored. To send through a mail server instead, fill in the SMTP lines in `.env.example` (Microsoft 365: `smtp.office365.com`, port 587; your IT admin may need to allow SMTP sending for the account).

Check the setup without running a tract:

```
titlepilot test-email
titlepilot test-email someone@segland.com
```

If the email can't be sent, the run still finishes; the failure is printed and saved in the run history.

### Teams or Slack

To send a Teams message, create a workflow in Teams ("Post to a channel when a webhook request is received"), copy its URL and add it to `.env`:

```
TITLEPILOT_MAPPING_WEBHOOK=https://...the workflow URL...
```

For Slack, use an incoming webhook URL and also add `TITLEPILOT_WEBHOOK_FORMAT=slack`. Use `--no-notify` on a run to skip the message. A failed message is logged in the run history and does not stop the run.


## Pore space owner (PS01)

Texas default: the surface owner owns the pore space unless a conveyance says otherwise, and pore space is not a mineral, so a mineral reservation doesn't take it (*Myers-Woodward, LLC v. Underground Services Markham, LLC*, Tex. 2025; confirm with counsel).

Claude copies any pore space language into the `pore_space` field: pore space, subsurface or underground storage, geologic storage, salt caverns, CO2 injection, storage or sequestration. The code also searches the reservation, easement, restriction, notes and legal description text for the same words. Then:

- **No language**: the pore space owner is the surface owner, with the same vesting deed.
- **A deed reserves or excepts the pore space**: it stays with that deed's grantor and doesn't follow later surface deeds (severed).
- **A pore space deed** (a conveyance of the pore space alone): it goes to that grantee (severed).
- **A pore space, storage or sequestration lease, easement or option**: listed as an encumbrance; the owner doesn't change.
- **Any other mention**: flagged for a landman to read.

Every instrument with pore space language gets a PS01 flag (High), and so does a severance. The report's Pore Space Ownership section shows the pore space owner and vesting instrument, and the AI Review sheet gives the basis.


## Agent marks on name runsheets

Many name runsheets have a column showing what the agent decided about each entry (for example C = copied, X = not pertaining, * = pertains but not included, or notes like "not subject property"). Claude reads that mark into `agent_note`, and PK02 uses it:

- **Pertains, not included**: a High flag, "The agent marked this as pertaining to the tract but it is not in the package". Pull it.
- **Not pertaining / not subject property**: not flagged one by one; counted in a single line instead.
- An instrument that appears on several runsheets is only flagged once.

## Ordering and vesting

Instruments are put in filing order. One with no filing date read goes by the date it was signed; one with neither date goes last and is never taken as the vesting deed, so a late-recorded probate or affidavit can't be mistaken for the newest conveyance. Correction deeds ("Correction Deed", "Corrective ... Deed") count as conveyances.


## Misread numbers (NUM01)

Two checks catch a misread digit (a 7 read as a 1, a 17 read as 27) without a second reading:

- **Shares must add up.** Claude records each grantee's share as written. If the shares in one instrument don't total 100% of the interest conveyed, NUM01 flags it. (On Vol. 810, Page 400 a misread 7% → 1% made the shares total 94%.)
- **Words must match digits.** Deeds usually write numbers twice: "fifteen (15) years", "one twenty-fifth (1/25)", "three hundred seventy-one and eight/tenths (371.8) acres". If the words and digits disagree, NUM01 flags it. When the start of a number can't be seen, it is skipped rather than reported.

Tested on the 1,099 word/digit pairs in tract CCS.TX.SDY.0180: 10 flags, most of them real misreads (e.g. "seventeen (27)" where the page says 17). Low confidence on acreage or the interest conveyed is also flagged (R13).
