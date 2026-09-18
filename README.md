# eGain AI — Transcript Downloader & Combiner

Direct-API tooling for bulk-exporting bot session transcripts from eGain AI
(Specsavers "Domiciliary" bot-sessions, `specsavers_va`), without clicking
through the Transcripts UI page by page.

It has two parts:

1. **`egain_transcript_downloader.py`** — calls eGain's own listing +
   transcript-export API endpoints directly (the same ones the "Transcripts"
   export button in the UI calls) and reconstructs the same CSV format that
   button produces, one CSV per page of up to 500 rows.
2. **`Combine transcript/combine_transcripts.py`** — merges all of those
   per-page CSVs into a single, deduplicated, date-sorted `.xlsx` file.

## ⚠️ Before you push this to a repo

This folder, as uploaded, contains **`token.txt` with a live `Bearer` auth
token** in it. That token is a live credential — anyone who has it can pull
transcript data with it until it expires. **Do not commit `token.txt` (or
any file containing a token) to the repository.**

Add a `.gitignore` before your first commit:

```gitignore
token.txt
token_*.txt
*.csv
*.xlsx
summary.json
.DS_Store
__MACOSX/
```

(The `*.csv` / `*.xlsx` / `summary.json` lines are optional but recommended
— those are the tool's *output*, not source, and downloaded transcript data
generally shouldn't live in git history either.)

If this token has already been pushed anywhere, treat it as compromised:
grab a fresh one (steps below) and let it expire naturally, or get it
revoked if your org's IT can do that.

## What's in this folder

```
Script/
├── egain_transcript_downloader.py     # the downloader
├── run_instruction.txt                # a ready-to-copy example command
├── token.txt                          # YOUR auth token — never commit this
└── Combine transcript/
    └── combine_transcripts.py         # merges the CSVs into one .xlsx
```

## Requirements

```bash
pip3 install requests pandas openpyxl
```

## Getting the auth token

The script doesn't log in for you — it re-uses a token copied out of your
own already-logged-in browser session, the same way you'd normally use the
UI:

1. Log into `https://egain-cloud.specsavers.co.uk` and open the Transcripts
   page (Domiciliary bot-sessions), same as normal.
2. Open Chrome DevTools (F12) → **Network** tab.
3. Change page, or click **Transcripts** (export) once, to trigger a
   request.
4. Click a request to `.../egain/va/v1/session/156` or
   `.../egain/va/v1/exchange/transcript` in the Network list.
5. In the **Headers** panel, find the request header named `Authorization`
   — right-click it and choose **Copy value** (don't drag-select across the
   wrapped lines by hand, it's easy to miss a character).
6. Paste that whole value into `token.txt` and save it.

**The token expires** — when the script starts failing with `401
Unauthorized`, repeat these steps to get a fresh one.

## Usage

### 1. Download transcripts for a date range

```bash
cd "/path/to/Script"
python3 egain_transcript_downloader.py \
    --start 2026-07-31T18:30:00.000Z \
    --end   2026-08-31T18:29:59.999Z \
    --out   ./transcripts_august \
    --token-file token.txt \
    --workers 5
```

`--start`/`--end` are **UTC** ISO-8601 timestamps. eGain's grid displays
times in **IST (UTC+5:30)**, so to get a clean calendar month in IST you
need to shift the boundaries — e.g. for August 2026 IST:

| IST | UTC (`--start`/`--end`) |
|---|---|
| 1 Aug 00:00:00.000 | 31 Jul 18:30:00.000 |
| 31 Aug 23:59:59.999 | 31 Aug 18:29:59.999 |

Using the naive `2026-08-01T00:00:00.000Z` → `2026-08-31T23:59:59.000Z`
instead would clip off the last ~5.5 hours of 31 July (IST) and pull in the
first ~5.5 hours of 1 September (IST). `run_instruction.txt` has this
worked example ready to copy for a new month — just adjust the dates,
keeping the 18:30 / 18:29:59.999 UTC offset pattern.

**Flags:**

| Flag | Required | Default | Description |
|---|---|---|---|
| `--start` | yes | — | UTC ISO-8601 start, e.g. `2026-07-01T00:00:00.000Z` |
| `--end` | yes | — | UTC ISO-8601 end |
| `--out` | yes | — | Output directory |
| `--token-file` | no* | — | Path to a text file containing the `Authorization` header value |
| `--workers` | no | `4` | Pages fetched/exported concurrently. Start at 4–6; if you see more "came back empty — retrying" warnings than usual, lower it |
| `--delay` | no | `0.3` | Polite pause (seconds) between a page's listing call and its export call |
| `--page-size` | no | `500` | Rows per page (matches the UI). Larger values are unverified — spot-check against a manual export before trusting them |
| `--api-host` | no | `https://va21-emea.egain.cloud` | Override for a different eGain customer/department (see below) |
| `--session-type-id` | no | `156` | Override for a different eGain customer/department (see below) |
| `--limit` | no | — | Testing only: export just the first N sessions from page 1 |
| `--order` | no | `desc` | Testing only, used with `--limit` |

\* Alternatively set the `EGAIN_AUTH_TOKEN` environment variable instead of
`--token-file`.

**Output**, in `--out`:

- `transcripts_page_001.csv`, `transcripts_page_002.csv`, … — up to 500
  rows each, same columns as eGain's own manual "Transcripts" export:
  `SID, dialog, intent, score, utterance, created, num_custq, chat_status,
  escalation_offered, solution_presented, warm_transfer_offered,
  chat_offer_accepted, chat_offer_rejected, abandoned, error,
  quality_interaction, self_served, medium_confirmed, referrer_url`
- `transcripts_page_asc001.csv`, … — only appears if a **recovery pass**
  ran (see below)
- `summary.json` — run metadata: totals, page/worker counts, which pages
  (if any) failed or needed recovery

### 2. Combine the pages into one spreadsheet

```bash
cd "/path/to/Script"
python3 "Combine transcript/combine_transcripts.py" \
    --dir ./transcripts_august \
    --out ./transcripts_august/transcripts_august_combined.xlsx
```

This reads every `transcripts_page*.csv` in `--dir`, drops rows with a
duplicate `SID` (can happen at the boundary between the normal pages and a
recovery pass), sorts everything by `created` date (newest first, matching
eGain's own export convention), and writes one `.xlsx`.

## How it works

- **Two API calls per page**: `GET .../session/{id}` for the paginated
  session list (SID, intent, score, flag columns, …), then
  `POST .../exchange/transcript` for the actual dialog turns of those
  session IDs.
- **Concurrency**: pages are fetched/exported in parallel across
  `--workers` threads. Each request is deliberately cookie-less/stateless
  (a fresh request every time, no shared `requests.Session`), because
  testing showed that reusing a session/cookie jar across different date
  ranges could cause a *later* request to come back with a correct
  `totalCount` but an empty result — issuing every call statelessly avoids
  that and makes concurrent fetching safe. A lock-protected de-dupe set
  ensures two concurrent pages can never double-count the same session.
- **Exact date range, no sub-splitting**: the script queries your exact
  `--start`/`--end` once and pages through it — an earlier version tried
  splitting wide ranges into sub-windows and that made things *worse*
  (eGain's `totalCount` for arbitrary sub-day windows has been observed to
  be server-side inconsistent). Instead, a page that comes back
  unexpectedly empty is retried with escalating backoff (3s, 6s, 12s, 24s,
  48s) before being given up on.
- **Ascending-order recovery pass**: eGain's descending-order pagination
  has been observed to sometimes fail to reach the *oldest* chunk of a
  range even after retries — but that same chunk is trivially reachable as
  "page 1" when queried in **ascending** order instead (oldest sorts
  first). If anything is still missing after the normal descending pass,
  the script automatically walks ascending pages (sequentially, not
  concurrently — this is the rare fallback path, not the common case)
  until the gap closes or it runs out of pages to try.

## Known field-mapping caveats (already handled, documented for awareness)

- `chat_status` is not present in the API's listing fields at all — every
  sample in the reference export had `"TRUE"`, so it's filled as a
  constant `"TRUE"` for every row. If you ever see it vary in a real
  export, that's worth investigating further.
- `referrer_url` is tracked and written to its own CSV column, but is
  **not** embedded into the `dialog` text (an earlier version did this and
  it was found not to match eGain's real export format).
- Boolean columns are written as uppercase `"TRUE"`/`"FALSE"`, matching the
  most recently verified manual export (earlier samples, checked earlier
  in this project, used lowercase — eGain's own export formatting has not
  been fully stable over time).
- A rare (~0.1% of sessions) duplicate-prefix artifact in the raw API
  response is known but not yet fully handled — see the `build_dialog()`
  docstring in `egain_transcript_downloader.py` for the exact detail if it
  comes up.

## Pointing this at a different eGain customer or department

`--api-host` and `--session-type-id` default to Specsavers' "Domiciliary"
setup, but can be overridden for any other eGain customer, department, or
assistant:

1. Log into that customer's own eGain portal and open **their** Transcripts
   page.
2. DevTools → Network → trigger a request the same way as above.
3. Find a request to `.../egain/va/v1/session/<NUMBER>`. The host part
   (e.g. `https://va21-emea.egain.cloud`) is `--api-host`; the number at
   the end is `--session-type-id`.
4. Get a fresh token the same way as above — **tokens are per-customer**,
   a Specsavers token will not work elsewhere.
5. Run with both overrides:

   ```bash
   python3 egain_transcript_downloader.py \
       --start 2026-07-01T00:00:00.000Z \
       --end   2026-07-31T23:59:59.000Z \
       --out   ./transcripts_customer_x \
       --token-file token_customer_x.txt \
       --api-host https://SOME-OTHER-HOST.egain.cloud \
       --session-type-id 999
   ```

Before trusting the output for a new customer, spot-check: download one
small date range manually from their UI and compare it against this
script's output for the same range (see the field-mapping caveats above —
conventions like `TRUE`/`FALSE` casing have varied even within the same
customer over time, so don't assume a new customer's export will look
identical without checking).

## Troubleshooting

- **`401 Unauthorized`** — your token has expired. Get a fresh one (steps
  above) and re-save `token.txt`.
- **Repeated "came back empty — retrying" warnings** — the API may not
  like the current concurrency level from one token. Lower `--workers`.
- **`rowsWritten` short of `totalCount` at the end, even after recovery** —
  worth a manual spot-check of that date range in the eGain UI; the run's
  `summary.json` records exactly which descending pages failed and which
  ascending pages were used for recovery, which is the place to start
  investigating.
- **`combine_transcripts.py` can't find any CSVs** — check `--dir` points
  at the same `--out` folder you passed to the downloader.
