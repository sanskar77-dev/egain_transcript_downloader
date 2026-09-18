"""
eGain AI — direct-API transcript downloader (Specsavers / Domiciliary bot-sessions)

Replaces the manual "select all -> Export -> next page" UI loop with two direct
API calls per page, then reconstructs the SAME merged CSV format eGain's own
"Transcripts" export button produces (one row per session: SID, flattened
dialog, intent/score, and all the true/false flag columns):

  1. GET  {API_HOST}/egain/va/v1/session/{SESSION_TYPE_ID}
         -> paginated list of session summaries (SID, intent, score, flags, etc.)
  2. POST {API_HOST}/egain/va/v1/exchange/transcript
         -> full transcript (ask/response turns) for a given list of session ids

Both endpoints require an `Authorization` header copied from an active,
logged-in browser session — this script does NOT log in for you and does not
store a password anywhere. See "HOW TO GET THE TOKEN" below.

------------------------------------------------------------------------------
PERFORMANCE (v2): this version adds concurrent page fetching.

The original version fetched pages strictly one at a time (listing call ->
export call -> write CSV -> sleep -> next page), which is safe but slow for
wide date ranges with many pages. This version fetches/export multiple pages
IN PARALLEL using a thread pool, controlled by --workers (default 4).

This is safe to parallelize because:
  - The listing GET calls were already made cookie-less/stateless per call
    (see fetch_listing_page's docstring) specifically so that different
    requests don't interfere with each other via shared session state.
  - The export POST call has been changed in this version to ALSO be a
    fresh, cookie-less request per call (previously it reused a shared
    requests.Session), for the same reason: no shared mutable state between
    concurrent requests.
  - The only shared, mutable state across pages (the `seen_sids` de-dupe
    set and the running row count) is now protected by a lock.

The ascending-order RECOVERY pass (the rare fallback used when descending
pagination doesn't reach every row) is deliberately left SEQUENTIAL, not
parallelized — it's an edge-case correctness path, not the common case, and
keeping it simple matters more there than shaving a few seconds off it.

Tuning:
  --workers N   How many pages to fetch/export concurrently (default 4).
                Start around 4-6. If the run logs start showing repeated
                "came back empty — retrying" warnings that didn't happen
                before, that's a sign the API doesn't like this many
                concurrent requests from one token — lower --workers.
  --delay S     Politeness pause (seconds) between a page's own listing call
                and its own export call (default 0.3). This does NOT add up
                across pages anymore since pages run concurrently — it only
                paces the two calls *within* one page's processing.
  --page-size N Rows per page requested from the listing endpoint (default
                500, matching eGain's own UI page size). Larger values mean
                fewer total pages/requests, which is also faster — but this
                has NOT been verified against a manual export the way 500
                has. If you try a larger value, spot-check the output
                against a manual UI export for a small date range before
                trusting it for a real run.

------------------------------------------------------------------------------
HOW TO GET THE TOKEN (do this yourself, in your own browser — do not share it
with anyone, treat it like a password):

  1. Log into https://egain-cloud.specsavers.co.uk and open the Transcripts
     page (Domiciliary bot-sessions), same as normal.
  2. Open Chrome DevTools (F12) -> Network tab.
  3. Change page or click "Transcripts" (export) once, to trigger a request.
  4. Click on a request to ".../egain/va/v1/session/156" or
     ".../egain/va/v1/exchange/transcript" in the Network list.
  5. In the "Headers" panel, find the request header named "Authorization"
     — right-click directly on it and choose "Copy value" (don't drag-select
     across the wrapped lines by hand, it's easy to miss a character).
  6. Paste that whole value into a new plain text file, e.g. token.txt, and
     save it. That's it — no terminal quoting to fight with.

------------------------------------------------------------------------------
HOW TO POINT THIS AT A DIFFERENT CUSTOMER (or a different department/assistant
within the same customer):

  This script defaults to Specsavers' "Domiciliary" (specsavers_va) setup, but
  --api-host and --session-type-id override that for any other eGain customer.
  Find both the same way the originals were found:

  1. Log into that customer's own eGain portal and open THEIR Transcripts page.
  2. Open Chrome DevTools (F12) -> Network tab, then trigger a request the
     same way as step 3 above.
  3. Find a request to ".../egain/va/v1/session/<SOME NUMBER>" in the Network
     list. The part before "/egain/va/..." (e.g. "https://va21-emea.egain.cloud")
     is the --api-host value; the number at the end is the --session-type-id
     value.
  4. Get a fresh Authorization token the same way as above (it's per-customer
     too — a Specsavers token will NOT work against a different customer).
  5. Run with both overrides, e.g.:

       python3 egain_transcript_downloader.py \\
           --start 2026-07-01T00:00:00.000Z \\
           --end   2026-07-31T23:59:59.000Z \\
           --out   ./transcripts_customer_x \\
           --token-file token_customer_x.txt \\
           --api-host https://SOME-OTHER-HOST.egain.cloud \\
           --session-type-id 999

  Before trusting the output for a new customer, do the same sanity check we
  did for Specsavers: download one small date range manually from their UI
  and compare it against this script's output for the same range (see the
  NOTE ON FIELD MAPPING below — some conventions, like TRUE/FALSE casing,
  turned out to vary even across different exports from the SAME customer, so
  don't assume a different customer's export will look identical either).

  This token is tied to your login session and WILL expire, possibly
  quite quickly. When the script starts failing with 401 errors, repeat the
  steps above to get a fresh token and re-save token.txt.
------------------------------------------------------------------------------

USAGE (recommended — token from a file, no shell quoting involved)

    python3 egain_transcript_downloader.py \\
        --start 2026-07-01T00:00:00.000Z \\
        --end   2026-07-31T23:59:59.000Z \\
        --out   ./transcripts_july \\
        --token-file token.txt \\
        --workers 5

USAGE (alternative — environment variable)

    export EGAIN_AUTH_TOKEN="Bearer eyJ..."
    python3 egain_transcript_downloader.py \\
        --start 2026-07-01T00:00:00.000Z \\
        --end   2026-07-31T23:59:59.000Z \\
        --out   ./transcripts_july

Output: one CSV file per page (<= 500 rows each) in --out
(transcripts_page_001.csv, transcripts_page_002.csv, ...), with the same
columns as eGain's own "Transcripts" export button: SID, dialog, intent,
score, utterance, created, num_custq, chat_status, escalation_offered,
solution_presented, warm_transfer_offered, chat_offer_accepted,
chat_offer_rejected, abandoned, error, quality_interaction, self_served,
medium_confirmed, referrer_url. Plus a summary.json with run metadata.

NOTE ON PAGINATION: this script queries your EXACT given (start, end) range
ONCE and pages through it with $pagenum — it does not invent narrower
sub-ranges. An earlier version tried recursively splitting the date range
into sub-windows to avoid deep $pagenum values; testing proved that made
things WORSE, because eGain's totalCount for arbitrary sub-day windows is
genuinely inconsistent server-side (verified: a day's morning-half window
reported totalCount=0 while its afternoon-half reported a real count and the
full day's total didn't match the sum — confirmed even as the very first
request of a fresh run, with independently double-checked date-math). Every
new date range queried is a new chance to hit that inconsistency, so this
version deliberately queries only ONE range (the one you asked for) and
instead retries a page that comes back unexpectedly empty several times with
increasing delays before giving up on it, on the theory that occasional
empty pages are transient server-side lag rather than a permanent gap.

NOTE ON FIELD MAPPING: most columns map directly to fields eGain's listing
API returns for each session (intent <- classifiedIntent, score <-
classifiedScore, etc.) and were verified against a real eGain-exported CSV.
One exception: `chat_status` is not present in the listing API's fields at
all — every sample row in the reference export had it as "TRUE", so this
script fills it as a constant "TRUE" for every row. If you ever see mixed
chat_status values in a real export, flag it and we'll dig into where that
comes from.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

# Defaults match the Specsavers "Domiciliary" (specsavers_va) setup this script was
# originally built for. Both are now overridable via --api-host / --session-type-id
# so this same script can be pointed at a different eGain customer/department/
# assistant without editing code — see "HOW TO POINT THIS AT A DIFFERENT CUSTOMER" above.
DEFAULT_API_HOST = "https://va21-emea.egain.cloud"
DEFAULT_SESSION_TYPE_ID = 156
DEFAULT_PAGE_SIZE = 500
DEFAULT_REQUEST_DELAY_SECONDS = 0.3  # polite pause between a page's listing & export call
DEFAULT_WORKERS = 4

CSV_COLUMNS = [
    "SID", "dialog", "intent", "score", "utterance", "created", "num_custq",
    "chat_status", "escalation_offered", "solution_presented",
    "warm_transfer_offered", "chat_offer_accepted", "chat_offer_rejected",
    "abandoned", "error", "quality_interaction", "self_served",
    "medium_confirmed", "referrer_url",
]

print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)


def get_auth_header(token_file):
    """
    Prefer reading the token from a plain text file (no shell-quoting risk at
    all — just paste and save). Falls back to the EGAIN_AUTH_TOKEN env var
    for anyone who prefers that route.
    """
    if token_file:
        path = Path(token_file)
        if not path.exists():
            sys.exit(f"ERROR: token file not found: {path}")
        token = path.read_text().strip()
        if not token:
            sys.exit(f"ERROR: token file is empty: {path}")
        return token

    token = os.environ.get("EGAIN_AUTH_TOKEN")
    if token:
        return token.strip()

    sys.exit(
        "ERROR: no token provided. Either pass --token-file pointing at a text "
        "file containing the Authorization header value, or set the "
        "EGAIN_AUTH_TOKEN environment variable "
        "(see the HOW TO GET THE TOKEN instructions at the top of this script)."
    )


_DEBUG_LISTING = os.environ.get("EGAIN_DEBUG") == "1"


def fetch_listing_page(auth_header, pagenum, start, end, order="desc",
                        api_host=DEFAULT_API_HOST, session_type_id=DEFAULT_SESSION_TYPE_ID,
                        page_size=DEFAULT_PAGE_SIZE):
    """
    Deliberately issues a fresh, cookie-less request every time (no shared
    requests.Session) rather than reusing one across calls.

    Why: testing showed a request for one (start, end) range returns real
    `Session` data, but a DIFFERENT (start, end) range requested afterward
    (same token, same requests.Session/cookie-jar) comes back with a correct
    `totalCount` but an EMPTY `Session` array — even though that same range
    works fine as the very first request of a fresh run. That's consistent
    with eGain's backend keying actual result data off server-side search/
    session state (likely via a cookie set on the first response) rather
    than purely off the start/end query params — so carrying cookies forward
    across different date ranges silently breaks later ones. Issuing every
    listing call with a clean cookie jar avoids that entirely, and as a nice
    side effect makes these calls safe to fire concurrently from a thread
    pool (no shared, mutable client state between requests).
    """
    params = {
        "$sort": "TimeStamp",
        "$order": order,
        "$pagenum": pagenum,
        "$pagesize": page_size,
        "start": start,
        "end": end,
    }
    url = f"{api_host}/egain/va/v1/session/{session_type_id}?{urlencode(params)}"
    resp = requests.get(url, headers={"Authorization": auth_header, "Accept": "application/json"})
    if _DEBUG_LISTING:
        safe_print(f"    [debug] GET {start}..{end} pagenum={pagenum} order={order} -> "
                   f"status={resp.status_code} set-cookie={resp.headers.get('Set-Cookie')!r} "
                   f"resp-cookies={dict(resp.cookies)!r}")
    if resp.status_code == 401:
        sys.exit(
            "ERROR: got 401 Unauthorized from the listing endpoint — your token has "
            "expired. Grab a fresh one (see HOW TO GET THE TOKEN) and re-run."
        )
    resp.raise_for_status()
    return resp.json()


def export_transcripts(auth_header, ids, start, end, retries=3, api_host=DEFAULT_API_HOST):
    """
    Also issues a fresh, cookie-less request every call now (previously this
    reused a shared requests.Session across every export call in the run).
    The Authorization header alone carries auth, so no session/cookie state
    was actually needed here — making this stateless too means concurrent
    export calls from different pages can never interfere with each other.
    """
    url = f"{api_host}/egain/va/v1/exchange/transcript"
    body = {"ids": ids, "start": start, "end": end}
    last_resp = None
    for attempt in range(1, retries + 1):
        resp = requests.post(
            url,
            headers={
                "Authorization": auth_header,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=json.dumps(body),
        )
        if resp.status_code == 401:
            sys.exit(
                "ERROR: got 401 Unauthorized from the export endpoint — your token has "
                "expired. Grab a fresh one (see HOW TO GET THE TOKEN) and re-run."
            )
        if resp.status_code >= 500 and attempt < retries:
            wait = 2 * attempt
            safe_print(f"    got {resp.status_code} from server, retrying in {wait}s (attempt {attempt}/{retries})...")
            time.sleep(wait)
            last_resp = resp
            continue
        last_resp = resp
        break
    last_resp.raise_for_status()
    return last_resp.json()


def parse_ask(ask_raw):
    """
    Returns (is_system_event, value). eGain encodes the session-open/close
    events as a JSON string in the `ask` field (e.g. {"type":"reporting",
    "referrer_url":"NA"}); ordinary user turns have `ask` as plain text.
    """
    if isinstance(ask_raw, dict):
        return True, ask_raw
    try:
        parsed = json.loads(ask_raw)
        if isinstance(parsed, dict):
            return True, parsed
    except (TypeError, ValueError):
        pass
    return False, ask_raw


_SESSION_EVENT_PREFIX = "[cust] session event -> [bot] "


def build_dialog(exchange_entries):
    """
    Reconstructs the same "[cust] X -> [bot] Y -> [cust] ... " flattened
    dialog string eGain's own CSV export produces, and pulls out referrer_url
    from the session's opening event (used for the separate `referrer_url`
    CSV column — NOT embedded into the dialog text itself).

    IMPORTANT: an earlier version of this function also appended a trailing
    "<!-- type:...,referrer_url:... -->" HTML comment onto the first system
    turn's response, believing that matched eGain's own export. Direct
    verification against a real, current manual export (transcripts_57.csv,
    2,138 rows) showed this was wrong: 2,135 of 2,138 real dialogs have NO
    such comment at all. So that comment is no longer added — referrer_url
    is still tracked and returned separately for the CSV column, just not
    written into the dialog text.

    Defensive fix: when queried with a wide date range, eGain's export API
    has been observed to occasionally hand back `response` with this exact
    dialog-line prefix already baked into it, producing a visible duplicate
    ("[cust] session event -> [bot] [cust] session event -> [bot] ...").
    Checked against a real manual export (500 rows) that this baked-in
    prefix never legitimately appears inside `response` — so it's always
    safe to strip before we add our own. (Known remaining edge case, rare:
    a shorter variant of this same baked-in-duplicate behavior — missing the
    "-> [bot] " suffix — has been observed in ~0.1% of real sessions and
    isn't caught by this exact-prefix check; fixing that would need the raw
    API response for an affected session to confirm the real shape first.)
    """
    pieces = []
    referrer_url = "NA"
    referrer_url_set = False
    for entry in exchange_entries:
        is_system, parsed = parse_ask(entry.get("ask", ""))
        response = entry.get("response") or ""
        if is_system:
            cust_text = "session event"
            while response.startswith(_SESSION_EVENT_PREFIX):
                response = response[len(_SESSION_EVENT_PREFIX):]
            if not referrer_url_set:
                ref = parsed.get("referrer_url") or "NA"
                referrer_url = ref
                referrer_url_set = True
            piece = f"[cust] {cust_text} -> [bot] {response}"
        else:
            piece = f"[cust] {parsed} -> [bot] {response}"
        pieces.append(piece)
    return " -> ".join(pieces), referrer_url


IST = timezone(timedelta(hours=5, minutes=30))


def format_date(ts):
    """
    Handles either shape eGain's API might hand back for a timestamp:
      - epoch milliseconds, as a number or numeric string (e.g. 1786700000000)
      - an ISO 8601 string (e.g. '2026-08-01T09:21:10.865Z')
    Always returns 'DD/MM/YYYY' in IST (matching what the eGain grid itself
    displays — confirmed by comparing the grid's shown date/time against the
    raw UTC values its own API calls used). Naively taking the UTC calendar
    date is wrong for anything within ~5.5 hours of midnight IST — e.g. a
    session at 00:30 IST on Aug 1st is still "Jul 31" in UTC, which is
    exactly the bug that showed up on page 47 (the oldest, boundary-adjacent
    page of a range starting at midnight IST).
    """
    if ts is None or ts == "":
        return ""

    # epoch milliseconds (number, or a numeric string — e.g. Excel/Sheets
    # rendering a raw 13-digit millisecond timestamp as "1.7867E+12" is what
    # tipped us off that this shape shows up here)
    numeric = None
    if isinstance(ts, (int, float)):
        numeric = ts
    elif isinstance(ts, str) and ts.strip().lstrip("-").isdigit() and len(ts.strip()) >= 12:
        numeric = int(ts)
    if numeric is not None:
        try:
            dt = datetime.fromtimestamp(numeric / 1000, tz=timezone.utc).astimezone(IST)
            return dt.strftime("%d/%m/%Y")
        except (ValueError, OverflowError, OSError):
            return str(ts)

    # ISO 8601 string
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST)
        return dt.strftime("%d/%m/%Y")
    except (ValueError, AttributeError):
        return ts


def bool_str(value):
    # Uppercase to match eGain's own current manual export convention —
    # verified directly against a real manual export (transcripts_57.csv,
    # 2,138 rows) where every one of these flag columns used "TRUE"/"FALSE",
    # not lowercase. (Note: an earlier manual export sample, checked much
    # earlier in this project, showed lowercase for these same columns — so
    # eGain's own export formatting isn't fully stable over time. This
    # matches the most recent, most relevant sample.)
    return "TRUE" if value else "FALSE"


def _sort_key(created):
    """
    Normalizes a 'created' value (epoch ms number/string OR ISO 8601 string)
    into something consistently comparable, so sorting a session's turns
    doesn't crash or misorder if the API mixes formats.
    """
    if created is None or created == "":
        return 0.0
    if isinstance(created, (int, float)):
        return float(created)
    if isinstance(created, str) and created.strip().lstrip("-").isdigit() and len(created.strip()) >= 12:
        return float(created)
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def build_rows(sessions, exchange_list):
    by_sid = defaultdict(list)
    for e in exchange_list:
        by_sid[str(e.get("sid"))].append(e)
    for entries in by_sid.values():
        entries.sort(key=lambda e: _sort_key(e.get("created")))

    rows = []
    for s in sessions:
        entries = by_sid.get(str(s.get("id")), [])
        dialog, referrer_url = build_dialog(entries)
        rows.append({
            "SID": s.get("SID", ""),
            "dialog": dialog,
            "intent": s.get("classifiedIntent") or "",
            "score": s.get("classifiedScore") or "",
            "utterance": s.get("utterance") or "",
            "created": format_date(s.get("timeStamp")),
            "num_custq": s.get("numberInteractions", ""),
            "chat_status": "TRUE",  # see NOTE ON FIELD MAPPING at top of file
            "escalation_offered": bool_str(s.get("escalationOffered")),
            "solution_presented": bool_str(s.get("solutionPresented")),
            "warm_transfer_offered": bool_str(s.get("warmTransferOffered")),
            "chat_offer_accepted": bool_str(s.get("chatOfferAccepted")),
            "chat_offer_rejected": bool_str(s.get("chatOfferRejected")),
            "abandoned": bool_str(s.get("abandoned")),
            "error": bool_str(s.get("error")),
            "quality_interaction": bool_str(s.get("qualityInteraction")),
            "self_served": bool_str(s.get("selfServed")),
            "medium_confirmed": bool_str(s.get("mediumConfirmed")),
            "referrer_url": referrer_url,
        })
    return rows


def fetch_listing_with_retry(auth_header, pagenum, start, end, total_count, order="desc",
                              api_host=DEFAULT_API_HOST, session_type_id=DEFAULT_SESSION_TYPE_ID,
                              page_size=DEFAULT_PAGE_SIZE, cached_listing=None):
    """
    Fetches one $pagenum of the listing endpoint. If it comes back
    unexpectedly empty, retries the SAME (start, end, pagenum, order) several
    times with escalating delays rather than giving up after one try, or
    inventing a different date range (a different date range was tried
    before and made things worse — see the NOTE ON PAGINATION at the top of
    the file). Returns (sessions, recovered) where recovered is True if it
    took more than the first try.

    `cached_listing`: if the caller already fetched this exact page (e.g.
    page 1, fetched once up front to learn totalCount), pass that response
    here to skip the redundant network call.
    """
    retry_delays = [3, 6, 12, 24, 48]  # seconds; escalating backoff
    listing = cached_listing if cached_listing is not None else fetch_listing_page(
        auth_header, pagenum, start, end, order=order,
        api_host=api_host, session_type_id=session_type_id, page_size=page_size)
    sessions = listing.get("Session", [])
    page_total = listing.get("totalCount")
    if page_total != total_count:
        safe_print(f"    WARNING: page {pagenum} (order={order})'s totalCount ({page_total}) "
                   f"differs from the original ({total_count})")

    if sessions:
        return sessions, False

    safe_print(f"    page {pagenum} (order={order}): came back empty — retrying with backoff "
               f"(same exact range/page/order) rather than giving up immediately...")
    for attempt, delay in enumerate(retry_delays, start=1):
        time.sleep(delay)
        safe_print(f"    retry {attempt}/{len(retry_delays)} for page {pagenum} order={order} (waited {delay}s)...")
        listing = fetch_listing_page(auth_header, pagenum, start, end, order=order,
                                      api_host=api_host, session_type_id=session_type_id, page_size=page_size)
        sessions = listing.get("Session", [])
        if sessions:
            safe_print(f"    page {pagenum} (order={order}): recovered on retry {attempt} ({len(sessions)} rows)")
            return sessions, True

    safe_print(f"    WARNING: page {pagenum} still empty after {len(retry_delays)} retries — giving up on this page")
    return [], True


class SharedState:
    """Thread-safe de-dupe set + running row count, shared across the worker pool."""

    def __init__(self):
        self.lock = threading.Lock()
        self.seen_sids = set()
        self.rows_written_total = 0

    def claim_new(self, sessions):
        """Returns only the sessions whose id hasn't been claimed by another
        page yet, and marks them claimed — atomically, so two concurrent
        pages can never both export (and double-count) the same session."""
        with self.lock:
            new_sessions = [s for s in sessions if s.get("id") not in self.seen_sids]
            for s in new_sessions:
                self.seen_sids.add(s.get("id"))
            return new_sessions

    def add_rows(self, n):
        with self.lock:
            self.rows_written_total += n
            return self.rows_written_total


def process_page(pagenum, order, args, auth_header, total_count, out_dir, state,
                  cached_listing=None, file_suffix=""):
    """
    Full pipeline for one page: fetch listing (with retry) -> dedupe against
    everything already claimed by other (possibly concurrently running)
    pages -> export transcripts -> write CSV. Safe to run from multiple
    threads at once (see SharedState and the module docstring's PERFORMANCE
    section for why).
    """
    sessions, _recovered = fetch_listing_with_retry(
        auth_header, pagenum, args.start, args.end, total_count, order=order,
        api_host=args.api_host, session_type_id=args.session_type_id,
        page_size=args.page_size, cached_listing=cached_listing)

    if not sessions:
        return {"pagenum": pagenum, "order": order, "rows": 0, "failed": True}

    new_sessions = state.claim_new(sessions)
    if not new_sessions:
        safe_print(f"  page {pagenum} ({order}): all {len(sessions)} row(s) already collected, skipping")
        return {"pagenum": pagenum, "order": order, "rows": 0, "failed": False}

    ids = [s["id"] for s in new_sessions]
    time.sleep(args.delay)
    safe_print(f"  page {pagenum} ({order}): exporting {len(ids)} transcripts...")
    transcript_data = export_transcripts(auth_header, ids, args.start, args.end, api_host=args.api_host)
    exchange_list = transcript_data.get("exchange", [])

    rows = build_rows(new_sessions, exchange_list)

    out_file = out_dir / f"transcripts_page_{file_suffix}{pagenum:03d}.csv"
    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    total_so_far = state.add_rows(len(rows))
    safe_print(f"  page {pagenum} ({order}): wrote {out_file} ({len(rows)} rows; {total_so_far}/{total_count} so far)")
    return {"pagenum": pagenum, "order": order, "rows": len(rows), "failed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="ISO 8601 UTC start, e.g. 2026-07-01T00:00:00.000Z")
    parser.add_argument("--end", required=True, help="ISO 8601 UTC end, e.g. 2026-07-31T23:59:59.000Z")
    parser.add_argument("--out", required=True, help="Output directory for downloaded transcript files")
    parser.add_argument(
        "--token-file",
        help="Path to a plain text file containing the Authorization header value "
        "(e.g. 'Bearer eyJ...'). Recommended over EGAIN_AUTH_TOKEN — no shell "
        "quoting to worry about, just paste and save the file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="For testing: only export the first N sessions from page 1, "
        "instead of the full date range. E.g. --limit 1 to compare one "
        "script-downloaded transcript against one manually-downloaded one.",
    )
    parser.add_argument(
        "--order",
        choices=["asc", "desc"],
        default="desc",
        help="For testing: sort order sent to the listing endpoint ($order). "
        "Only meaningful together with --limit right now.",
    )
    parser.add_argument(
        "--api-host",
        default=DEFAULT_API_HOST,
        help=f"eGain API host to use, including https://. Default: {DEFAULT_API_HOST} "
        "(Specsavers' Domiciliary setup). For a different customer, find this by "
        "inspecting their Transcripts page in DevTools — see 'HOW TO POINT THIS AT A "
        "DIFFERENT CUSTOMER' at the top of this file.",
    )
    parser.add_argument(
        "--session-type-id",
        type=int,
        default=DEFAULT_SESSION_TYPE_ID,
        help=f"eGain session type ID to use (the number in .../session/<ID> in "
        f"DevTools). Default: {DEFAULT_SESSION_TYPE_ID} (Specsavers' Domiciliary/"
        "specsavers_va view). Different per customer AND per department/assistant "
        "within the same customer.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"How many pages to fetch/export concurrently (default {DEFAULT_WORKERS}). "
        "Higher = faster, but more load on the API from one token at once. Start "
        "around 4-6; if you see more 'came back empty — retrying' warnings than "
        "before, lower this.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help=f"Polite pause in seconds between a page's own listing call and its own "
        f"export call (default {DEFAULT_REQUEST_DELAY_SECONDS}). Does not add up "
        "across pages since pages run concurrently now.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Rows per page requested from the listing endpoint (default "
        f"{DEFAULT_PAGE_SIZE}, eGain's own UI page size). A larger value means "
        "fewer total pages/requests (faster) but has not been verified the way "
        "500 has — spot-check output against a manual export before trusting it.",
    )
    args = parser.parse_args()

    auth_header = get_auth_header(args.token_file)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.limit:
        print(f"Fetching page 1 (--limit mode, order={args.order}, api_host={args.api_host}, "
              f"session_type_id={args.session_type_id})...")
        first_page = fetch_listing_page(auth_header, 1, args.start, args.end, order=args.order,
                                         api_host=args.api_host, session_type_id=args.session_type_id,
                                         page_size=args.page_size)
        limit_total = first_page.get("totalCount", 0)
        limit_got = len(first_page.get("Session", []))
        print(f"totalCount={limit_total}, Session array length={limit_got}")
        sessions = first_page.get("Session", [])[: args.limit]
        if not sessions:
            print("No sessions found for --limit test.")
            return
        for s in sessions:
            print(f"  selected SID={s.get('SID')} created={s.get('timeStamp')} utterance={s.get('utterance')!r}")
            print("  -> find this exact SID/timestamp in the eGain UI grid to compare against a manual export")
        ids = [s["id"] for s in sessions]
        time.sleep(args.delay)
        transcript_data = export_transcripts(auth_header, ids, args.start, args.end, api_host=args.api_host)
        rows = build_rows(sessions, transcript_data.get("exchange", []))
        out_file = out_dir / "transcripts_part_001.csv"
        with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nDone. Wrote {len(rows)} row(s) to {out_file}")
        return

    print(f"Fetching page 1 to determine total record count... (api_host={args.api_host}, "
          f"session_type_id={args.session_type_id})")
    first_page = fetch_listing_page(auth_header, 1, args.start, args.end,
                                     api_host=args.api_host, session_type_id=args.session_type_id,
                                     page_size=args.page_size)
    total_count = first_page.get("totalCount", 0)
    print(f"totalCount={total_count} row(s) in range {args.start} .. {args.end}")

    summary_path = out_dir / "summary.json"

    if total_count == 0:
        print("No records in range — nothing to download.")
        summary_path.write_text(json.dumps({
            "start": args.start, "end": args.end, "totalCount": 0,
            "rowsWritten": 0, "partsWritten": 0, "outputDir": str(out_dir),
        }, indent=2))
        return

    total_pages = max(1, (total_count + args.page_size - 1) // args.page_size)
    print(f"Paging through {total_pages} page(s) of up to {args.page_size} rows each, "
          f"using {args.workers} concurrent worker(s), using the exact range you gave "
          f"(no invented sub-ranges)...\n")

    state = SharedState()
    failed_pages = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for pagenum in range(1, total_pages + 1):
            cached = first_page if pagenum == 1 else None
            futures[pool.submit(process_page, pagenum, "desc", args, auth_header,
                                 total_count, out_dir, state, cached_listing=cached)] = pagenum
        for future in as_completed(futures):
            result = future.result()
            if result["failed"]:
                failed_pages.append(result["pagenum"])

    rows_written_total = state.rows_written_total
    failed_pages.sort()

    # Recovery pass: eGain's descending ($order=desc) pagination has proven
    # unable to reach the oldest chunk of a date range (its "last page")
    # even with heavy retries — but that exact chunk becomes an easily
    # reachable "page 1" when queried in ASCENDING order instead, since it's
    # the oldest records that sort first. Verified directly: a range short
    # by exactly 500 rows in desc order returned precisely those 500 rows as
    # asc page 1. So if anything is still missing after the desc pass above,
    # walk asc pages (also 1-indexed, also with the same retry-with-backoff)
    # until the gap is closed, de-duplicating by session id against what
    # desc already collected (harmless overlap is possible near the middle).
    #
    # Kept SEQUENTIAL (not parallelized like the pass above): this only runs
    # at all when something is actually missing, so it's the exception path,
    # not the common case — simplicity/correctness matter more here than
    # speed.
    recovery_pages_written = []
    if rows_written_total < total_count:
        print(f"\n{total_count - rows_written_total} row(s) still missing after descending pagination — "
              f"trying ascending order to reach them from the other end...")
        asc_pagenum = 0
        max_asc_pages = total_pages + 2  # small safety margin over the expected page count
        while rows_written_total < total_count and asc_pagenum < max_asc_pages:
            asc_pagenum += 1
            time.sleep(args.delay)
            print(f"  fetching ASCENDING listing page {asc_pagenum}...")
            result = process_page(asc_pagenum, "asc", args, auth_header, total_count, out_dir, state,
                                   file_suffix="asc")
            if result["rows"] == 0 and result["failed"]:
                print(f"  ascending page {asc_pagenum}: empty even after retries — stopping ascending recovery")
                break
            rows_written_total = state.rows_written_total
            if result["rows"] > 0:
                recovery_pages_written.append(asc_pagenum)

    if rows_written_total != total_count:
        missing = total_count - rows_written_total
        print(
            f"\nWARNING: wrote {rows_written_total} rows total, but the listing "
            f"reported totalCount={total_count} ({missing} row(s) short) even after "
            f"the ascending-order recovery pass. Descending page(s) that stayed empty: "
            f"{failed_pages or 'none'}. This is worth a manual spot-check in the eGain "
            f"UI for this date range."
        )
    else:
        extra = f" (including {len(recovery_pages_written)} page(s) recovered via ascending order)" if recovery_pages_written else ""
        print(f"\nAll {total_count} rows accounted for{extra}.")

    summary = {
        "start": args.start,
        "end": args.end,
        "totalCount": total_count,
        "rowsWritten": rows_written_total,
        "totalPages": total_pages,
        "workers": args.workers,
        "pageSize": args.page_size,
        "failedDescendingPages": failed_pages,
        "ascendingRecoveryPages": recovery_pages_written,
        "outputDir": str(out_dir),
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nDone. {rows_written_total}/{total_count} rows written to {out_dir}/ "
          f"({total_pages - len(failed_pages)}/{total_pages} descending page(s) succeeded"
          + (f", {len(recovery_pages_written)} ascending recovery page(s) used" if recovery_pages_written else "")
          + ")")


if __name__ == "__main__":
    main()