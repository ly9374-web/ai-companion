# Project working notes

- Backend source lives in `src/`; editable frontend source lives in `frontend-src/`.
- `frontend/` is the deployed web build. Prefer changing `frontend-src/` and rebuilding; do not hand-edit hashed files in `frontend/assets/`.
- Exclude `.venv/`, `models/`, `logs/`, `cache/`, `frontend/libs/`, `frontend/assets/`, and all `node_modules/` directories from routine searches.
- For frontend changes, edit `frontend-src/`, then run `./build_frontend.sh` from the project root to build and deploy unless the user explicitly asks not to build. Treat this frontend build as a normal implementation step, not as a test.
- Do not proactively run tests, type checks, linters, targeted imports, compilation checks, or server startups unless the user explicitly asks for validation. Do not use the current ESLint setup or repository-wide `npm run typecheck` as pass/fail gates: ESLint is missing its Airbnb config and the vendored Live2D SDK has existing type errors.
- Do not delete or redownload local models and dependencies as part of validation.
- Preserve existing uncommitted changes in both frontend worktrees.
