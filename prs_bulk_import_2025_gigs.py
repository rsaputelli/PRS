"""
PRS Bulk Import: 2025 Gigs from Master Schedule

Modes:
  - extract: build detailed staging CSVs from `PRS Master Schedule.xlsx`
  - load:    read edited staging CSVs and insert into Supabase

Environment:
  - SUPABASE_URL
  - SUPABASE_SERVICE_ROLE_KEY   (recommended for bypassing RLS on backfill)

Requires:
  - pandas
  - openpyxl
  - supabase-py

Usage:
  python prs_bulk_import_2025_gigs.py --mode extract
  # review and edit staging CSVs
  python prs_bulk_import_2025_gigs.py --mode load
"""

import os
import re
import argparse
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import pandas as pd
from supabase import create_client, Client

# -----------------------------
# Constants / Config
# -----------------------------

EXCEL_PATH = "PRS Master Schedule.xlsx"
FULL_GIG_SHEET = "Full Gig Sched"

# Excel row indices (1-based in Excel, convert to 0-based for pandas)
EXCEL_ROW_MUSICIAN_START = 2
EXCEL_ROW_MUSICIAN_END = 50

EXCEL_ROW_SOUNDTECH_START = 51
EXCEL_ROW_SOUNDTECH_END = 54

EXCEL_ROW_AGENT_START = 55
EXCEL_ROW_AGENT_END = 57

EXCEL_ROW_EXTRA_START = 58
EXCEL_ROW_EXTRA_END = 59

EXCEL_ROW_PRS_FEE = 60

YEAR_FILTER = 2025

# Known venue mappings (lowercased keys)
KNOWN_VENUES: Dict[str, str] = {
    "reef": "403915e8-f127-4a88-853d-fb345361bac6",
    "the reef": "403915e8-f127-4a88-853d-fb345361bac6",
    "buck": "afe2e8a6-5e6e-4911-bd65-6be1ce0f591a",
    "buck hotel": "afe2e8a6-5e6e-4911-bd65-6be1ce0f591a",
    "the buck": "afe2e8a6-5e6e-4911-bd65-6be1ce0f591a",
    "creekside": "b8d57056-c26e-42b3-833a-1483aafcfb33",
    "creek side": "b8d57056-c26e-42b3-833a-1483aafcfb33",
    "creekside inn": "b8d57056-c26e-42b3-833a-1483aafcfb33",
}

# Staging file names
STAGING_GIGS = "staging_gigs_2025.csv"
STAGING_GIG_MUSICIANS = "staging_gig_musicians_2025.csv"
STAGING_GIG_SOUNDTECHS = "staging_gig_soundtechs_2025.csv"
STAGING_EXTRA_PAYOUTS = "staging_extra_payouts_2025.csv"


# -----------------------------
# Utility helpers
# -----------------------------

def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def excel_row_to_index(excel_row: int) -> int:
    # Header row is row 1 in Excel; pandas index 0 = Excel row 2
    return excel_row - 2


def parse_gig_header(col_name: str) -> Optional[Tuple[pd.Timestamp, str]]:
    """
    Parse a column header like "7/11/25  Goat" or "09/19/25  Flip Flopz"
    into (date, venue_text). Returns None if it doesn't look like a gig header.
    """
    if not isinstance(col_name, str):
        return None
    s = col_name.strip()
    if not s:
        return None

    # Grab leading date-ish tokens (digits, slash, space)
    m = re.match(r"([0-9/ ]+)", s)
    if not m:
        return None

    date_str = m.group(1).strip()
    venue_part = s[m.end():].strip()

    if not venue_part:
        # no venue text, probably not a gig column
        return None

    try:
        dt = pd.to_datetime(date_str, errors="raise")
    except Exception:
        return None

    # ensure it's the year we care about
    if dt.year != YEAR_FILTER:
        return None

    return dt, venue_part


@dataclass
class MusicianRef:
    id: str
    display_name: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    stage_name: Optional[str]


@dataclass
class SoundTechRef:
    id: str
    display_name: Optional[str]
    company: Optional[str]


def connect_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or ANON_KEY) must be set.")
    return create_client(url, key)


def load_reference_data(sb: Client) -> Tuple[List[MusicianRef], List[SoundTechRef]]:
    # Musicians
    mus_resp = sb.table("musicians").select(
        "id, first_name, last_name, display_name, stage_name"
    ).execute()
    musicians: List[MusicianRef] = []
    for row in mus_resp.data:
        musicians.append(
            MusicianRef(
                id=row["id"],
                display_name=row.get("display_name"),
                first_name=row.get("first_name"),
                last_name=row.get("last_name"),
                stage_name=row.get("stage_name"),
            )
        )

    # Sound techs
    st_resp = sb.table("sound_techs").select(
        "id, display_name, company"
    ).execute()
    soundtechs: List[SoundTechRef] = []
    for row in st_resp.data:
        soundtechs.append(
            SoundTechRef(
                id=row["id"],
                display_name=row.get("display_name"),
                company=row.get("company"),
            )
        )

    return musicians, soundtechs


def normalize_name(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip()).lower()


def match_musician(name_sheet: str, musicians: List[MusicianRef]) -> Tuple[Optional[str], str]:
    """
    Attempt to match sheet musician name to Supabase musicians.
    Returns (musician_id or None, match_confidence: 'exact', 'loose', 'none').
    """
    target = normalize_name(name_sheet)
    if not target:
        return None, "none"

    # exact match against display_name or stage_name
    for m in musicians:
        if normalize_name(m.display_name) == target or normalize_name(m.stage_name) == target:
            return m.id, "exact"

    # try split first/last against musician first+last
    parts = target.split(" ")
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        for m in musicians:
            if normalize_name(m.first_name) == first and normalize_name(m.last_name) == last:
                return m.id, "exact"

    # could add fuzzy logic later; for now, just "none"
    return None, "none"


def match_soundtech(name_sheet: str, company_sheet: str, soundtechs: List[SoundTechRef]) -> Tuple[Optional[str], str]:
    """
    Attempt to match sound tech by display_name or company.
    Returns (soundtech_id or None, match_confidence).
    """
    n = normalize_name(name_sheet)
    c = normalize_name(company_sheet or name_sheet)

    for st in soundtechs:
        if normalize_name(st.display_name) == n or normalize_name(st.company) == c:
            return st.id, "exact"

    return None, "none"


# -----------------------------
# Extract staging
# -----------------------------

def extract_staging():
    print("Loading Excel...")
    df = pd.read_excel(EXCEL_PATH, sheet_name=FULL_GIG_SHEET)

    # Connect to Supabase & load reference data for matching
    print("Connecting to Supabase for reference data...")
    sb = connect_supabase()
    musicians_ref, soundtechs_ref = load_reference_data(sb)

    # Determine row indices (0-based)
    m_start = excel_row_to_index(EXCEL_ROW_MUSICIAN_START)
    m_end = excel_row_to_index(EXCEL_ROW_MUSICIAN_END)
    st_start = excel_row_to_index(EXCEL_ROW_SOUNDTECH_START)
    st_end = excel_row_to_index(EXCEL_ROW_SOUNDTECH_END)
    extra_start = excel_row_to_index(EXCEL_ROW_EXTRA_START)
    extra_end = excel_row_to_index(EXCEL_ROW_EXTRA_END)
    fee_row = excel_row_to_index(EXCEL_ROW_PRS_FEE)

    # Core identifying columns
    col_member = "Member"
    col_instrument = "Instrument" if "Instrument" in df.columns else "Instrument "  # just in case

    gig_columns = []
    parsed_gigs = {}  # col_name -> (date, venue_text)

    print("Scanning columns for 2025 gig headers...")
    for col in df.columns:
        parsed = parse_gig_header(str(col))
        if parsed is None:
            continue
        date, venue_text = parsed
        # quick sanity: column shouldn't be entirely empty in fee row
        if pd.isna(df.at[fee_row, col]):
            continue
        gig_columns.append(col)
        parsed_gigs[col] = (date, venue_text)

    print(f"Found {len(gig_columns)} gig columns for {YEAR_FILTER}.")

    # Build staging_gigs
    gigs_rows = []
    for col in gig_columns:
        date, venue_text = parsed_gigs[col]
        parsed_venue = venue_text.strip()
        fee_val = df.at[fee_row, col] if fee_row in df.index else None

        norm_venue = normalize_name(parsed_venue)
        auto_venue_id = KNOWN_VENUES.get(norm_venue)
        notes = []
        if auto_venue_id:
            notes.append("auto-venue-id")

        # gather sound tech info (rows 51–54)
        sound_names = []
        sound_companies = []
        sound_amounts = []

        for idx in range(st_start, st_end + 1):
            row = df.iloc[idx]
            name_sheet = str(row.get(col_member, "")).strip()
            company_sheet = str(row.get("Name for 1099", "")).strip()
            if not company_sheet:
                company_sheet = name_sheet
            val = row.get(col)
            if pd.isna(val):
                continue
            sound_names.append(name_sheet)
            sound_companies.append(company_sheet)
            sound_amounts.append(val)

        if len(sound_names) > 1:
            notes.append("multiple-soundtechs")
        elif len(sound_names) == 0:
            notes.append("no-soundtech")

        gig_date_str = date.strftime("%Y-%m-%d")
        gig_key = f"{gig_date_str}_{slugify(parsed_venue)}"

        gigs_rows.append({
            "gig_key": gig_key,
            "sheet_column_name": col,
            "gig_date": gig_date_str,
            "parsed_venue_name": parsed_venue,
            "standardized_venue_name": "",  # you will fill this in
            "venue_id_override": auto_venue_id or "",
            "prs_fee_sheet": fee_val,
            "sound_tech_names_sheet": ", ".join(sound_names),
            "sound_tech_companies_sheet": ", ".join(sound_companies),
            "sound_tech_amounts_sheet": ", ".join(str(x) for x in sound_amounts),
            "notes": "; ".join(notes),
        })

    gigs_df = pd.DataFrame(gigs_rows).sort_values(["gig_date", "parsed_venue_name"])
    gigs_df.to_csv(STAGING_GIGS, index=False)
    print(f"Wrote {STAGING_GIGS} ({len(gigs_df)} rows).")

    # Build staging_gig_musicians
    mus_rows = []
    for col in gig_columns:
        date, venue_text = parsed_gigs[col]
        gig_date_str = date.strftime("%Y-%m-%d")
        parsed_venue = venue_text.strip()
        gig_key = f"{gig_date_str}_{slugify(parsed_venue)}"

        for idx in range(m_start, m_end + 1):
            row = df.iloc[idx]
            name_sheet = str(row.get(col_member, "")).strip()
            if not name_sheet:
                continue
            val = row.get(col)
            if pd.isna(val):
                continue
            role = str(row.get(col_instrument, "")).strip()
            amount = val

            musician_id, confidence = match_musician(name_sheet, musicians_ref)
            needs_review = confidence == "none"

            mus_rows.append({
                "gig_key": gig_key,
                "sheet_column_name": col,
                "gig_date": gig_date_str,
                "parsed_venue_name": parsed_venue,
                "musician_name_sheet": name_sheet,
                "role_sheet": role,
                "amount_paid_sheet": amount,
                "musician_id_supabase": musician_id or "",
                "match_confidence": confidence,
                "needs_review": needs_review,
            })

    mus_df = pd.DataFrame(mus_rows)
    mus_df.to_csv(STAGING_GIG_MUSICIANS, index=False)
    print(f"Wrote {STAGING_GIG_MUSICIANS} ({len(mus_df)} rows).")

    # Build staging_gig_soundtechs
    st_rows = []
    for col in gig_columns:
        date, venue_text = parsed_gigs[col]
        gig_date_str = date.strftime("%Y-%m-%d")
        parsed_venue = venue_text.strip()
        gig_key = f"{gig_date_str}_{slugify(parsed_venue)}"

        for idx in range(st_start, st_end + 1):
            row = df.iloc[idx]
            name_sheet = str(row.get(col_member, "")).strip()
            company_sheet = str(row.get("Name for 1099", "")).strip()
            if not company_sheet:
                company_sheet = name_sheet
            val = row.get(col)
            if pd.isna(val):
                continue

            st_id, confidence = match_soundtech(name_sheet, company_sheet, soundtechs_ref)
            needs_review = confidence == "none"

            st_rows.append({
                "gig_key": gig_key,
                "sheet_column_name": col,
                "gig_date": gig_date_str,
                "parsed_venue_name": parsed_venue,
                "soundtech_name_sheet": name_sheet,
                "soundtech_company_sheet": company_sheet,
                "amount_paid_sheet": val,
                "soundtech_id_supabase": st_id or "",
                "match_confidence": confidence,
                "needs_review": needs_review,
            })

    st_df = pd.DataFrame(st_rows)
    st_df.to_csv(STAGING_GIG_SOUNDTECHS, index=False)
    print(f"Wrote {STAGING_GIG_SOUNDTECHS} ({len(st_df)} rows).")

    # Extra payouts (rows 55–59), purely for reference for now
    extra_rows = []
    for col in gig_columns:
        date, venue_text = parsed_gigs[col]
        gig_date_str = date.strftime("%Y-%m-%d")
        parsed_venue = venue_text.strip()
        gig_key = f"{gig_date_str}_{slugify(parsed_venue)}"

        for idx in range(extra_start, extra_end + 1):
            row = df.iloc[idx]
            name_sheet = str(row.get(col_member, "")).strip()
            val = row.get(col)
            if pd.isna(val) and not name_sheet:
                continue

            extra_rows.append({
                "gig_key": gig_key,
                "sheet_column_name": col,
                "gig_date": gig_date_str,
                "parsed_venue_name": parsed_venue,
                "row_index": idx,
                "label_sheet": name_sheet,
                "amount_sheet": val,
            })

    extra_df = pd.DataFrame(extra_rows)
    extra_df.to_csv(STAGING_EXTRA_PAYOUTS, index=False)
    print(f"Wrote {STAGING_EXTRA_PAYOUTS} ({len(extra_df)} rows).")

    print("\nStaging extraction complete.")
    print("Next: review/edit the staging CSVs, then run with --mode load.")


# -----------------------------
# Load into Supabase
# -----------------------------

def load_from_staging():
    sb = connect_supabase()
    print("Loading staging CSVs...")
    gigs_df = pd.read_csv(STAGING_GIGS)
    mus_df = pd.read_csv(STAGING_GIG_MUSICIANS)
    st_df = pd.read_csv(STAGING_GIG_SOUNDTECHS)

    # Build venue cache: name -> id
    venue_cache: Dict[str, str] = {}

    def get_or_create_venue(standardized_name: str, override_id: str) -> str:
        standardized_name_norm = standardized_name.strip()
        if override_id:
            return override_id
        if not standardized_name_norm:
            raise RuntimeError(f"Missing standardized_venue_name for a gig; please fill in {STAGING_GIGS}.")

        key = standardized_name_norm.lower()
        if key in venue_cache:
            return venue_cache[key]

        # Try lookup by name
        resp = sb.table("venues").select("id").eq("name", standardized_name_norm).execute()
        if resp.data:
            vid = resp.data[0]["id"]
            venue_cache[key] = vid
            return vid

        # Create new venue
        create_resp = sb.table("venues").insert({
            "name": standardized_name_norm
        }).execute()
        vid = create_resp.data[0]["id"]
        venue_cache[key] = vid
        print(f"Created new venue '{standardized_name_norm}' -> {vid}")
        return vid

    # Create gigs and map gig_key -> gig_id
    gig_id_map: Dict[str, str] = {}

    print("Inserting gigs...")
    for _, row in gigs_df.iterrows():
        gig_key = row["gig_key"]
        if gig_key in gig_id_map:
            continue

        gig_date = row["gig_date"]
        parsed_venue_name = row["parsed_venue_name"]
        standardized_venue_name = row.get("standardized_venue_name", "") or parsed_venue_name
        venue_id_override = row.get("venue_id_override", "") or ""

        venue_id = get_or_create_venue(standardized_venue_name, venue_id_override)

        fee_val = row.get("prs_fee_sheet", None)
        try:
            fee_numeric = float(fee_val) if pd.notna(fee_val) else None
        except Exception:
            fee_numeric = None

        # Determine sound tech id (take first non-empty from soundtech staging for this gig)
        st_rows = st_df[st_df["gig_key"] == gig_key]
        sound_tech_id = None
        for _, st_row in st_rows.iterrows():
            candidate = st_row.get("soundtech_id_supabase", "")
            if isinstance(candidate, str) and candidate:
                sound_tech_id = candidate
                break

        title = f"PRS at {standardized_venue_name}"

        insert_payload = {
            "event_date": gig_date,
            "venue_id": venue_id,
            "sound_tech_id": sound_tech_id,
            "title": title,
            "fee": fee_numeric,
            "total_fee": fee_numeric,
            "is_test": False,
        }

        resp = sb.table("gigs").insert(insert_payload).execute()
        gig_id = resp.data[0]["id"]
        gig_id_map[gig_key] = gig_id
        print(f"Inserted gig {gig_key} -> {gig_id}")

    print("Inserting gig_musicians links...")
    # Insert gig_musicians
    for _, row in mus_df.iterrows():
        gig_key = row["gig_key"]
        gig_id = gig_id_map.get(gig_key)
        if not gig_id:
            print(f"WARNING: gig_key {gig_key} missing in gig_id_map; skipping musician row.")
            continue

        musician_id = row.get("musician_id_supabase", "")
        if not isinstance(musician_id, str) or not musician_id:
            # unresolved; you can choose to skip or raise
            print(f"WARNING: unresolved musician for {gig_key} / {row['musician_name_sheet']}; skipping.")
            continue

        role = row.get("role_sheet", "")
        insert_payload = {
            "gig_id": gig_id,
            "musician_id": musician_id,
            "role": role,
        }
        sb.table("gig_musicians").insert(insert_payload).execute()

    print("Load complete. No emails were sent; only direct inserts into gigs and gig_musicians.")


# -----------------------------
# Main
# -----------------------------

def main():
    parser = argparse.ArgumentParser(description="PRS Bulk Import 2025 Gigs")
    parser.add_argument("--mode", choices=["extract", "load"], required=True,
                        help="extract: build staging CSVs; load: read staging CSVs and insert into Supabase")
    args = parser.parse_args()

    if args.mode == "extract":
        extract_staging()
    elif args.mode == "load":
        load_from_staging()


if __name__ == "__main__":
    main()
