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
titlepilot run sample_data/TX-SAMPLE-001 --mock          # uses <file>.mock.json instead of the API
titlepilot score sample_data/answer_key
pytest
```

The sample tract is invented: a 1978 deed, a deed of trust and its release, an unreleased abstract of judgment found by name search, a 1994 deed with a mineral exception, and a pipeline easement. It should produce three flags (TS03, TS07, R13) and score 100% against its answer key.

## Day-to-day workflow

1. **Update fields and rules.** Landmen edit `docs/TX_Surface_Fields_and_Rules.xlsx`. The pilot lead sets Status to Approved, then runs:
   `titlepilot import-config docs/TX_Surface_Fields_and_Rules.xlsx`
   Only Starter and Approved rows are imported. `enabled` and `params` in `rules.yaml` are kept across imports.
2. **Receive a package.** One folder per tract, named with the tract ID, holding the documents plus the agent's intake workbook (Intake Form and Document List sheets).
3. **Run.** `titlepilot run path/to/TX-REEVES-014` (or `run-all path/to/parent`). The package check runs first; problems go back to the agent unless `--force` is used.
4. **Review.** Open `output/<tract>/run_<n>/<tract>_review.xlsx`: Summary, Chain, Encumbrances and Flags. This stands in for the review screen during the pilot.
5. **Score (pilot only).** Put the verified results in an answer key folder and run `titlepilot score path/to/answer_key`.

## Layout

```
config/          fields.yaml, rules.yaml (generated from the workbook), prompt.md (edit directly)
docs/            Fields and Rules workbook and intake template
src/titlepilot/
  intake.py        read the intake workbook, package completeness check
  pages.py         PDF/TIFF/image -> PNG pages
  extract.py       Claude API call with the schema as a forced tool; mock mode
  schema.py        JSON Schema built from fields.yaml ({value, source_text, confidence} per field)
  validate.py      schema errors, normalized dates, references, names
  chain.py         ordering, current owner, encumbrances open/released
  rules.py         rules engine; one Python check per rule ID
  db.py            SQLite run history, append-only by trigger
  export.py        review workbook
  score.py         compare against an answer key
  pipeline.py      one tract end to end
  cli.py           commands
tests/           end-to-end tests on the synthetic tract
```

## Status of the rules

Implemented: R01, R06, R12, R13, TS03, TS06, TS07, PK01 (owner in the chain not name-searched, a code-only rule).

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
