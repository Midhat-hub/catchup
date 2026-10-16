# Catchup: Explain Every Push (final plan, local version)

> Working name. Last updated: 16 Oct 2026. Owner: Midhat.
> This is the single source of truth. Paste it at the start of every new coding chat.

---

## 1. What we are building

A local web app. You add a public GitHub repository by pasting its name. The app reads and maps the repo. After that, **every time someone pushes to the main branch, the app explains the change in plain language**, says what else it might affect, shows it on a page and emails it.

**Why:** a lot of code is now written quickly, often with AI tools, and the people who have to work with it did not write it and cannot keep up. Catchup helps them catch up.

**Main purpose of this project:** to show in interviews that I can build a real **pipeline** (ingestion, chunking, embeddings, vector search, retrieval, prompt building, generation, checking, evaluation) and explain every decision in it with a number.

**Goals**
- A working ingestion pipeline: read repo, split into pieces, store, search.
- A "who calls whom" map to find likely-affected code.
- Beginner-friendly explanations that only mention code that really exists.
- A small, honest test with a few real numbers.

**Not doing:** user accounts or sign-in, private repos, languages other than JavaScript, hosting for now, detecting whether code was written by AI (it cannot be done reliably), agents, MCP.

## 2. What it looks like

Runs on my own computer. No login. Pages:

1. **Add repo:** paste `owner/name`. Private repos are refused with a clear message.
2. **Repo status:** "mapping your repo", then "ready".
3. **List of pushes**, newest first. Only pushes made after the repo was added are explained.
4. **Explanation page** for each push:
   - one line: what this change is about,
   - what changed, in simple words,
   - what it might affect, with links to the code,
   - things to know first (short explanations of terms for beginners),
   - flags such as "touches the database", "changes an API route", "many files at once", "no tests cover this",
   - an **evidence panel**: which pieces of code were retrieved, their similarity scores, and what went into the prompt.
5. An **email** with the same explanation, sent to one address set in `.env`.

The website is simple React. It only needs to work and be readable.

## 3. How it works

**Part A: mapping a repo (once, when it is added)**
1. Download the code files.
2. Split them into functions and classes (Tree-sitter, JavaScript).
3. Turn each piece into numbers (an embedding) and store it, so similar code can be found (pgvector).
4. Record who calls whom, and which test files cover which code.

**Part B: explaining a push**
1. A **checker** asks GitHub every few minutes: "any commits newer than the last one I explained?" (no webhooks while local).
2. For each new push it saves a job.
3. A background worker finds what changed (functions and files, not just lines).
4. It gathers evidence: callers, tests, similar code, and flags.
5. It fits the evidence into a size limit (tokens) and asks Gemini for a structured explanation.
6. A check removes any file or function name that does not exist.
7. The result is saved, shown on the website and emailed.
8. The changed files are re-mapped, and the repo's "last explained commit" is updated.

## 4. Decisions (locked)

| Topic | Decision |
|---|---|
| Backend | Python 3.11+, FastAPI |
| Frontend | React, simple |
| Repos we explain | JavaScript (Node), public only, enforced in code |
| Users and sign-in | None. Local use only |
| GitHub access | A read-only personal access token in `.env`, behind a `get_token()` function |
| Trigger | A push to the repo's default branch, found by polling every few minutes |
| Database | Postgres with pgvector, in Docker |
| Embeddings | One small local model (free, no rate limits) |
| Explanation model | Gemini Flash or Flash-Lite, free tier, behind one wrapper function |
| Job queue | A `jobs` table in Postgres |
| Email | One address from `.env`, free tier of an email service or Gmail |
| Cost | Zero. Free tools only |
| Hosting | Not now. Docker plus environment variables |
| Test repo | Your distributed cache project (Node), which must be public |

## 5. Data

- `repos`: id, github repo id, name, status (mapping, ready, failed), last explained commit.
- `chunks`: repo id, file, name, kind, start line, end line, text, embedding.
- `references`: caller chunk, callee chunk.
- `test_links`: test file, chunk it covers.
- `pushes`: repo id, before commit, after commit, status.
- `jobs`: id, payload, status, attempts, error.
- `explanations`: push id, content, evidence used, tokens in, tokens out, seconds, model, extra_evidence on or off.

**Data shapes (define in Step 1, do not change later)**
- **PushData:** repo, before commit, after commit, files (path, status, patch).
- **ChangedSymbol:** file, name, kind (added, modified, removed), start line, end line, diff snippet.
- **Evidence:** changes, callers, tests, similar code (with scores), flags, token count.
- **Explanation:** one_line, what_changed, might_affect (target, reason, link), things_to_know (term, meaning), flags, meta (model, tokens, seconds).

The main function is `explain_push(repo, before, after, use_extra_evidence)`. The worker, the test and the website all use it.

## 6. The pipeline, as I would explain it in an interview

| Stage | What it does | Decision to be ready to defend | Number to quote |
|---|---|---|---|
| Ingestion | Downloads the repo's files | Public only; polling instead of webhooks while local | Time to map a repo |
| Chunking | Splits code into functions and classes | By function, not fixed size, so each piece makes sense alone | Chunks per repo, average size in tokens |
| Embeddings | Turns each chunk into numbers | One small local model: free, no rate limits | Seconds per chunk |
| Vector search | Finds similar code in pgvector | Top-k results, similarity scores shown on the page | Search time |
| Evidence | Adds callers, tests, flags to the search results | A connection map finds what similarity search cannot | Affected code found with vs without |
| Prompt building | Fits everything into a token budget | Fixed priority order; big pushes split by file | Tokens per push |
| Generation | Gemini writes the explanation | Small fast model; structured output; retries on rate limits | Seconds per push |
| Checking | Removes made-up names | Every named file or function must exist | Made-up names removed |
| Evaluation | A small test with hand labels | Own repo, labeled by me, limits stated honestly | Results table |

## 7. Build steps (each built once, in final form)

Estimates are focused working days.

**Step 1. Setup (1 day).** Project layout, Docker with Postgres and pgvector, settings in `.env`, logging, **all tables created at once**, and the data shapes above.
*Done when:* `docker compose up` works and the tables exist.

**Step 2. GitHub connection (1 to 2 days).** A client that fetches a repo's files and commits, and checks that a repo is public. Handles pagination and rate limits, and caches raw responses. The token comes from `.env` through `get_token()`.
*Done when:* given `owner/name` it returns the file list and the latest commits, and a private repo is refused.

**Step 3. Map a repo (6 to 8 days).** Parse JavaScript with Tree-sitter: function declarations, arrow functions assigned to names, class methods, Express route handlers, and imports (`import` and `require`). Split by function (plus a file-level piece for anything outside functions), embed locally, store in pgvector. Record who calls whom by function name, using the imports to find the right file, and record which test files (`*.test.js`, `*.spec.js`, `__tests__`) mention each function.
*Known limits, written in the README:* calls through callbacks, dynamic calls and same-name functions in different files can be matched wrongly.
*Done when:* a repo is mapped, the status page says "ready", and you checked the callers of 10 functions by hand.

**Step 4. Explain a push (6 to 8 days).**
- Find changed functions from the patch. Ignore comment-only and whitespace-only changes.
- Build the evidence (callers, tests, similar code with scores, flags). The flags are plain rules: a database or migration file changed, a config or `.env`-style file changed, `package.json` dependencies changed, an API route changed, many files at once, no test covers the change.
- Count tokens, fit the limit, and split big pushes by file, then combine.
- Call Gemini through one wrapper: structured output, retries with growing delays on rate limits, caching, and logging of tokens and seconds.
- Run the check that removes made-up names, and log how many it removed.
- `use_extra_evidence = false` skips callers, tests, similar code and flags (used for the test).
- Save the evidence used, so the page can show it.

*Done when:* `explain_push` returns a valid explanation for 5 real pushes.

**Step 5. Trigger and delivery (2 days).** The polling checker creates jobs for new pushes, the worker runs `explain_push`, the result is saved, the email is sent, and the changed files are re-mapped.
*Done when:* pushing to the test repo produces an email and a saved explanation within a few minutes.

**Step 6. Simple UI (3 to 4 days).** React pages: add repo, repo status, list of pushes, explanation page with the evidence panel.
*Done when:* you can use the whole flow from the browser.

**Step 7. Small test and README (2 to 3 days).** See section 8, then write the README.
*Done when:* the README opens with the results.

**Total:** about 21 to 28 working days, roughly **3 to 5 weeks** part-time with ChatGPT writing the code. This is an estimate.

## 8. The small test

After adding the test repo, make about **10 pushes** of different kinds:
- rename a function that other code calls,
- add a parameter to a function,
- delete a function,
- change a function that has no tests,
- change a config file,
- change a database query or migration,
- add a new feature,
- change only comments (the tool should ignore it),
- fix a small bug,
- change something only tests use.

For each push, write down by hand which functions or files it affects. You wrote the change, so you know. Then:
1. Let the app explain each push as normal.
2. Compare each explanation with your list.
3. Run the same pushes again with `use_extra_evidence = false`, and compare.

**Numbers to report**
- How much of the affected code was found with and without the extra evidence, and how many wrong items were flagged.
- Seconds per push and tokens per push (from the logs).
- How many made-up names the check removed.

**README honesty note:** say plainly this is a small test on your own repo, so nobody reads more into it than it deserves. Report results that did not help, too.

## 9. Tokens and limits

- A token is a small chunk of text, about 4 characters of English. Code uses more per line.
- **Target per request:** about 6,000 to 10,000 tokens in, 400 to 600 out.
- **Context window** is the most tokens in one request. **Rate limits** are how many requests or tokens per minute or day. On the free tier, rate limits are the tight ones and they change, so check Google's page.
- Do not put the whole repo into the prompt. It is slower and the AI misses what matters.
- Process one push at a time, retry with delays, cache results.

## 10. The AI-written-code focus

- Big pushes are explained in layers, so they do not become a wall of text.
- Risk flags help readers who did not write the code.
- "What might this affect?" is the main question each explanation answers.
- We do **not** try to detect AI-written code. Optional later: if a commit is already labeled as AI-assisted, say so.

## 11. Risks

| Risk | What to do |
|---|---|
| Overlaps with existing tools | After-push, beginner-first angle plus real numbers |
| Cannot see runtime behavior | Say "likely affected" and always link evidence |
| Name-based call matching links wrong functions | Document it and report wrong flags honestly |
| Free-tier limits change | AI call stays behind one wrapper |
| Code privacy | Public repos only, enforced in code |
| App has no login | Run it only on this computer. Never put it online in this form |
| Two assistants drifting apart | One main coder, this file as the source of truth |
| Running over time | Cut UI polish before cutting the small test |

## 12. Later additions (only after Step 7)

- **Hosting:** needs two additions first. Sign-in with GitHub (a small change: a users table, a user id on repos and explanations, and a login screen), and real webhooks in place of polling. Then Neon for the database, a free web host for the backend, a free static host for the UI.
- An agent version that chooses its own lookups.
- An MCP wrapper so an editor assistant can ask "what could changing this affect?".
- More languages, incremental mapping, explaining past pushes, a demo video.

## 13. Repo layout

```
catchup/
  PROJECT.md
  README.md
  .env.example
  docker-compose.yml
  backend/
    app/
      models.py        data shapes
      config.py
      github/          Step 2
      parsing/         Step 3
      index/           Step 3
      explain/         Step 4
      delivery/        Step 5: checker, worker, email
    tests/
  frontend/            Step 6
  test/
    pushes.json        Step 7: your 10 pushes and labels
    results/
```

## 14. Rules for the coding assistant (paste at the start of each chat)

- I am new to this and will copy-paste your code. Give **one step at a time, in final form**: no placeholders, no "we will improve this later".
- Follow sections 4 to 7. If you think a decision or data shape should change, say so first and explain why.
- Explain each piece in simple words.
- Free tools only. Ask before adding a library.
- Settings and keys go in environment variables. Never ask me to paste a key into a chat.
- Small functions with type hints, and a simple test for each.
- Do not invent library functions. If unsure, say so.
- Every AI call goes through the single wrapper.
- This version has no user accounts and no login. Do not add them.

## 15. Open items

- Final name.
- Whether to record a demo video later.
- Make sure the test repo (the cache project) is public before Step 2.
