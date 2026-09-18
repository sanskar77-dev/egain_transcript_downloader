"""
Combine eGain transcript CSV files into a single Excel file.

Point this at the folder where egain_transcript_downloader.py wrote its
output (the transcripts_page_NNN.csv and transcripts_page_ascNNN.csv files),
and it will:
  1. Find every matching CSV in that folder.
  2. Combine all their rows together.
  3. Drop any duplicate rows (same SID appearing in more than one file —
     this can happen near the boundary between the normal descending-order
     pages and the ascending-order recovery pages).
  4. Sort everything by the "created" date, newest first (matching eGain's
     own manual export convention).
  5. Write the result out as one .xlsx file.

USAGE

    python3 combine_transcripts.py --dir ./test_3_4_aug_final --out combined_transcripts.xlsx

If --dir is omitted, it looks in the current folder. If --out is omitted,
it writes "combined_transcripts.xlsx" in --dir.

REQUIRES: pandas and openpyxl. If you don't have them yet:

    pip3 install pandas openpyxl
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    sys.exit(
        "ERROR: this script needs the 'pandas' and 'openpyxl' packages.\n"
        "Install them with:\n\n    pip3 install pandas openpyxl\n"
    )

CSV_COLUMNS = [
    "SID", "dialog", "intent", "score", "utterance", "created", "num_custq",
    "chat_status", "escalation_offered", "solution_presented",
    "warm_transfer_offered", "chat_offer_accepted", "chat_offer_rejected",
    "abandoned", "error", "quality_interaction", "self_served",
    "medium_confirmed", "referrer_url",
]


def parse_created(value):
    """Parses the DD/MM/YYYY 'created' column for sorting; unparsable/blank
    values sort to the very end rather than crashing the whole sort."""
    try:
        return datetime.strptime(str(value), "%d/%m/%Y")
    except (ValueError, TypeError):
        return datetime.min


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=".", help="Folder containing the transcripts_page*.csv files (default: current folder)")
    parser.add_argument("--out", default=None, help="Output .xlsx path (default: combined_transcripts.xlsx inside --dir)")
    args = parser.parse_args()

    in_dir = Path(args.dir)
    if not in_dir.is_dir():
        sys.exit(f"ERROR: folder not found: {in_dir}")

    out_path = Path(args.out) if args.out else in_dir / "combined_transcripts.xlsx"

    # Matches both transcripts_page_001.csv and transcripts_page_asc003.csv
    csv_files = sorted(in_dir.glob("transcripts_page*.csv"))
    if not csv_files:
        sys.exit(f"ERROR: no transcripts_page*.csv files found in {in_dir}")

    print(f"Found {len(csv_files)} file(s):")
    for f in csv_files:
        print(f"  {f.name}")

    all_rows = []
    seen_sids = set()
    duplicate_count = 0
    per_file_counts = {}

    for f in csv_files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        per_file_counts[f.name] = len(rows)
        for row in rows:
            sid = row.get("SID")
            if sid in seen_sids:
                duplicate_count += 1
                continue
            seen_sids.add(sid)
            # keep only the known columns, in the right order, filling
            # anything unexpectedly missing with an empty string
            all_rows.append({col: row.get(col, "") for col in CSV_COLUMNS})

    print()
    for name, count in per_file_counts.items():
        print(f"  {name}: {count} row(s)")
    print(f"\nTotal rows read: {sum(per_file_counts.values())}")
    print(f"Duplicate SIDs skipped: {duplicate_count}")
    print(f"Unique rows combined: {len(all_rows)}")

    all_rows.sort(key=lambda r: parse_created(r.get("created")), reverse=True)

    df = pd.DataFrame(all_rows, columns=CSV_COLUMNS)
    df.to_excel(out_path, index=False, engine="openpyxl")

    print(f"\nDone. Wrote {len(all_rows)} row(s) to {out_path}")


if __name__ == "__main__":
    main()
