-- Title pilot: database and storage for indexing tract files (run once in the Supabase SQL editor).
-- Safe to run again: everything is "if not exists" / "or replace".
--
-- Files live in Storage (bucket title-packages, one folder per tract); this database holds what is known
-- about each file, so tracts, owners and documents can be searched and the scans called back up.
--
--   tracts     one row per tract: county, acreage, current owner, CAD ID, notice address
--   documents  one row per file in Storage: path, fingerprint, kind, and what the program read from it
--   parties    grantors and grantees of each document (for owner search)
--   runs       each program run that was indexed
--   flags      flags from each run, with their review status
--
-- Signed-in users can read everything; only the program (service key) writes. Tighten by role later.

create extension if not exists pg_trgm;

-- ------------------------------------------------------------------------------------------------- tables
create table if not exists public.tracts (
  tract_id          text primary key,
  project           text,
  county            text,
  acreage           numeric,
  current_owner     text[],
  vesting_instrument text,
  remaining_acres   numeric,
  cad_property_id   text,
  notice_address    text,
  latest_run_id     uuid,
  status            text not null default 'indexed',   -- indexed, run, in review, approved
  updated_at        timestamptz not null default now()
);

create table if not exists public.runs (
  run_id        uuid primary key,
  tract_id      text not null references public.tracts(tract_id) on delete cascade,
  local_run_id  integer,                -- the run number in runs.sqlite on the machine that ran it
  started_at    timestamptz,
  program_version text,
  model         text,
  flag_count    integer,
  report_path   text,                   -- Storage path of the title report
  review_path   text,                   -- Storage path of the review workbook
  indexed_at    timestamptz not null default now()
);

create table if not exists public.documents (
  id              uuid primary key default gen_random_uuid(),
  tract_id        text not null references public.tracts(tract_id) on delete cascade,
  storage_path    text not null unique,  -- path inside the bucket
  file_name       text not null,         -- name as uploaded
  folder          text,                  -- subfolder it was uploaded from, if any
  sha256          text not null,         -- fingerprint: the same scan on another tract has the same value
  bytes           bigint,
  content_type    text,
  kind            text,                  -- recorded instrument, runsheet, tax or appraisal record, plat, report...
  instrument_id   text,                  -- recording reference as read (e.g. 'Vol. 1279, Page 279')
  ref_keys        text[],                -- normalized recording references, for matching
  instrument_type text,
  category        text,                  -- conveyance, lien, release, easement, ...
  execution_date  date,
  filing_date     date,
  acreage         numeric,
  legal           text,
  grantors        text[],
  grantees        text[],
  fields          jsonb,                 -- every field the program read, as text
  run_id          uuid references public.runs(run_id) on delete set null,
  uploaded_at     timestamptz not null default now(),
  search          tsvector               -- filled by documents_search_update() below
);

create table if not exists public.parties (
  id            bigint generated always as identity primary key,
  document_id   uuid not null references public.documents(id) on delete cascade,
  tract_id      text not null,
  role          text not null check (role in ('grantor', 'grantee')),
  name          text not null,
  capacity      text,
  marital_status text,
  share         text
);

create table if not exists public.flags (
  id            bigint generated always as identity primary key,
  run_id        uuid not null references public.runs(run_id) on delete cascade,
  tract_id      text not null,
  rule_id       text not null,
  severity      text,
  instrument_id text,
  message       text not null,
  what_to_do    text,
  status        text not null default 'open' check (status in ('open', 'confirmed', 'dismissed', 'fixed')),
  reviewed_by   text,
  reviewed_at   timestamptz
);

-- Search text: recording reference, type and file name weigh most, then names, then the legal description,
-- then every other field read.
create or replace function public.documents_search_update() returns trigger language plpgsql as $$
begin
  new.search :=
    setweight(to_tsvector('english', coalesce(new.instrument_id, '') || ' ' || coalesce(new.instrument_type, '')
                                     || ' ' || coalesce(new.file_name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(array_to_string(new.grantors, ' '), '') || ' '
                                     || coalesce(array_to_string(new.grantees, ' '), '')), 'A') ||
    setweight(to_tsvector('english', coalesce(new.legal, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(new.fields::text, '')), 'C');
  return new;
end $$;

drop trigger if exists documents_search on public.documents;
create trigger documents_search before insert or update on public.documents
  for each row execute function public.documents_search_update();

-- ------------------------------------------------------------------------------------------------ indexes
create index if not exists documents_tract     on public.documents (tract_id);
create index if not exists documents_sha       on public.documents (sha256);
create index if not exists documents_refs      on public.documents using gin (ref_keys);
create index if not exists documents_search    on public.documents using gin (search);
create index if not exists documents_fields    on public.documents using gin (fields jsonb_path_ops);
create index if not exists parties_name_trgm   on public.parties using gin (name gin_trgm_ops);
create index if not exists parties_document    on public.parties (document_id);
create index if not exists flags_run           on public.flags (run_id);
create index if not exists flags_tract_status  on public.flags (tract_id, status);

-- -------------------------------------------------------------------------------------- search functions
-- Called from the website (supabase.rpc('search_owner', {q: 'smith ranch'})) or the titlepilot search command.

-- Owner or party name, fuzzy: 'Smith Ranch LLC' also finds 'Smith Ranch, Ltd.' and 'J. Smith Ranch'.
create or replace function public.search_owner(q text, min_similarity real default 0.3)
returns table (tract_id text, name text, role text, similarity real, instrument_id text, instrument_type text,
               filing_date date, storage_path text)
language sql stable as $$
  select p.tract_id, p.name, p.role, similarity(p.name, q) as similarity, d.instrument_id, d.instrument_type,
         d.filing_date, d.storage_path
  from public.parties p join public.documents d on d.id = p.document_id
  where similarity(p.name, q) >= min_similarity or p.name ilike '%' || q || '%'
  order by similarity(p.name, q) desc, d.filing_date nulls last
  limit 200
$$;

-- Any words, ranked: 'pipeline easement Reeves', 'vendor''s lien 1279', 'Section 22 Block 4'.
create or replace function public.search_documents(q text, tract text default null)
returns table (tract_id text, instrument_id text, instrument_type text, filing_date date, file_name text,
               storage_path text, rank real)
language sql stable as $$
  select d.tract_id, d.instrument_id, d.instrument_type, d.filing_date, d.file_name, d.storage_path,
         ts_rank(d.search, websearch_to_tsquery('english', q)) as rank
  from public.documents d
  where d.search @@ websearch_to_tsquery('english', q) and (tract is null or d.tract_id = tract)
  order by rank desc, d.filing_date nulls last
  limit 200
$$;

-- Every file on a tract, in filing order (runsheets, printouts and unread files at the end).
create or replace function public.tract_documents(tract text)
returns table (instrument_id text, instrument_type text, category text, filing_date date, kind text,
               file_name text, storage_path text, grantors text[], grantees text[])
language sql stable as $$
  select d.instrument_id, d.instrument_type, d.category, d.filing_date, d.kind, d.file_name, d.storage_path,
         d.grantors, d.grantees
  from public.documents d
  where d.tract_id = tract
  order by d.filing_date nulls last, d.instrument_id nulls last, d.file_name
$$;

-- A recording reference on any tract: 'which tracts does this instrument touch?'
create or replace function public.find_instrument(ref text)
returns table (tract_id text, instrument_id text, instrument_type text, filing_date date, storage_path text)
language sql stable as $$
  select d.tract_id, d.instrument_id, d.instrument_type, d.filing_date, d.storage_path
  from public.documents d
  where d.instrument_id ilike '%' || ref || '%'
     or exists (select 1 from unnest(d.ref_keys) k
                where k = lower(regexp_replace(ref, '[^0-9A-Za-z/]+', '', 'g')))
  order by d.tract_id
$$;

-- --------------------------------------------------------------------------------------- access (RLS)
alter table public.tracts    enable row level security;
alter table public.runs      enable row level security;
alter table public.documents enable row level security;
alter table public.parties   enable row level security;
alter table public.flags     enable row level security;

do $$
declare t text;
begin
  foreach t in array array['tracts', 'runs', 'documents', 'parties', 'flags'] loop
    if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = t
                   and policyname = 'signed-in users can read') then
      execute format('create policy "signed-in users can read" on public.%I for select to authenticated using (true)', t);
    end if;
  end loop;
end $$;
-- The program writes with the service key, which bypasses RLS. No insert/update policies for users yet.
-- Spelled out (Supabase grants broadly by default): signed-in users read, anonymous visitors get nothing.
revoke all on public.tracts, public.runs, public.documents, public.parties, public.flags from anon;
grant select on public.tracts, public.runs, public.documents, public.parties, public.flags to authenticated;
grant all on public.tracts, public.runs, public.documents, public.parties, public.flags to service_role;
grant usage, select on all sequences in schema public to service_role;
revoke execute on function public.search_owner(text, real), public.search_documents(text, text),
  public.tract_documents(text), public.find_instrument(text) from public, anon;
grant execute on function public.search_owner(text, real), public.search_documents(text, text),
  public.tract_documents(text), public.find_instrument(text) to authenticated, service_role;

-- ------------------------------------------------------------------------------------------- storage
-- Private bucket: files are opened through short-lived signed links, never public URLs.
insert into storage.buckets (id, name, public)
values ('title-packages', 'title-packages', false)
on conflict (id) do nothing;

do $$
begin
  if not exists (select 1 from pg_policies where schemaname = 'storage' and tablename = 'objects'
                 and policyname = 'signed-in users can read title packages') then
    create policy "signed-in users can read title packages" on storage.objects
      for select to authenticated using (bucket_id = 'title-packages');
  end if;
end $$;
