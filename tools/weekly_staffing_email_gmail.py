# tools/weekly_staffing_email_gmail.py
import os, base64, datetime as dt, json
import pandas as pd
import requests
from email.mime.text import MIMEText
from supabase import create_client, Client

# --- Env/Secrets ---
SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

GMAIL_CLIENT_ID     = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
GMAIL_SENDER        = os.environ["GMAIL_SENDER"]

# --- Supabase ---
sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def _sel(table, select="*", eq=None):
    q = sb.table(table).select(select)
    if eq:
        for k, v in eq.items():
            q = q.eq(k, v)
    return pd.DataFrame(q.execute().data or [])

def currency(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return ""

def _normalize_and_sort_by_date(df: pd.DataFrame, candidates=None) -> pd.DataFrame:
    """
    Try to locate a date-like column, parse to date, and sort by it.
    If none found, return df unchanged.
    """
    if df is None or df.empty:
        return df
    if candidates is None:
        candidates = ["event_date", "gig_date", "date", "eventDate"]
    date_col = next((c for c in candidates if c in df.columns), None)
    if date_col:
        try:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.date
        except Exception as e:
            print("DEBUG: date parse error:", e)
        try:
            df = df.sort_values(by=[date_col])
        except Exception as e:
            print("DEBUG: sort error:", e)
    return df

def get_understaffed():
    """
    Load understaffed gigs either from a view (preferred) or compute on the fly.
    Adds a 'missing_roles' column (comma-separated) in both paths.
    Robust to different date column names.
    """
    # --- Preferred path: view ---
    try:
        df = _sel("vw_understaffed_gigs", "*")
    except Exception:
        df = pd.DataFrame()

    if not df.empty:
        print("DEBUG(view): columns =", df.columns.tolist())
        # Normalize/sort by a date-like column
        df = _normalize_and_sort_by_date(df)

        # Compute missing_roles explicitly for gigs in this view
        roles_needed = {
            "Male Vocals", "Female Vocals", "Guitar", "Bass", "Keyboard",
            "Drums", "Saxophone", "Trombone", "Trumpet"
        }
        try:
            gid_list = [r for r in df.get("id", []) if pd.notna(r)]
            assign = _sel("gig_musicians", "gig_id, role, musician_id")
            if not assign.empty and gid_list:
                assign = assign[assign["gig_id"].isin(gid_list)]
                # build missing_roles per gig
                miss_map = {}
                for gid, grp in assign.groupby("gig_id"):
                    have = set(grp["role"].dropna().astype(str))
                    miss = sorted(list(roles_needed - have))
                    miss_map[gid] = ", ".join(miss)
                # for gigs with no assignments, all 9 are missing
                default_all_missing = ", ".join(sorted(list(roles_needed)))
                df["missing_roles"] = df["id"].apply(lambda x: miss_map.get(x, default_all_missing))
            else:
                # no assignments at all → every gig is missing all roles
                df["missing_roles"] = ", ".join(sorted(list(roles_needed)))
        except Exception as e:
            print("DEBUG(view): could not compute missing_roles:", e)
            df["missing_roles"] = ""

        return df

    # --- Fallback: compute from gigs + gig_musicians ---
    gigs = _sel("gigs", "*")
    if gigs.empty:
        return gigs  # nothing to report

    assign = _sel("gig_musicians", "gig_id, role, musician_id")

    roles_needed = {
        "Male Vocals", "Female Vocals", "Guitar", "Bass", "Keyboard",
        "Drums", "Saxophone", "Trombone", "Trumpet"
    }

    out = []
    for _, r in gigs.iterrows():
        gid = r.get("id")
        sub = assign[assign["gig_id"] == gid]
        have = set(sub["role"].dropna().astype(str))
        missing_roles = sorted(list(roles_needed - have))

        sound_ok = (
            (pd.notna(r.get("sound_tech_id")) and str(r.get("sound_tech_id")).strip() != "") or
            (str(r.get("sound_by_venue_name") or "").strip() != "") or
            (str(r.get("sound_by_venue_phone") or "").strip() != "")
        )
        details_ok = (
            pd.notna(r.get("venue_id")) and
            pd.notna(r.get("start_time")) and
            pd.notna(r.get("end_time"))
        )
        fully = (len(missing_roles) == 0) and sound_ok and details_ok
        if not fully:
            out.append({
                "id": gid,
                "event_date": r.get("event_date"),
                "title": r.get("title"),
                "venue_id": r.get("venue_id"),
                "missing_roles": ", ".join(missing_roles),
                "sound_ok": sound_ok,
                "details_ok": details_ok,
            })

    df = pd.DataFrame(out)
    print("DEBUG(fallback): columns =", df.columns.tolist())
    return _normalize_and_sort_by_date(df)


def get_recipients():
    sub = _sel("notif_staffing_subscribers", "email", {"active": True})
    emails = [e for e in sub.get("email", []) if e]
    return emails

def build_html(df: pd.DataFrame) -> str:
    today = dt.date.today().strftime("%b %d, %Y")

    # --- Load venue names from Supabase for lookup ---
    try:
        venues_df = _sel("venues", "id,name,city,state")
    except Exception:
        venues_df = pd.DataFrame()

    venue_lookup = {}
    if not venues_df.empty:
        for _, v in venues_df.iterrows():
            vid = str(v.get("id"))
            name = str(v.get("name") or "").strip()
            city = str(v.get("city") or "").strip()
            state = str(v.get("state") or "").strip()
            label = name
            if city or state:
                label += f" ({city}, {state})".strip()
            venue_lookup[vid] = label or "(unnamed venue)"

    if df.empty:
        return f"<p>All upcoming gigs are fully staffed as of {today}. ✅</p>"

    total_gigs = len(df)
    summary_line = f"<p><b>{total_gigs} gig{'s' if total_gigs != 1 else ''} need attention this week.</b></p>"

    # --- Build HTML table ---
    rows = []
    for _, r in df.iterrows():
        date = r.get("event_date") or r.get("gig_date") or r.get("date") or ""
        title = (r.get("title") or "").strip() or "(untitled)"
        venue_id = str(r.get("venue_id") or "")
        venue = venue_lookup.get(venue_id, "(venue not assigned)")
        missing_roles = str(r.get("missing_roles") or "").strip()
        sound_ok = bool(r.get("sound_ok"))
        details_ok = bool(r.get("details_ok"))

        # Build human-readable "Missing" column
        missing_parts = []
        if missing_roles:
            missing_parts.append(f"Roles: {missing_roles}")
        if not sound_ok:
            missing_parts.append("Sound")
        if not details_ok:
            missing_parts.append("Details")
        missing_txt = ", ".join(missing_parts) if missing_parts else ""

        rows.append(
            f"<tr>"
            f"<td>{date}</td>"
            f"<td>{title}</td>"
            f"<td>{venue}</td>"
            f"<td>{missing_txt}</td>"
            f"</tr>"
        )

    head = "<thead><tr><th>Date</th><th>Title</th><th>Venue</th><th>Missing</th></tr></thead>"
    body = "<tbody>" + "".join(rows) + "</tbody>"

    return (
        f"<p>Gigs not fully staffed as of {today}:</p>"
        f"{summary_line}"
        f"<table border='1' cellpadding='6' cellspacing='0'>{head}{body}</table>"
    )


# --- Gmail send via OAuth refresh token ---
def gmail_access_token() -> str:
    data = {
        "client_id": GMAIL_CLIENT_ID,
        "client_secret": GMAIL_CLIENT_SECRET,
        "refresh_token": GMAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
    r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def gmail_send_html(to_list, subject, html_body):
    """Send HTML email via Gmail API using messages.send (works with gmail.send scope).
       Also prints console feedback for test visibility.
    """
    if not to_list:
        print("⚠️  No recipients specified — email not sent.")
        return

    msg = MIMEText(html_body, "html")
    msg["To"] = ", ".join(to_list)
    msg["From"] = GMAIL_SENDER
    msg["Subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    token = gmail_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # messages.send automatically creates a Sent item; no extra labels needed
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    payload = {"raw": raw}

    print(f"📨 Sending email to {', '.join(to_list)} ...")
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)

    if r.status_code == 200:
        print(f"✅ Email sent successfully to: {', '.join(to_list)}")
        print(f"   Subject: {subject}")
        print(f"   Gmail API response: {r.status_code}")
    else:
        print(f"❌ Gmail API error {r.status_code}: {r.text}")
        r.raise_for_status()

def main():
    df = get_understaffed()
    html = build_html(df)
    recipients = get_recipients()
    subject = f"PRS Weekly Staffing Gaps – {dt.date.today():%b %d, %Y}"
    gmail_send_html(recipients, subject, html)

if __name__ == "__main__":
    main()
