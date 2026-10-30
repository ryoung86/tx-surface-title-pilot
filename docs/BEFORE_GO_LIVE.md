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
