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
    try: return f"${float(x):,.2f}"
    except: return ""

def get_understaffed():
    # Prefer a view if you created it: vw_understaffed_gigs
    try:
        df = _sel("vw_understaffed_gigs", "*")
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        # Fallback: compute from gigs + gig_musicians here if you didn’t create the view
        gigs = _sel("gigs", "*")
        if gigs.empty:
            return gigs
        if "event_date" in gigs:
            gigs["event_date"] = pd.to_datetime(gigs["event_date"], errors="coerce").dt.date
        assign = _sel("gig_musicians", "gig_id, role, musician_id")
        roles_needed = {"Male Vocals","Female Vocals","Guitar","Bass","Keyboard","Drums","Saxophone","Trombone","Trumpet"}
        out = []
        for _, r in gigs.iterrows():
            gid = r.get("id")
            sub = assign[assign["gig_id"] == gid]
            have = set(sub["role"].dropna().astype(str))
            missing_roles = list(roles_needed - have)
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
            fully = (len(missing_roles)==0) and sound_ok and details_ok
            if not fully:
                out.append({
                    "event_date": r.get("event_date"),
                    "title": r.get("title"),
                    "venue_id": r.get("venue_id"),
                    "fee": r.get("fee"),
                    "missing_roles": ", ".join(sorted(missing_roles)),
                    "sound_ok": sound_ok,
                    "details_ok": details_ok,
                })
        df = pd.DataFrame(out)

    if "event_date" in df:
        try: df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce").dt.date
        except: pass
    return df.sort_values(by=["event_date"])

def get_recipients():
    sub = _sel("notif_staffing_subscribers", "email", {"active": True})
    emails = [e for e in sub.get("email", []) if e]
    return emails

def build_html(df: pd.DataFrame) -> str:
    today = dt.date.today().strftime("%b %d, %Y")
    if df.empty:
        return f"<p>All upcoming gigs are fully staffed as of {today}. ✅</p>"
    rows = []
    for _, r in df.iterrows():
        date = r.get("event_date") or ""
        title = r.get("title") or ""
        venue = r.get("venue_id") or ""  # can join to venues for nice names if desired
        fee = currency(r.get("fee"))
        miss_bits = []
        if str(r.get("missing_roles") or "").strip(): miss_bits.append("roles")
        if not bool(r.get("sound_ok")): miss_bits.append("sound")
        if not bool(r.get("details_ok")): miss_bits.append("details")
        missing = ", ".join(miss_bits)
        rows.append(f"<tr><td>{date}</td><td>{title}</td><td>{venue}</td><td>{fee}</td><td>{missing}</td></tr>")
    head = "<thead><tr><th>Date</th><th>Title</th><th>Venue</th><th>Fee</th><th>Missing</th></tr></thead>"
    body = "<tbody>" + "".join(rows) + "</tbody>"
    return f"<p>Gigs not fully staffed as of {today}:</p><table border='1' cellpadding='6' cellspacing='0'>{head}{body}</table>"

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
    if not to_list:
        return
    msg = MIMEText(html_body, "html")
    msg["To"] = ", ".join(to_list)
    msg["From"] = GMAIL_SENDER
    msg["Subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    token = gmail_access_token()
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    payload = {"raw": raw}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()

def main():
    df = get_understaffed()
    html = build_html(df)
    recipients = get_recipients()
    subject = f"PRS Weekly Staffing Gaps – {dt.date.today():%b %d, %Y}"
    gmail_send_html(recipients, subject, html)

if __name__ == "__main__":
    main()
