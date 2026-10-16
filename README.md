# Catchup: Explain Every Push

Catchup is a local developer tool that explains every push to the default branch of a public GitHub repository in beginner-friendly, plain language. It maps the codebase, pinpoints affected callers and tests, generates structured summaries using LLMs, and flags potential risks.

---

## Prerequisites

- **Python**: 3.12 (virtual environment at `.venv`)
- **Docker Desktop**: Running with Docker Compose support

---

## Step 1 Setup & Verification

### 1. Activate the Virtual Environment

**Windows PowerShell:**
```powershell
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
source .venv/bin/activate
```

---

### 2. Install Dependencies

Install the pinned backend dependencies into the virtual environment:
```powershell
pip install -r requirements.txt
```

---

### 3. Configure Environment Variables

Copy the example environment file:
```powershell
Copy-Item .env.example .env
```

Review `.env` and adjust the PostgreSQL connection details if needed. Safe defaults for local development are provided.

---

### 4. Start PostgreSQL with Docker Compose

Launch the PostgreSQL 16 container with `pgvector` enabled:
```powershell
docker compose up -d
```

Verify that the container is healthy:
```powershell
docker compose ps
```

---

### 5. Initialize the Database Schema

Run the database initialization script to create all 7 core tables and enable the `vector` extension:
```powershell
python -m backend.app.db
```

This verifies connectivity and creates:
- `repos`
- `chunks`
- `references`
- `test_links`
- `pushes`
- `jobs`
- `explanations`

---

### 6. Run the FastAPI Application

Start the local API development server with hot-reload:
```powershell
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

- **API Documentation**: Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser.
- **Health Check**: Open [http://localhost:8000/health](http://localhost:8000/health) to inspect app and database readiness.

---

### 7. Run the Test Suite

**Run all unit tests (isolated, no live database required):**
```powershell
pytest -m "not integration"
```

**Run integration tests against the live PostgreSQL database:**
```powershell
pytest -m integration
```

**Run all tests together:**
```powershell
pytest -v
```
