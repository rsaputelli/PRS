# File: tools/send_soundtech_confirm.py
"token": token,
"event_id": event_id,
"recipient_email": recipient_email,
"kind": kind,
"status": status,
"ts": dt.datetime.utcnow().isoformat()
}).execute()




def send_soundtech_confirm(event_id: int) -> None:
sb = _sb()
payload = _fetch_event_and_tech(sb, event_id)
ev, tech = payload["event"], payload["tech"]


starts_at, ends_at = _localize(ev["date"], ev.get("start_time") or "17:00")
fee_str = None
if not ev.get("sound_provided") and ev.get("sound_fee") is not None:
fee_str = f"${float(ev['sound_fee']):,.2f}"


token = uuid.uuid4().hex
_insert_email_audit(
sb, token=token, event_id=ev["id"], recipient_email=tech["email"], kind="soundtech_confirm", status="sent"
)


# Email body
rows = [{
"Gig": ev.get("gig_name", ""),
"Date": ev["date"],
"Call Time": ev.get("start_time", ""),
"Venue": ev.get("venue", ""),
"Address": ev.get("address", ""),
"City": ev.get("city", ""),
"State": ev.get("state", ""),
"Fee (if applicable)": fee_str or "—",
}]
html_table = build_html_table(rows)


mailto = (
f"mailto:{tech['email']}?subject="
f"Confirm%20received%20-%20{ev['gig_name']}%20({ev['date']})%20[{token}]&body=Reply%20to%20confirm.%20Token%3A%20{token}"
)


html = f"""
<p>Hi {tech['full_name']},</p>
<p>You’ve been assigned as <b>Sound Tech</b> for the gig below.</p>
{html_table}
<p>
Please <a href="{mailto}"><b>confirm received</b></a>.
This helps us keep staffing tight and on time.
</p>
<p>— {FROM_NAME}</p>
"""


subject = f"[Sound Tech] {ev['gig_name']} — {ev['date']}"


atts = []
if INCLUDE_ICS:
ics_bytes = make_ics_bytes(
uid=token + "@prs",
title=f"{ev.get('gig_name','Gig')} — Sound Tech",
starts_at=starts_at,
ends_at=ends_at,
location=f"{ev.get('venue','')} {ev.get('address','')} {ev.get('city','')}, {ev.get('state','')}",
description="Sound tech call. Brought to you by PRS Scheduling.",
)
atts.append({"filename": f"{ev['gig_name']}-{ev['date']}.ics", "mime": "text/calendar", "content": ics_bytes})


gmail_send(subject, tech["email"], html, cc=[CC_RAY], attachments=atts)




if __name__ == "__main__":
import argparse
p = argparse.ArgumentParser(description="Send immediate sound tech confirmation email")
p.add_argument("event_id", type=int, help="Event ID")
args = p.parse_args()
send_soundtech_confirm(args.event_id)