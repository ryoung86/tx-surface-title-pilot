# Working on this repo

- **Audit trail:** every change discussed with the user gets a row in `docs/AUDIT_TRAIL.md`
  (note of what came up, change made, why, commit). Add it in the same commit as the change.
  Decisions or deferred ideas with no code change go under "Decisions and open items".
- Deferred ideas to decide before go-live live in `docs/BEFORE_GO_LIVE.md`.
- Bump the version in `src/titlepilot/__init__.py` and `pyproject.toml` with each change sent out.
- Run `pytest` before pushing; only push when it passes.
