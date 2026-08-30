##--- pages/99_Venue_Confirm.py

import datetime as dt
import streamlit as st
from supabase import create_client
from pathlib import Path

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_ROLE"]

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Venue Confirmation", page_icon="✅")

token = st.query_params.get("token")

# --- PRS Logo ---
logo_path = Path(__file__).parents[1] / "assets" / "prs_logo.png"
if logo_path.exists():
    st.image(str(logo_path), width=200)

st.title("Venue Booking Confirmation")

if not token:
    st.error("Invalid or missing confirmation link.")
    st.stop()

# 🔒 CRITICAL: enforce role = venue
vc = (
    sb.table("gig_confirmations")
    .select("id, gig_id, confirmed_at, role")
    .eq("token", token)
    .eq("role", "venue")
    .maybe_single()
    .execute()
    .data
)

if not vc:
    st.error("This confirmation link is not valid or has expired.")
    st.stop()

if vc.get("confirmed_at"):
    st.success("✅ This booking has already been confirmed.")
    st.write("Thank you — no further action is needed.")
    st.stop()

try:
    gig = (
        sb.table("gigs")
        .select("title, event_date, start_time, end_time, fee, venue_id, sound_provided")
        .eq("id", vc["gig_id"])
        .maybe_single()
        .execute()
        .data
    )
except Exception as e:
    st.error(f"Unable to retrieve booking details: {e}")
    st.stop()

if not gig:
    st.error("Unable to retrieve the booking associated with this confirmation link.")
    st.stop()

venue_name = "Not assigned"
if gig.get("venue_id"):
    try:
        venue = (
            sb.table("venues")
            .select("name")
            .eq("id", gig["venue_id"])
            .maybe_single()
            .execute()
            .data
        )
        if venue:
            venue_name = venue.get("name") or "Unnamed venue"
        else:
            venue_name = "Unavailable"
            st.warning("Venue details are no longer available for this booking.")
    except Exception:
        venue_name = "Unavailable"
        st.warning("Venue details could not be retrieved for this booking.")

def _format_time(value):
    if not value:
        return "—"
    for time_format in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(str(value), time_format).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return str(value)

fee = gig.get("fee")
try:
    fee_display = f"${float(fee):,.2f}" if fee is not None else "—"
except (TypeError, ValueError):
    fee_display = str(fee) if fee else "—"

booking_summary = {
    "Event": gig.get("title") or "Live Performance",
    "Date": gig.get("event_date") or "—",
    "Start time": _format_time(gig.get("start_time")),
    "End time": _format_time(gig.get("end_time")),
    "Fee": fee_display,
    "Venue": venue_name,
}
if gig.get("sound_provided"):
    booking_summary["Sound"] = "Provided by venue"

st.info("Please confirm the booking details below.")
st.table([booking_summary])

if st.button("✅ Confirm This Booking"):
    sb.table("gig_confirmations").update(
        {
            "confirmed_at": dt.datetime.utcnow().isoformat(),
            "confirmation_method": "link",
        }
    ).eq("id", vc["id"]).execute()

    st.success("🎉 Thank you! Your booking is now confirmed.")
    st.write("You may close this window.")
    st.stop()
