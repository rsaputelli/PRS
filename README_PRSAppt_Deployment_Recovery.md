PRS Booking & Scheduling App
Deployment, Environment & Recovery Guide

This repository powers the PRS Band Booking, Scheduling & Communications App.
This README documents the required environment settings, recovery steps, and validation procedures to ensure stability across deployments and Streamlit environment resets.

Last validated: 2025-12-26

✔ Environment Requirements

The app depends on:

Supabase (auth, database, storage)

Gmail API (email sending + ICS attachments)

Google Calendar (event integration)

Streamlit Cloud secrets (secrets.toml)

All deployments must include the working secrets structure below.

🔐 Required secrets.toml
SUPABASE_URL="https://xxxxxxxxxxxx.supabase.co"
SUPABASE_ANON_KEY="..."
SUPABASE_SERVICE_KEY="..."

ADMIN_EMAILS=[
  "prsbandinfo@gmail.com"
]

PRS_ADMINS=[
  "prsbandinfo@gmail.com"
]

# ---- Gmail OAuth (primary mail sender) ----
[gmail_oauth]
client_id = "..."
client_secret = "..."
refresh_token = "..."
token_uri = "https://oauth2.googleapis.com/token"

# ---- Google Calendar IDs ----
[gcal_ids]
"Philly Rock and Soul" = "d3bi42ke2ks1ndqdnea2ushegc@group.calendar.google.com"

# ---- Optional SMTP fallback ----
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER="prsbandinfo@gmail.com"
SMTP_PASS=""

Notes

Gmail + Calendar must remain TOML tables, not JSON blocks

PRS_ADMINS controls admin-only pages

SMTP is fallback only — OAuth is primary

Multiple calendars may be added under [gcal_ids]

🛠 Deployment / Recovery Checklist

Run these steps when redeploying, migrating, or fixing a broken environment.

Phase 1 — Core Environment Check

Confirm secrets load without TOML parsing errors

Verify login with prsbandinfo@gmail.com

Ensure admin pages open normally (no permission block)

Phase 2 — Email + ICS Test

Open Enter Gig / Edit Gig

Enable Diagnostic Mode

Trigger send for:

Sound Tech

Agent

Player

Verify audit rows = dry-run

Then disable diagnostic mode and send a real test:

Gmail sends successfully

ICS calendar file opens correctly

Phase 3 — Calendar Validation

Run the Calendar Diagnostics tool.

Expected:

Google auth OK

Correct Calendar ID detected

events.list: allowed

If not:

re-check [gcal_ids]

refresh token may need replacement

Phase 4 — Database Integrity Check

Confirm:

Gigs load

Musicians list loads

Staffing report works

No RLS / permission errors

Audit tables receive entries

🧩 Recommended Files to Preserve

After a stable deployment, archive:

secrets.toml
lib/auth.py
lib/email_utils.py
lib/calendar_utils.py
tools/send_*.py
requirements.txt


Store in:

/Recovery/<date>/

🧪 Planned: App Health Self-Test Page

Future addition: 99_App_Health_Check.py providing one-click diagnostics for:

Supabase connection

Admin state

Gmail auth

Calendar auth

ICS generator

Audit table write test

🎯 Next Development Area

Player-access scheduling review (login + personal schedule visibility).

📜 Changelog
Date	Update
2025-12-26	Deployment baseline restored & verified
