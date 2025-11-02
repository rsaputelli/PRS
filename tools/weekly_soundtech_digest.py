# -----------------------------------------------------------------------------
sb.table("email_audit").insert({
"token": token,
"recipient_email": recipient_email,
"kind": "soundtech_weekly_digest",
"status": "sent",
"ts": dt.datetime.utcnow().isoformat()
}).execute()




def run_weekly_digest(now: Optional[dt.datetime] = None):
if now is None:
now = dt.datetime.now()
sb = _sb()
start, end = _week_window(now)


techs = _fetch_soundtechs(sb)
events = _fetch_events_for_range(sb, start, end)


by_tech: dict[int, list[dict]] = defaultdict(list)
for ev in events:
tid = ev.get("sound_tech_id")
if tid:
by_tech[tid].append(ev)


for tech in techs:
gigs = by_tech.get(tech["id"], [])
if not gigs:
continue


rows = []
attachments = []
for ev in sorted(gigs, key=lambda r: (r["date"], r.get("start_time") or "")):
fee_str = None
if not ev.get("sound_provided") and ev.get("sound_fee") is not None:
fee_str = f"${float(ev['sound_fee']):,.2f}"
rows.append({
"Gig": ev.get("gig_name", ""),
"Date": ev["date"],
"Call Time": ev.get("start_time", ""),
"Venue": ev.get("venue", ""),
"Fee": fee_str or "—",
})
if INCLUDE_ICS:
# Generate ICS per gig (optional)
uid = uuid.uuid4().hex + "@prs"
# Build starts/ends from date & start_time
tz = pytz.timezone(TZ)
day = dt.datetime.strptime(ev["date"], "%Y-%m-%d").date()
st = dt.datetime.combine(day, dt.datetime.strptime(ev.get("start_time") or "17:00", "%H:%M").time())
et = st + dt.timedelta(hours=4)
stz = tz.localize(st)
etz = tz.localize(et)
ics = make_ics_bytes(
uid=uid,
title=f"{ev.get('gig_name','Gig')} — Sound Tech",
starts_at=stz,
ends_at=etz,
location=f"{ev.get('venue','')} {ev.get('address','')} {ev.get('city','')}, {ev.get('state','')}",
description="Sound tech call. PRS Scheduling.",
)
attachments.append({
"filename": f"{ev['gig_name']}-{ev['date']}.ics",
"mime": "text/calendar",
"content": ics,
})


html = (
f"<p>Hi {tech['full_name']},</p>"
f"<p>Here are your sound gigs for the coming week ({start.date()} → {end.date()}).</p>"
+ build_html_table(rows)
+ f"<p>— {FROM_NAME}</p>"
)
subject = f"[Sound Tech] Weekly Digest — {start.date()}"


token = uuid.uuid4().hex
_insert_email_audit(sb, token=token, recipient_email=tech["email"])
gmail_send(subject, tech["email"], html, cc=[CC_RAY], attachments=attachments)




if __name__ == "__main__":
run_weekly_digest()