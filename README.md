# Catchup

Catchup is a local tool for mapping public JavaScript GitHub repositories and explaining meaningful changes between commits. It stores parsed code chunks, local embeddings, likely caller relationships, static test links, push explanations, evidence, and generation metrics. Push polling and email delivery are later project steps and are not implemented yet.

## Requirements

- Python 3.11 or newer (the project venv currently uses Python 3.12).
- Docker Desktop with Docker Compose, for PostgreSQL 16 and pgvector.
- Network access on the first mapping run to download the local embedding model and to read public repositories from GitHub.

## Setup

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create `.env` from `.env.example` if needed, then review the PostgreSQL settings. The defaults use `localhost:5433`; `DATABASE_URL` can override the individual `POSTGRES_*` settings. A GitHub token is optional for public repositories, but a read-only token in `GITHUB_TOKEN` provides a higher API rate limit. Keep `.env` private.

Start PostgreSQL with pgvector and initialize or apply the schema:

```powershell
docker compose up -d
python -m backend.app.db
```

The schema initialization creates the seven existing tables and applies the additive `repos.mapped_commit_sha` and `repos.mapping_error` columns used by Step 3. Check the service with `docker compose ps` and `http://localhost:8000/health` after starting the API.

## Run The API

```powershell
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Interactive API documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

Map the default branch of a public JavaScript repository:

```powershell
Invoke-RestMethod -Method Post `
	-Uri http://localhost:8000/api/repos/map `
	-ContentType "application/json" `
	-Body '{"repository":"Midhat-hub/bookstore-api"}'
```

The response includes the pinned commit SHA, files examined and parsed, chunk/embedding/reference/test-link counts, duration, skipped-file details, and whether this revision was already mapped. Private repositories are refused. Invalid identifiers, unsupported primary languages, missing repositories, GitHub rate limits, parse errors, and model failures return distinct HTTP errors. A failed map is recorded as `failed`; previous chunks remain in place unless a replacement mapping succeeds atomically.

Read the persisted status and counts without calling GitHub:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/api/repos/Midhat-hub/bookstore-api/mapping
```

Explain a push between two commits (the repository must be mapped at the `before_commit` SHA):

```powershell
Invoke-RestMethod -Method Post `
	-Uri http://localhost:8000/api/pushes/explain `
	-ContentType "application/json" `
	-Body '{"repo":"Midhat-hub/bookstore-api","before_commit":"<before-sha>","after_commit":"<after-sha>","use_extra_evidence":true}'
```

Set `use_extra_evidence` to `false` to omit callers, test links, similar code, and rule-based flags. Comment/whitespace-only JavaScript changes return HTTP 204 without calling Gemini or creating an explanation. A successful result is saved in the existing `pushes` and `explanations` tables with its evidence, token counts, duration, model, and extra-evidence setting. Completed push explanations are returned from storage on repeat requests.

Gemini uses `GEMINI_API_KEY` from `.env` and defaults to `gemini-3.5-flash-lite`; `GEMINI_MODEL` can override the model. The key is sent in an HTTP header and is never logged. Calls request JSON structured output, retry transient errors with bounded exponential backoff, and cache responses in memory. Risk flags are computed locally, and generated file/function references are filtered against mapped repository evidence.

## Run The Frontend

Keep the FastAPI server running in one terminal. In a second PowerShell terminal:

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Vite proxies `/api` and `/health` to FastAPI at `127.0.0.1:8000`; the frontend never reads `.env` or receives API keys. Add a public `owner/name` repository to map it, then choose **Explain a push** and enter the before/after commit SHAs. Saved pushes appear newest first with their persisted explanation and evidence. `GET /api/repos` and `GET /api/repos/{owner}/{repo}/pushes` read existing repository, push, explanation, and evidence records; neither route changes database contents.

## Parsing And Embeddings

Tree-sitter with the JavaScript grammar extracts function declarations, named function expressions, identifier-assigned arrows, async functions, class methods, Express-style route callbacks, ES module imports, CommonJS `require` bindings, identifiable calls, and file-level code outside recognized functions. Nested functions get their own chunks while retaining their original enclosing function chunk. Each stored chunk keeps its repository-relative path, source text, kind, and one-based start/end lines.

The local model is `BAAI/bge-small-en-v1.5` through FastEmbed. A real local inference produced a non-zero vector of **384 dimensions**, matching `vector(384)` in PostgreSQL. The first mapping downloads the model from Hugging Face; the current model cache is about **65 MiB**. It runs on CPU; allow roughly **0.5-1 GiB of RAM** while mapping. Tests inject a fake model and do not initialize, download, or contact the model service.

Files with `.js`, `.jsx`, `.cjs`, and `.mjs` extensions are eligible. `node_modules`, generated/build directories, minified files, non-UTF-8/binary files, and files larger than 1 MB are skipped. A truncated GitHub file tree or a JavaScript parse failure fails the mapping instead of marking an incomplete result ready.

## Static Analysis Limits

- Call edges are resolved only for unique same-file targets or targets connected through supported relative imports. Package aliases, barrel re-exports, dynamic imports, computed properties, and many CommonJS export patterns are not resolved.
- Callback and dynamic calls can be missed. In cases where a target remains ambiguous, the mapper skips the edge; name-based static analysis can still associate a call with the wrong same-named symbol in some code shapes.
- Test links use statically resolved calls and imports. They indicate likely references, not actual runtime test coverage or assertions.
- The parser targets JavaScript/JSX, not TypeScript/TSX or framework-specific syntax outside the JavaScript grammar. Unsupported syntax causes a failed mapping with a file and line error.
- Mapping is a full snapshot at one commit, not incremental. Re-mapping the same ready SHA is a no-op; a new SHA replaces chunks and relationships in one database transaction.
- Push comparison analyzes changed files from both commit trees. Rename metadata from GitHub's compare API is not preserved by the current Step 1 `PushFile` shape, and generated output cannot infer runtime behavior.
- Token counts used to select evidence are estimated at roughly four characters per token; Gemini's returned usage metadata is recorded for actual provider calls. Large pushes are split by file and combined afterward.

## Tests

Run the full suite with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Unit tests use mocked GitHub and database boundaries and do not require credentials or a model download. Tests marked `integration` require the configured PostgreSQL/pgvector service; tests also marked `github` call the public GitHub API.

The live Gemini check is opt-in and makes one request:

```powershell
$env:RUN_LIVE_GEMINI_TEST = "1"
.\.venv\Scripts\python.exe -m pytest -v backend/tests/test_gemini_live.py
Remove-Item Env:RUN_LIVE_GEMINI_TEST
```
