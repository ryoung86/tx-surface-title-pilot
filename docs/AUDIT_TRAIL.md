# Audit trail

Every change discussed for the Texas surface title pilot: what came up, what was changed, and why.
Newest at the bottom. Commit IDs point to the full change on GitHub. **Add an entry for every change.**

| # | Date | Ver. | Note (what came up) | Change made | Why | Commit |
|---|---|---|---|---|---|---|
| 1 | 10/29 | 0.1 | Set up the codebase before documents and rules are final | Starter pipeline: intake, page rendering, extraction, rules engine, chain, run history (append-only), review export, sample tract, tests | A working skeleton driven by the Fields and Rules workbook, so landmen can edit rules without code | b0b788b |
| 2 | 10/29 | 0.1 | First live run failed (API 400: forced tool_choice not allowed) | Switched to structured outputs | Newer models reject forced tool use | 52606e8 |
| 3 | 10/29 | 0.1 | Second live run failed (schema grammar too large) | Schema goes in the prompt; replies validated in code with one correction round | Strict structured output can't compile a schema this size | 21fdebb |
| 4 | 10/29 | 0.1 | "Nothing happened in cmd" | Progress lines per document; recording-number matching (Vol/Page vs instrument no.) | User couldn't tell the run was working; refs were written different ways | 1a0f16a |
| 5 | 10/29 | 0.1 | Easement dropped from encumbrances; judgment showed the debtor as holder | Instrument types classified by keyword; holder chosen by type (creditor for judgments, lender for DTs) | Model writes types many ways ("easement/row") | 7e91863 |
| 6 | 10/29 | 0.1 | Agents shouldn't have to fill out an intake form or document list | Intake workbook optional: agent's runsheet, else documents only | Less work for title agents | b0e9f25 |
| 7 | 10/29 | 0.2.0 | "No intake workbook" error with documents in subfolders | Search subfolders; version number printed | Agents organize tracts into folders | dee6d77 |
| 8 | 10/29 | 0.3.0 | First real tract (CCS.TX.SDY.0143) compared to verified title | Fixes from that comparison (types, dates, holders, runsheet use) | Match SEG's verified result | 635f3a4 |
| 9 | 10/29 | 0.3.1 | Long name runsheet cut off (max_tokens) | Runsheets read page by page, streamed, cut-off replies salvaged | One reply couldn't hold a long runsheet | 3cb1cf8 |
| 10 | 10/29 | 0.3.2 | Wrong surface owner, no acreage | tracts.csv found in more places; flag when a deed's acreage can't be read; out-sales told apart by tract acreage | A 1-ac out-sale was taken as the vesting deed | 9735fb4, f3f9cd5 |
| 11 | 10/29 | 0.4.0 | Format the report like SEG's title report (no logo) | Report built from a blank copy of SEG's template: Ownership, BT Data, Encumbrances, Flowchart, Names Examined, Index, Runsheet, plus AI sheets | Output landmen already use | 68f848c |
| 12 | 10/29 | 0.4.1 | Run in chat to save console credits (0143) | Abstract-aware runsheet check (PK02); deed easements/restrictions on Encumbrances; runsheet legal field | Found while reading 0143 by hand; scored 100% vs verified title | a53ed38 |
| 13 | 10/30 | 0.5.0 | Notification and flag when an out-sale or metes and bounds needs mapping | MP01 flag, AI Mapping sheet, mapping_queue.csv, console notice, optional Teams/Slack | Mapping team needs to know without opening each report | 34dc63f |
| 14 | 10/30 | 0.5.1 | Insert an email address later for the notification | Email via Outlook on the PC (or SMTP), report attached; `titlepilot test-email`; off until TITLEPILOT_MAPPING_EMAIL is set | User wants email, address to be decided | a20bc2d |
| 15 | 10/30 | 0.6.0 | Identify the pore space owner if there is such language | pore_space field, PS01 flag, Pore Space Ownership section; default = surface owner (Myers-Woodward, Tex. 2025) unless reserved/conveyed | Texas law: surface owner owns pore space absent agreement; pore space isn't a mineral | bc20576 |
| 16 | 10/30 | 0.7.0 | New tract CCS.TX.SDY.0180 (1,460 pages) read in chat | Runsheets found by folder name; agent's C/X/* marks drive PK02; PK04 duplicate/mislabeled uploads; undated docs never vest; better name matching; leases/timber/accommodation classified; pore space section filled; lessor-wiped bug (C6) fixed; mapping for tracts described only as "a portion of" | Problems seen on a large real tract (e.g. 703/445 is a copy of 703/443) | f6f9c70 |
| 17 | 10/30 | 0.8.0 | Check my 0180 report against SEG's final | ROWs and Easements tab; Active Material table holds only major leases (SOLAR/WIND/CARBON/OGML/GRAZING) with TERM; new term field; reading errors corrected | Match SEG's final layout; ~95% → ~98% substance | 69f9635 |
| 18 | 10/30 | 0.9.0 | Rule to double-check numbers (the 1%/7% mistake) | NUM01: grantee shares must total 100% or the interest conveyed; number words must match digits; per-grantee share field; acreage/interest added to low-confidence check | Catches misread digits without a second reading; found 1.968% read as 1.948% in 809/87 | 1e5425f, f703db4, 345c2a4 |
| 19 | 10/30 | 0.9.0 | Cache readings so reruns don't re-pay the API | **Deferred.** Logged in docs/BEFORE_GO_LIVE.md with flag-noise tuning | Decide before go-live | a537857 |
| 20 | 10/30 | 0.9.0 | Keep an audit trail of every change | This file; kept up to date with each change | Review at project end and for future changes | 2f23fb8 |

## Decisions and open items (no code change)

- **10/30 Pilot plan doc retooled** (Texas Surface Title Extraction Pilot Plan): added Progress to date (0143, 0180 results); updated capture (no intake form, runsheets, tracts.csv), page prep, schema (no nulls; pore_space, term, share, document_kind), prompt (numbers in words and digits), API call (schema in prompt, validated in code), commands, ownership assembly (out-sales, undated docs, pore space, mapping), outputs (SEG format, ROWs tab, AI sheets), live workflow, go-live checklist, key risks, two success metrics, and rules PK01–PK04, OW01, MP01, PS01, NUM01 with implementation status. Why: plan matched the original design, not what the build and the test tracts changed.

- **Vesting deeds on 0180:** SEG cites 1279/279 + 1391/371–435; the program cites 1445/892 (correction deed). Owner is the same. Landman's call.
- **Prudential DT 1279/312 (1989):** marked by the agent as pertaining, not in the package or SEG's mortgages. Confirm release.
- **SEG final corrections found (0180):** 703/445 duplicate; 1445/892 and 703/452 dates; 655/405 grantor; 810/400 shares; 2023052480 number; "Sailin Shoes".
- **Cost estimate:** a full API run of 0180 ≈ $14–24 (Sonnet 5.5), ≈ half with batch.
- **Next test:** a blind tract (finished SEG title shown only after the run), and 0180 through the API.
