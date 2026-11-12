# Supabase: index tract files and call them up

Test setup for storing tract files in Supabase Storage and finding them through the database.

## 1. Create the tables, search functions and bucket (once)

1. Supabase dashboard > **SQL Editor** > New query.
2. Paste all of `supabase/schema.sql` and click **Run**. It is safe to run again later.
3. Check **Storage**: a private bucket named `title-packages` now exists.
4. Check **Storage > Settings** (or the bucket's settings) for the upload size limit. Large combined PDFs can
   be bigger than the default; raise it if an upload fails with a size error.

What it creates:

| Table | One row per | Used for |
|---|---|---|
| tracts | tract | county, acreage, current owner, remaining acres, CAD ID, notice address, latest run |
| documents | file in Storage | path, fingerprint, kind, and everything the program read from it |
| parties | grantor or grantee | owner search |
| runs | indexed run | version, model, flag count, report paths |
| flags | flag | rule, severity, message, what to do, review status |

Search functions: `tract_documents`, `search_owner` (fuzzy names), `search_documents` (any words),
`find_instrument` (recording reference). Signed-in users can read; anonymous visitors can't; only the
program writes.

## 2. Settings on the PC that runs the program

Add to `.env` (see `.env.example`):

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...
SUPABASE_BUCKET=title-packages
```

The URL and keys are under **Project Settings > API Keys**. Use a **secret** key (or the legacy
`service_role` key). It can read and write everything, so it stays in `.env` on this machine and never goes
in the website's code. `.env` is not committed to git.

## 3. Index a tract

```
titlepilot index "C:\Tracts\CCS.TX.SDY.0180"
```

- Uploads every file in the folder to `tracts/<tract>/originals/...`, unchanged (subfolders kept).
- If the tract has been run, records what was read from each document (type, dates, recording reference,
  grantors and grantees, legal description, every field), the run, its flags, and uploads the title report
  and review workbook to `tracts/<tract>/reports/run-<n>/`.
- Files already uploaded with the same contents are skipped, so it is safe to run again after each run.
- `--files-only` indexes files before a run; `--run 3` indexes a particular run; several folders can be
  given at once.

## 4. Call them back up

```
titlepilot search --tract CCS.TX.SDY.0180            every document on the tract, in filing order
titlepilot search --owner "smith ranch"               every document naming that owner (fuzzy)
titlepilot search --text "pipeline easement"          any words (types, names, legal, fields)
titlepilot search --text "vendor's lien" --in-tract CCS.TX.SDY.0180
titlepilot search --ref "1279/279"                    a recording reference on any tract
```

Add `--links` for a link that opens each file (valid one hour).

The same searches work from the website with supabase-js, for a signed-in user:
`supabase.rpc('search_owner', { q: 'smith ranch' })`, and Storage `createSignedUrl(path, 3600)` to open a file.

## What to check on the first tracts

- Storage shows the tract folder with every file, and the file counts match.
- `search --tract` lists every instrument with the right dates and types.
- `search --owner` finds the owner under the name variations used in the deeds.
- Open a few files with `--links` and confirm the right scan opens.
