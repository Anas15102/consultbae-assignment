# ConsultBae AI Automation — Take-Home Assignment

A fully working end-to-end pipeline: messy CSV merge → SQLite → Flask audio app → n8n automation.

---

## Project Structure

```
.
├── source1_naukri_applicants.csv   # Raw input
├── source2_gig_workers.csv
├── source3_cbnexus_contacts.csv
├── database.db                     # Generated SQLite DB (after running pipeline)
├── requirements.txt
├── scripts/
│   └── merge_pipeline.py           # Task 1: Data merge
├── app/
│   ├── app.py                      # Task 3: Flask audio app
│   └── templates/
│       ├── index.html              # Submit page
│       └── submissions.html        # Submissions list page
├── n8n/
│   └── duplicate_check_flow.json   # Task 2: n8n automation export
├── docs/
│   └── data_issues_report.csv      # Generated issues log
└── uploads/                        # Audio files (created at runtime)
```

---

## Setup & Run

### Prerequisites
- Python 3.10+
- pip

### Install dependencies
```bash
pip install -r requirements.txt
```

### Task 1 — Run the merge pipeline
```bash
python3 -W ignore scripts/merge_pipeline.py
```
This reads all 3 CSVs, cleans/normalizes them, merges by email → phone → name, and writes `database.db` + `docs/data_issues_report.csv`.

### Task 3 — Run the audio app
```bash
python3 -W ignore app/app.py
```
Open http://localhost:5050 in your browser.

---

## Task 1 — Merge Pipeline Design

### Matching Strategy
No single ID spans all 3 sources, so I used a cascade:

1. **Email (primary)** — normalized to lowercase, `alt.` prefix stripped. Matches source1 ↔ source2.
2. **Phone (secondary)** — stripped to 10-digit Indian mobile (removes `+91`, `91`, leading `0`). Matches (source1 ∪ source2) → source3.
3. **Name (fallback)** — title-cased exact match. Used for the 5 source3 records where phone wasn't present in source1/2.

### Result
- 42 source1 records + 30 source2 records + 30 source3 records → **55 unique persons**
- 91 data quality issues logged

---

## Task 4 — Data Issues Report

Every issue is logged to `docs/data_issues_report.csv`. Summary:

### SOURCE 1 — Naukri Applicants

| Issue | Count | What I did |
|---|---|---|
| `Current CTC` stored as lakhs (e.g. `4.2`, `11.9`) instead of rupees | 21 | Detected values < 100, multiplied by 100,000 |
| Phone numbers with no country code, no leading 0 — plain 10-digit | 30 | Normalized — stripped/padded to 10-digit standard |
| `Applied Date` in 5+ different formats: `24-07-2026`, `7 Jul 2026`, `08/19/2026`, etc. | 9 unparseable | Tried 7 format strings; stored raw if none matched |
| Intra-source duplicate rows (same email appearing twice) | 4 | Flagged in issues log; the later row was deduplicated at INSERT |
| Email with `alt.` prefix (`alt.nikhil.chopra70@example.com`) | 1 | Stripped prefix before matching |
| Name stored as initial + surname (`R. Verma`) | 1 | Kept as-is; flagged — can't reliably expand |
| `Nikhil Chopra` appears twice with different emails but same phone + NOIDA | 2 rows | Flagged — likely same person; both kept with different email keys |
| `Rohit Verma` / `R. Verma` appear twice with identical email + phone | 2 rows | Exact duplicate; second INSERT skipped by UNIQUE constraint |

### SOURCE 2 — Gig Workers

| Issue | Count | What I did |
|---|---|---|
| One completely blank row (all nulls) | 1 | Skipped |
| One row with **shifted columns** — skills data landed in `email_id` field (`"react, javascript, mysql"` as email) | 1 | Detected by checking for commas + known tech keywords in email field; skipped |
| Email addresses in ALL CAPS | many | Lowercased at normalization |
| Rate values in inconsistent units: `1415/hr` vs `72k/month` vs `28k/month` | all | Normalized: `/hr` kept as-is, `Xk/month` → `X000/month` |
| `Isha Chopra` duplicated (same shifted row reappeared lower in file) | 1 | Flagged as intra-source duplicate |
| Worker status values inconsistent: `active`, `ACTIVE`, `Active`, `paused`, `Inactive` | all | Title-cased |
| `arjun.mehta77@mailtest.example.org` in source2 vs `arjun.mehta9@example.in` in source1 — same name/city, different email | — | Could not safely auto-merge without email/phone match; kept separate. Flagged manually. |

### SOURCE 3 — CBNexus Contacts

| Issue | Count | What I did |
|---|---|---|
| Duplicate header row in the middle of the file (row 14) | 1 | Detected and dropped |
| Phone numbers with `919...` prefix (12-digit, `91` + 10-digit) | 17 | Stripped `91` prefix |
| Mixed formats: `+91-9000000131`, `919000000131`, `9000000131` | all | Normalized to 10-digit |
| `Verified` field: `Y`, `yes`, `Yes`, `N`, `No`, `N` | all | Normalized to `Y`/`N` |
| Names in ALL CAPS | many | Title-cased |
| `Arjun Mehta` appears **twice** with different phones (9000000131, 9000000272) — likely two different people with same name | 2 | Both kept; logged as potential same-name different-person |
| `Priya Singh` maps to Gurugram in source3 but GURGAON in source1 — same normalized city | — | Both → `Gurugram` via city map |

### MERGE-LEVEL

| Issue | Count | What I did |
|---|---|---|
| 5 source3 records matched by name fallback (phone not in source1/2) | 5 | Merged; logged with `matched_by_name` action |
| Source3-only records with no email, no phone match | several | Added as new records with `sources=source3` |

---

## Task 2 — n8n Automation

**Flow:** Webhook receives new CSV → parse rows → normalize email/phone → SQLite lookup → if duplicate found → Slack alert → respond.

File: `n8n/duplicate_check_flow.json`

### How to import and run
1. Open n8n (cloud trial at https://app.n8n.cloud or self-host with `npx n8n`)
2. Go to **Workflows → Import** → paste/upload `duplicate_check_flow.json`
3. Set up credentials:
   - **SQLite**: point to your `database.db` file path
   - **Slack OAuth2**: connect your Slack workspace (channel `#data-alerts`)
4. Activate the workflow
5. Test by POSTing to the webhook URL:

```bash
curl -X POST https://your-n8n-instance/webhook/new-csv-upload \
  -H "Content-Type: application/json" \
  -d '{
    "csv_data": "email,name,phone\ntanvi.gupta31@example.com,Tanvi Gupta,9000000254",
    "filename": "test_batch.csv"
  }'
```

The flow normalizes each incoming row and queries the DB by email OR phone. If any match is found, a Slack message is posted with the matched record details.

---

## Task 3 — Audio App

Two pages:

**Submit (`/`):**
- Enter name + phone
- Record directly in browser (MediaRecorder API, opus/webm)
- OR upload a file (WAV, MP3, OGG, M4A, FLAC)
- On submit: audio stored in `uploads/`, record written to `audio_submissions` table with extracted properties

**Submissions (`/submissions`):**
- Table of all recordings with play button
- Shows: duration, sample rate (kHz), bitrate (kbps), loudness (dB), noise quality estimate
- Live search by name/phone
- Stats bar (total, avg duration, avg loudness, clean count)
- Auto-refreshes every 30s

### Audio property extraction
- **Duration** + **Sample rate**: via `soundfile` (fast, no ffmpeg needed for WAV/OGG/FLAC) with `librosa` fallback
- **Bitrate**: estimated as `file_size_bits / duration_sec / 1000` kbps
- **Loudness (dB)**: RMS amplitude → `20 * log10(rms)`
- **Noise estimate**: spectral flatness via `librosa`. Flatness near 1.0 = white noise, near 0 = tonal/speech. Thresholds: `>0.5` = high noise, `0.15–0.5` = moderate, `<0.15` = clean.

---

## Task 5 — Scale to 5,000 Workers (Stretch)

**What breaks first:**

1. **Local file storage** — `uploads/` on a single server disk fills up fast. 5,000 workers × avg 2 MB audio = ~10 GB minimum. Switch to S3/R2 with presigned upload URLs before launch.

2. **Synchronous audio analysis** — `librosa` analysis blocks the request thread. Under concurrent load this kills the server. Move analysis to a background job queue (Celery + Redis, or even a simple threading pool) and return the submission ID immediately with a `processing` status.

3. **SQLite under write concurrency** — SQLite serializes writes. With hundreds of simultaneous submissions it becomes the bottleneck. Migrate to Postgres (single step with the same schema).

4. **No deduplication on submit** — same person can submit 50 times. Add a simple rate-limit per phone number (e.g. Redis with a 1-submission/minute key) and optionally store a hash of the audio file to detect identical re-uploads.

5. **No upload size/format validation** — someone uploads a 500 MB video file labeled `.mp3`. Add server-side MIME sniffing (not just extension check) and a hard 20 MB limit enforced before writing to disk.

**Changes before launch:**
- S3 for storage, presigned URLs for direct browser → S3 upload (bypasses server entirely)
- Postgres + connection pooling (PgBouncer)
- Background worker for audio analysis (submit returns instantly, analysis runs async)
- Phone-based rate limiting
- CDN in front for the static assets

---

## Stuck Log

### 1. NumPy 2.x / Pandas compatibility crash
The Anaconda environment had NumPy 2.5.2 but `pyarrow`, `numexpr`, and `bottleneck` were compiled against 1.x. Every `import pandas` exploded with `_ARRAY_API not found`. Initial instinct was to downgrade NumPy — rejected because that would break librosa/soundfile. Instead upgraded `numexpr` and `bottleneck` to their latest versions (which support NumPy 2.x), which cleared the crash while keeping everything else intact.

### 2. Matching source3 without email
Source3 only has name + phone. Phone formats were wildly inconsistent (`+91-9000000131`, `919000000131`, `9000000131`, `09000000131`). I wrote a single `normalize_phone()` function that strips all non-digits and then checks length, stripping leading `91` (12-digit) or `0` (11-digit) to land on a canonical 10-digit string. Tested against all format variants in the data before trusting the merge. Five records still didn't match by phone and needed the name fallback — logged each one explicitly.

### 3. Source2 shifted/corrupted row
One row in `source2_gig_workers.csv` had all columns shifted left — the skill tags string (`"react, javascript, mysql"`) had landed in the `email_id` column. A naive parser would try to use that as an email and silently corrupt the record. I detected it by checking if the `email_id` field contains a comma AND any known tech keyword — if so, skip and log. Considered trying to re-parse the row by shifting columns back, but the original column order couldn't be recovered reliably, so skipping was the safer call.
