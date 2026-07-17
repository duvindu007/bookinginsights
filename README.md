# Booking Insights

A FastAPI + Streamlit app for booking data: upload a CSV, every row gets
validated, valid rows land in PostgreSQL. Multi-user accounts (JWT auth),
a live analytics dashboard with PDF export, and an optional daily summary
email round it out.

## Columns expected in the CSV
`Booking No, Agent, Country, Tour Type, Booking Date, Amount, Status`

## Validation rules
1. **Mandatory fields** — every column must have a non-empty value.
2. **Invalid dates** — `Booking Date` must parse as a real date
   (`YYYY-MM-DD`, `DD-MM-YYYY`, `DD/MM/YYYY`, or `MM/DD/YYYY`).
3. **Duplicate Booking Numbers** — rejected if the Booking No already exists
   in the database *or* appears more than once in the uploaded file.
4. **Invalid Amounts** — `Amount` must be a valid, finite, non-negative
   number. Non-numeric text is rejected, and so are `nan`/`inf`/`-inf`/
   `Infinity`-style strings — Python's own `float()` parses those into
   real (non-JSON-safe) special values, so they're explicitly checked for
   and rejected rather than silently accepted as valid numbers.

Rows that fail any rule are **removed** (never written to the database) and
**logged**: every rejected row is written to a CSV log file on disk
(`upload_logs/`), one log file per upload, containing the full list of
rejected rows with their row number, booking number, and reason.

## Database schema

The data is normalized — `Agent`, `Country`, `TourType`, and `Status` are
each their own table with a unique `name`, and `Booking` references them
by foreign key instead of storing the strings directly:

```
agents(id, name)          countries(id, name)
tour_types(id, name)      statuses(id, name)

bookings(
  id, booking_no,
  agent_id      -> agents.id,
  country_id    -> countries.id,
  tour_type_id  -> tour_types.id,
  booking_date, amount,
  status_id     -> statuses.id
)

users(id, username, email, hashed_password, created_at)   -- unrelated to bookings
```

**Uploads still send plain strings** ("TUI", "Germany", ...) — the CSV
format, validators, and `BookingRepository` interface are unchanged.
FK resolution happens inside `SqlAlchemyBookingRepository.bulk_insert()`:
for each upload, it collects the distinct agent/country/tour-type/status
values across all valid rows, looks up which already exist, creates rows
for any that don't (via the generic `LookupRepository` in
`app/lookup_repository.py`), and only then inserts the `bookings` rows
with the resolved ids. This is one batched query per entity type per
upload, not one query per row, so it stays fast at scale. New values are
matched by exact string (after the existing `.strip()` whitespace
trimming) — `"TUI"` and `"tui"` would currently create two separate agents.

Analytics queries (`/analytics/revenue-by-country`, etc.) join back
against these tables, so their JSON output is unchanged even though the
storage underneath is now normalized.

## Daily summary email

A scheduled job (APScheduler, running inside the app process) can send a
daily email with the same numbers as `/analytics/summary` plus top
countries/agents — total bookings, revenue, average booking value, status
breakdown, top 5 countries by revenue, top 5 agents by bookings.

**Disabled by default.** It only activates if both `SMTP_HOST` and
`SUMMARY_EMAIL_RECIPIENTS` are set — otherwise the app logs a warning at
startup and runs normally without it. Config (all via env vars):

| Variable | Default | Purpose |
|---|---|---|
| `SMTP_HOST` | *(unset — feature off)* | SMTP server address |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | SMTP auth, if your server needs it |
| `SMTP_FROM_EMAIL` | `booking-manifest@example.com` | From address |
| `SMTP_USE_TLS` | `true` | Whether to call `STARTTLS` |
| `SUMMARY_EMAIL_RECIPIENTS` | *(unset)* | Comma-separated recipient list |
| `SUMMARY_EMAIL_HOUR` / `SUMMARY_EMAIL_MINUTE` | `7` / `0` | Send time, in **UTC** |

**Test it without waiting for the schedule** — `POST
/analytics/summary-email/send` (requires auth, same as every other route)
sends the report immediately:
```bash
curl -X POST http://localhost:8000/analytics/summary-email/send \
  -H "Authorization: Bearer $TOKEN"
```
Returns `503` if SMTP isn't configured, so you'll know why nothing arrived
rather than getting a silent no-op.

A failed *scheduled* send is logged and skipped — it never crashes the
scheduler, and tries again the next day. A failed *manual* send (via the
endpoint above) surfaces the actual error instead, so you can debug it.

### Testing it locally — no real mailbox needed

`docker compose up` includes a **Mailpit** service — a fake SMTP server
with a web UI, purely for local testing. Nothing sent to it leaves your
machine.

1. In `.env`, set:
   ```
   SMTP_HOST=mailpit
   SMTP_PORT=1025
   SMTP_USE_TLS=false
   SUMMARY_EMAIL_RECIPIENTS=test@example.com
   ```
2. `docker compose up` (or restart if already running, so the new env
   vars take effect)
3. Upload a CSV so there's data to report on
4. Trigger the email immediately, rather than waiting for the scheduled
   hour:
   ```bash
   curl -X POST http://localhost:8000/analytics/summary-email/send \
     -H "Authorization: Bearer $TOKEN"
   ```
5. Open **http://localhost:8025** — the email is sitting there, exactly
   as it would look in a real inbox (subject, both plain-text and HTML
   versions, correct numbers).

Once you've confirmed it looks right, switch `.env` to a real provider
(Gmail example in `.env.example` — needs an
[App Password](https://myaccount.google.com/apppasswords), not your normal
one) and repeat step 4 to confirm actual delivery.

## Architecture

The codebase is layered so each file has one reason to change, following
SOLID principles:

| File | Responsibility |
|---|---|
| `main.py` | Root-level entry point — re-exports the app for `uvicorn main:app`, supports `python main.py` |
| `app/api.py` | HTTP layer — routes, request/response shaping, dependency wiring, middleware, exception handlers |
| `upload_service.py` | Orchestrates one upload: read → validate → insert → log |
| `analytics_service.py` | Orchestrates summary/revenue/agent/trend queries via the repository |
| `daily_summary_service.py` | Builds the daily email report content and sends it, via `AnalyticsService` + `EmailSender` |
| `email_sender.py` | `EmailSender` interface + SMTP implementation |
| `scheduler.py` | APScheduler wiring — reads env vars, schedules the daily job, skips gracefully if unconfigured |
| `auth_service.py` | Signup/login logic — password hashing, username/email uniqueness, credential checks |
| `security.py` | Password hashing (bcrypt) and JWT creation/verification |
| `user_repository.py` | `UserRepository` interface + SQLAlchemy implementation |
| `auth_schemas.py` | Pydantic request/response models for signup/login (`UserSignup`, `UserOut`, `Token`) |
| `validators.py` | One class per validation rule, behind a shared `RowValidator` interface |
| `csv_reader.py` | Parses CSV text into rows, checks required headers |
| `repositories.py` | `BookingRepository` interface + SQLAlchemy implementation |
| `lookup_repository.py` | Generic get-or-create repository shared by Agent/Country/TourType/Status |
| `error_logger.py` | `ErrorLogger` interface + CSV-file implementation |
| `models.py` | SQLAlchemy ORM models — `Booking`, `Agent`, `Country`, `TourType`, `Status`, `User` |
| `database.py` | DB engine/session configuration only |
| `schemas.py` | Plain dataclasses (`ValidationError`, `UploadResult`) passed between layers |
| `logging_config.py` | Central `logging` setup (console + rotating file handler), called once at startup |
| `parsing.py` | Shared date/amount parsing helpers used by both validators and the row transformer |

**How SOLID shows up here:**
- **Single Responsibility** — `MandatoryFieldsValidator`, `DateFormatValidator`,
  `NegativeAmountValidator`, and `DuplicateBookingNoValidator` each check
  exactly one rule; persistence, logging, and HTTP concerns are in separate
  files entirely.
- **Open/Closed** — adding a new validation rule (e.g. "Amount below a
  minimum") means writing one new `RowValidator` subclass and adding it to
  `build_validators()` in `app/api.py`. Nothing else changes.
- **Liskov Substitution** — `RowValidationPipeline`, `BookingUploadService`,
  and the FastAPI routes only ever call methods declared on the
  `RowValidator`, `BookingRepository`, and `ErrorLogger` abstract base
  classes, so any correct implementation is interchangeable.
- **Interface Segregation** — each abstract base class exposes only the
  methods its callers actually need (e.g. `ErrorLogger` has `log`,
  `list_logs`, `get_log_path` — nothing else).
- **Dependency Inversion** — `BookingUploadService` depends on the
  `BookingRepository` and `ErrorLogger` abstractions, not on SQLAlchemy or
  the filesystem directly. Concrete implementations are constructed only in
  `app/api.py`'s dependency-wiring functions, so swapping the SQL engine
  or a file logger for a database-table logger touches one function, not
  the service logic.

One behavioral improvement from this refactor: a row now reports **every**
rule it fails (e.g. missing fields *and* a bad date *and* a negative
amount on the same row all get logged), instead of stopping at the first
failure — the pipeline runs all validators per row rather than short-
circuiting.

## Logging

All modules log through Python's standard `logging` module (no `print`
statements). `logging_config.configure_logging()` runs once at startup and
sets up two handlers on the root logger:
- **Console** — for local dev / container stdout
- **Rotating file** — `logs/app.log`, rotates at 5MB with 5 backups kept

Every request is logged on the way in and out (method, path, status code,
duration) via middleware in `app/api.py`. Business events are logged at the
layer they happen in — e.g. `upload_service` logs how many rows were
validated/rejected, `repositories` logs bulk-insert progress,
`error_logger` logs when a rejection log file is written.

## Global exception handling

`app/api.py` registers exception handlers on the `FastAPI` app so error
handling isn't scattered across routes:

| Exception | Status | Meaning |
|---|---|---|
| `CsvHeaderError` | 400 | Uploaded CSV is missing a required column |
| `HTTPException` | varies | Explicit errors raised in routes (bad file type, 404, etc.) |
| `RequestValidationError` | 422 | FastAPI request-shape validation failed |
| Anything else | 500 | Unexpected bug — full traceback goes to the log, client gets a generic message |

Every handled error is logged (`WARNING` for expected errors, full
`ERROR`-level traceback via `logger.exception(...)` for unexpected ones) and
returned to the client in one consistent shape:
```json
{"error": "HTTPException", "detail": "Only .csv files are accepted"}
```
Routes never leak internal details (stack traces, exception messages) for
unexpected failures — only the generic 500 message is returned, while the
real cause is captured server-side in `logs/app.log`.

## Frontend

`frontend/streamlit_app.py` is the app's only frontend — a Python-only
dashboard, good if you'd rather not touch JS at all. Two tabs:

- **Upload** — drag-and-drop CSV, results shown as metrics + a
  rejected-rows table + a download button for the full error log
- **Analytics** — bar/line charts for status breakdown, revenue by
  country, bookings by agent, and monthly revenue trend, all pulled live
  from the API, plus a **"Download PDF report"** button that bundles the
  summary stats and all three charts into a downloadable multi-page PDF
  (rendered with matplotlib — regenerated only when the analytics data
  actually changes, not on every UI interaction)

Run it with:
```bash
cd frontend
cp .env.example .env      # optional — only needed if your backend isn't on localhost:8000
python3 -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt
streamlit run streamlit_app.py
```
It opens at `http://localhost:8501` with a login/signup gate. The backend
URL comes from `API_BASE_URL` in `.env` (defaults to
`http://localhost:8000` if you skip the `.env` file entirely) — it's not
an editable field in the UI, just a small read-only caption in the
sidebar plus a "Test connection" button, so end users aren't shown or
asked about backend infrastructure. `.streamlit/config.toml` applies the
navy/parchment/brass palette via Streamlit's built-in theming.

Since Streamlit requests happen server-side (Python calling Python, not
the browser), the backend's CORS setting (`app/api.py`,
`allow_origins=["*"]`) doesn't affect it — that's only relevant if you add
a browser-based frontend back in later.

> A plain HTML page and a React component used to live here too, offering
> the same upload flow with a lighter-weight/embeddable option but without
> the Analytics tab. They were removed to keep a single frontend to
> maintain — let me know if you want either brought back.

## Configuration

Every setting the app reads is an environment variable — see
`.env.example` for the full list with comments. To use it:

```bash
cp .env.example .env
# edit .env with real values
```

`.env` is loaded automatically (via `python-dotenv` in `main.py`) whether
you run the app directly (`uvicorn main:app`) or through
`docker compose up` (which also reads the same file to configure the
Postgres container — see `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`
in `.env.example`). Non-sensitive values (ports, log directories, email
send time) fall back to sane defaults if left unset in `.env`.
**Sensitive ones don't** — `POSTGRES_PASSWORD` has no fallback in
`docker-compose.yml` at all (Compose refuses to start without it), and
`SECRET_KEY` falls through to the app's own safer behavior (a random key
per restart, logged as a warning) rather than any hardcoded value. See
"Security notes" below for why that distinction matters.

**Never commit your real `.env`** — it's already in `.gitignore` and
`.dockerignore`.

### Security notes

- **No secrets live in `docker-compose.yml`.** Earlier versions of this
  file had `POSTGRES_PASSWORD`/`SECRET_KEY` fallback defaults baked
  directly into it — since this file is meant to be committed to git,
  that meant a real (if weak) password and, worse, the JWT-signing
  `SECRET_KEY` were permanently public to anyone who ever saw the repo.
  Anyone holding a known `SECRET_KEY` can forge a valid login token for
  any username — that's a full auth bypass, not just a weak password. Both
  fallbacks were removed; misconfiguration now fails loudly (Postgres
  won't start; the app logs a warning and self-generates a safe ephemeral
  key) instead of silently running with a known value.
- **Postgres's port is bound to `127.0.0.1:5433`** (mapped from the
  container's internal 5432, chosen to avoid clashing with a Postgres
  instance you might already have running locally) — reachable from your
  own machine but not from your local network or the internet, even if
  the host has a public IP.

## Local Postgres setup

This app requires PostgreSQL — there's no SQLite fallback. `database.py`
fails fast with a clear error if `DATABASE_URL` isn't set or isn't a
`postgresql://` URL. Two ways to get Postgres running locally:

### Option A — Docker Compose (recommended, no local Postgres install)

```bash
cp .env.example .env
docker compose up --build
```
This starts four containers — Postgres (`db`), the backend, the Streamlit
frontend, and Mailpit (a fake SMTP server for local email testing, see
"Daily summary email" above) — wired together via the same `.env`. The
app creates the `bookings`/`users`/etc. tables on first startup. Data
persists in a named Docker volume across restarts.

Postgres is also exposed on `localhost:5433` if you want to `psql` in from
your host (note: 5433, not the usual 5432 — see docker-compose.yml comment
for why):
```bash
psql -h localhost -p 5433 -U bookingapp -d bookings   # password: whatever you set in .env
```

### Option B — Postgres installed natively

1. Install Postgres ([macOS](https://postgresapp.com/) /
   [Windows](https://www.postgresql.org/download/windows/) /
   `sudo apt install postgresql` on Linux) and make sure it's running.
2. Create a user and database:
   ```bash
   psql -U postgres -c "CREATE USER bookingapp WITH PASSWORD 'bookingpass';"
   psql -U postgres -c "CREATE DATABASE bookings OWNER bookingapp;"
   ```
3. Copy `.env.example` to `.env` — the default `DATABASE_URL` in there
   already points at `localhost:5432` for this exact setup.

### Then, either way, install deps and run the app

```bash
python3 -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn main:app --reload
```
(Skip this if you used Option A — `docker compose up` already runs the app
for you.)

The `bookings` table is created automatically on first startup — no
migration step needed for this schema. The API will be live at
`http://localhost:8000`; interactive docs (Swagger UI) at
`http://localhost:8000/docs`.

## Authentication

Every endpoint except `GET /` requires a valid bearer token — multi-user
accounts, username + email + password, JWT-based. There's no data
isolation between users (all users see the same bookings); this is
account-gating, not multi-tenancy.

Both **username and email must be unique** at signup — you'll get a `400`
naming which one conflicts if either is already taken. **Login accepts
either** — the same field takes a username or an email address.

**Set a real `SECRET_KEY`** (used to sign tokens) in `.env` before running
anywhere beyond local dev:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```
Paste the output as `SECRET_KEY` in `.env`. If left unset, the app
generates a random one on every restart — fine for a five-minute local
test, but every token breaks on restart, and multiple instances won't
agree on tokens.

### `POST /auth/signup`
```bash
curl -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "email": "alice@example.com", "password": "supersecret123"}'
```
Username: 3–50 characters. Email: validated format. Password: minimum
8 characters. Returns `201` with the new user's id/username/email/created_at,
or `400` if the username **or** the email is already taken (the error
tells you which).

### `POST /auth/login`
Standard OAuth2 password flow (form-encoded, not JSON) — this is also what
lets you use the "Authorize" button directly in Swagger UI at `/docs`.
The `username` field accepts **either a username or an email address**.
```bash
curl -X POST http://localhost:8000/auth/login \
  -d "username=alice&password=supersecret123"
# or equivalently:
curl -X POST http://localhost:8000/auth/login \
  -d "username=alice@example.com&password=supersecret123"
```
```json
{"access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer"}
```
Tokens expire after 60 minutes by default (`ACCESS_TOKEN_EXPIRE_MINUTES`
in `.env` to change it).

### Using the token
Every other endpoint needs it as a header:
```bash
curl http://localhost:8000/analytics/summary \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```
Without it, or with an invalid/expired token, you get `401`.

> **If you had this project running before email was added:** the `users`
> table needs an `email` column that doesn't exist on old data. Since there's
> no migration tool (see note in Database schema above), drop and let it
> recreate:
> ```bash
> psql -U bookingapp -d bookings -c "DROP TABLE IF EXISTS users CASCADE;"
> ```
> This only affects `users` — your booking data is untouched — but you
> will need to sign up again afterward.

### `GET /auth/me`
Returns the currently authenticated user — useful for checking a token is
still valid without hitting a data endpoint.

## Endpoints

All routes below require `Authorization: Bearer <token>` (see
Authentication above) — the `curl` examples omit it for brevity, but add
`-H "Authorization: Bearer $TOKEN"` to each one.

### `POST /upload-csv`
Upload a CSV file (`multipart/form-data`, field name `file`).

Example:
```bash
curl -X POST http://localhost:8000/upload-csv -F "file=@bookings.csv"
```

Response:
```json
{
  "filename": "bookings.csv",
  "total_rows_processed": 200000,
  "rows_inserted": 199950,
  "rows_rejected": 50,
  "errors_preview": [
    {"row": 12, "booking_no": "BK011", "reason": "Missing mandatory field(s): Agent"},
    {"row": 45, "booking_no": "BK044", "reason": "Invalid date: '2026-13-40'"},
    {"row": 88, "booking_no": "BK087", "reason": "Negative amount: -300.0"},
    {"row": 130, "booking_no": "BK011", "reason": "Duplicate Booking No"}
  ],
  "errors_truncated": true,
  "error_log_file": "20260715T090000_bookings_a1b2c3d4_errors.csv",
  "error_log_download_url": "/upload-logs/20260715T090000_bookings_a1b2c3d4_errors.csv"
}
```
`errors_preview` shows up to the first 50 rejected rows for a quick look;
the **full** list of every rejected row is always in the log file, whose
filename and download URL are returned as `error_log_file` and
`error_log_download_url`. If nothing was rejected, both are `null`.

### `GET /bookings/count`
Returns the total number of booking rows currently stored.

```bash
curl http://localhost:8000/bookings/count
```

### `GET /analytics/summary`
Overall stats: total bookings, total revenue, average booking value, a
status breakdown, and the earliest/latest booking date.

```bash
curl http://localhost:8000/analytics/summary
```
```json
{
  "total_bookings": 100005,
  "total_revenue": 254308932.0,
  "average_booking_value": 2542.96,
  "status_breakdown": {"Confirmed": 24938, "Cancelled": 24915, "Pending": 25078, "Completed": 25074},
  "earliest_booking_date": "2026-01-01",
  "latest_booking_date": "2026-12-31"
}
```

### `GET /analytics/revenue-by-country`
Revenue and booking count per country, sorted by revenue descending.

```bash
curl http://localhost:8000/analytics/revenue-by-country
```
```json
{"revenue_by_country": [{"country": "France", "bookings": 2, "revenue": 2300.0}, ...]}
```

### `GET /analytics/bookings-by-agent`
Booking count and revenue per agent, sorted by booking count descending.

```bash
curl http://localhost:8000/analytics/bookings-by-agent
```
```json
{"bookings_by_agent": [{"agent": "TUI", "bookings": 3, "revenue": 2300.0}, ...]}
```

### `GET /analytics/monthly-revenue-trend`
Booking count and revenue per calendar month (`YYYY-MM`), sorted
chronologically.

```bash
curl http://localhost:8000/analytics/monthly-revenue-trend
```
```json
{"monthly_revenue_trend": [{"month": "2026-01", "bookings": 8355, "revenue": 21403201.0}, ...]}
```

All four analytics endpoints currently include **every** row regardless of
`Status` (Confirmed, Cancelled, Pending, Completed all count toward
revenue). If you want cancelled bookings excluded from revenue, let me know
and I'll add a status filter.

### `POST /analytics/summary-email/send`
Sends the daily summary email immediately (see "Daily summary email"
above for full details). Returns `503` if SMTP isn't configured.
```bash
curl -X POST http://localhost:8000/analytics/summary-email/send
```

### `GET /upload-logs`
Lists every error log file generated so far, most recent first.

```bash
curl http://localhost:8000/upload-logs
```

### `GET /upload-logs/{log_filename}`
Downloads a specific error log — a CSV with `row, booking_no, reason` for
every row rejected during that upload.

```bash
curl -O http://localhost:8000/upload-logs/20260715T090000_bookings_a1b2c3d4_errors.csv
```

## Notes
- Validated at scale twice: a clean 200,000-row upload completes in ~5
  seconds; a 50,000-row upload with ~47% deliberately invalid data (bad
  dates, negative/non-numeric/non-finite amounts, missing fields,
  duplicates) completes in ~5.4 seconds with every rejection correctly
  categorized.
- Inserts are batched (5,000 rows per commit) for performance on large files.
- Amount parsing rejects `NaN`/`Infinity`/`-Infinity` strings, not just
  non-numeric text — Python's `float()` parses those into real special
  values that would otherwise corrupt `SUM()`/`AVG()` aggregates and
  break JSON serialization. There's also a defensive guard in
  `repositories.py` so even a non-finite value that somehow reaches the
  database (e.g. inserted outside the app) can't crash an analytics
  endpoint — though the real fix for corrupted data is finding and
  removing it: `SELECT ... WHERE amount = 'NaN'::float` (Postgres treats
  NaN as equal to itself, unlike standard IEEE754).
- Error logs are written to the `upload_logs/` folder (created automatically)
  and never deleted automatically — clean them up periodically if disk space
  matters for your deployment.
- No migration tool (e.g. Alembic) — schema changes require dropping and
  recreating the affected table(s), which means losing their data. Ask if
  you want Alembic added; it would remove this limitation going forward.
