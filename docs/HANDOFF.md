# Handoff summary (2026-11-19)

Start here in a new session. Change history: docs/AUDIT_TRAIL.md. Open decisions: docs/BEFORE_GO_LIVE.md.

## Where it stands (0.12.0)

- Reads every document in a tract package through the Claude API and writes SEG's title report format plus
  AI review sheets.
- Runsheet checks (agent C/X marks), current owner, out-sales, pore space owner, mapping flags with email/Teams
  notices.
- Broker rules sheet: Phase 1 (fields by document type; document-defect, lien, easement and lease checks; lien
  balances, expiry and survey closure calculated in code) and Phase 2 (taxes and CAD from the agent's printouts,
  IOL/FOL notice address, preliminary legal description) are built. Phase 3 waits on project templates.
- Supabase indexing test built (supabase/schema.sql, `titlepilot index` / `search`, docs/SUPABASE_SETUP.md),
  then shelved.

## Results so far

- CCS.TX.SDY.0143: 100% vs SEG's verified title. CCS.TX.SDY.0180: about 98%. Both read in chat, before the
  broker rules.
- Phases 1 and 2 are not yet tested on a real tract.

## Current test tract: Brazos South Plant Option / Mallard Land Development

- Glasscock County. Section 21, Block 36, T3S, T&P Ry Co Survey, about 640 acres.
- 40 instruments, 37 name runsheets, 436 pages.
- Chain: Chesser heirs to A.N. Hendrickson (1937); T.M. Chesser to Louis A. Wrage (1950); Wrage/Hendrickson
  probates; Gunn; PJK Family LP to Mallard Land Development (SWD OPR 346/115, 2017); about 15 easements and
  surface agreements.
- SEG's final ("Surface Flowchart - Tomahawk - Mallard Land Development.xlsx") is held back for blind scoring.
- The chat read was stopped after about 50 pages because it used up the daily chat allowance.

## Next steps

1. Run Brazos through the API on the PC, with SEG's flowchart removed from the folder:
   `git pull`, then `titlepilot run "<tract folder>"`. Expected cost roughly $4-7.
2. Bring the report and review workbook, plus SEG's flowchart, into chat for scoring.
3. Tune noisy flags (likely DR08 marital status and EA03 easement width).
4. Run 8-10 test tracts of different types, split into fix tracts and blind tracts.
5. Before go-live: reading cache, saved corrections, remembered flag decisions.

## Working note

Don't read large tracts page by page in chat: it uses up the chat allowance fast. Run them through the API on the
PC and bring only the outputs into chat.
