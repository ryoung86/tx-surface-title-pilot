# Before go-live: possible changes

Ideas held for later, to decide on before the program is used on live tracts.

## Cache document readings between runs

**Status:** deferred (decided 2026-10-30; revisit before go-live).

Today every run sends every page to the API again, even if only a rule or the report format changed.
A 1,100-page tract like CCS.TX.SDY.0180 means paying again for the full read on every rerun.

Proposal: save each document's reading (keyed by the file's contents, the model, and the version of
config/fields.yaml and config/prompt.md) and reuse it on later runs. Re-read a document only when the
file, the fields or the prompt change; a `--fresh` flag forces a full re-read. Reruns after a rule or
report change would then take minutes and cost nothing.

Things to settle first: where the cache lives (beside the tract or in the output folder), and whether
cached readings count as "reviewed" in the run history.

## Reduce flag noise

CCS.TX.SDY.0180 produced 117 flags. Most are name-runsheet summary lines (PK02) and "grantor not in
chain" (R01) on deeds where some grantors joined only for minerals or where two deeds were recorded the
same day out of order. Tune these so the review workbook leads with what needs a landman's attention.

## Broker sheet, Phases 2 and 3

**Status:** Proposed rows in the workbook (decided 2026-11-05).

- Phase 2: built in 0.11.0 (taxes and CAD from the agent's printouts, notice address, preliminary legal
  description). Still to settle: whether save-and-excepts written inside the vesting deed itself should be
  subtracted automatically (today they are shown as written and only later out-sales are subtracted).
- Phase 3: likely current easement holder (EA06: RRC pipeline data, SOS records, successor companies; note only),
  and the current fee owner, certificate block and IOL/FOL outputs once the project templates are settled.

## Flag volume after the broker rules

The broker's 30 new checks will add flags on every tract (DR08 marital status and EA03 width are the likeliest to be
noisy). Check the counts on the next API run of 0180 and tune thresholds or severities before go-live.

## Web app for title agents (idea, not started)

**Status:** discussed 2026-11-05. Supabase indexing test built 2026-11-12 (0.12.0, docs/SUPABASE_SETUP.md); website, worker and sorting step not started.

Outline: website on Vercel (upload, run status, review, approval, admin); Supabase for login (company Microsoft
accounts, roles agent / landman / admin), Postgres (tracts, runs, flags, approvals, costs) and file storage; the
Python program as a worker on AWS ECS Fargate, holding the API key; a jobs table as the queue for the pilot, Amazon
SQS with autoscaling at volume (10-500 tracts a month, 2-20 agents). Optional bridge from the local SFTP server that
pushes finished tracts (marked READY) to storage, outbound only. Batch API and the reading cache to hold API cost
down. To check: Supabase plan and current website login, who manages AWS.
