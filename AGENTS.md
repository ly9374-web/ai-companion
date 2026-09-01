# Project working notes

- Before making any file or code change, ask the user whether the requested change belongs to the “一眸项目” or the “自己项目”. Do not begin modifying files until the user has explicitly selected one of these two categories. Do not infer the category from the request or from prior tasks.
- If the user selects “一眸项目”, make every change exclusively inside the repository-root `摄像头/` directory. Keep that work fully isolated and self-contained: do not modify files outside `摄像头/`, do not make existing features outside that directory depend on it, and ensure deleting the entire `摄像头/` directory leaves every pre-existing feature outside it unchanged and functional. If the requested implementation cannot satisfy these isolation constraints, stop and explain the conflict before making changes.
- If the user selects “自己项目”, make changes normally but exclusively outside the repository-root `摄像头/` directory; do not modify anything inside `摄像头/`.
- Backend source lives in `src/`; editable frontend source lives in `frontend-src/`.
- `prompts/prompts.yaml` is the single source of truth for all prompt text. Put every new or updated system prompt, user prompt, character prompt, summary prompt, runtime instruction, and tool prompt there; reference it by key from code or character configuration, and do not add standalone prompt bodies to other YAML, Markdown, Python, or frontend files.
- `frontend/` is the deployed web build. Prefer changing `frontend-src/` and rebuilding; do not hand-edit hashed files in `frontend/assets/`.
- Exclude `.venv/`, `models/`, `logs/`, `cache/`, `frontend/libs/`, `frontend/assets/`, and all `node_modules/` directories from routine searches.
- For frontend changes, edit `frontend-src/`, then run `./build_frontend.sh` from the project root to build and deploy unless the user explicitly asks not to build. Treat this frontend build as a normal implementation step, not as a test.
- Do not proactively run tests, type checks, linters, targeted imports, compilation checks, or server startups unless the user explicitly asks for validation. Do not use the current ESLint setup or repository-wide `npm run typecheck` as pass/fail gates: ESLint is missing its Airbnb config and the vendored Live2D SDK has existing type errors.
- Do not delete or redownload local models and dependencies as part of validation.
- Preserve existing uncommitted changes in both frontend worktrees.
