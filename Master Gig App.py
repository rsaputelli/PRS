import streamlit as st
from auth_helper import require_admin

# -------------------------------------------------
# ADMIN GATE — NOTHING RENDERS BEFORE THIS
# -------------------------------------------------
user, session, user_id = require_admin()

if not user:
    st.stop()

# -------------------------------------------------
# GLOBAL ADMIN LAYOUT (SAFE ZONE)
# -------------------------------------------------
from lib.auth_logout import logout_and_redirect

with st.sidebar:
    if st.button("🚪 Logout"):
        logout_and_redirect()

# -------------------------------------------------
# ADMIN-ONLY CONTENT STARTS HERE
# -------------------------------------------------

st.title("Master Gig App (Admin)")
st.error("Admins only.")

# -------------------------------------------------
# Debug (ADMIN ONLY)
# -------------------------------------------------
with st.expander("Debug (Admin Only)", expanded=False):
    try:
        # If your auth helper exposes the Supabase user
        supabase_user = session.get("user") if session else None
        if supabase_user:
            st.json(supabase_user)
        else:
            st.write("No supabase_user available")

        st.write("DEBUG: role_is_admin version = id-column")
        st.write("User ID:", user_id)
    except Exception as e:
        st.error(f"Debug rendering error: {e}")


gig_id = st.query_params.get("gig_id", ["demo-001"])[0]
try:
    data = load_gig_view(gig_id)
except Exception as e:
    st.error(f"Could not load gig: {e}")
    st.stop()

# Show summary + admin overrides
colL, colR = st.columns([2,1])
with colL:
    st.subheader("Contract Summary")
    st.write({
        "Event": f'{data["event_type"]} on {data["event_date"]}',
        "Venue": f'{data["venue_name"]}, {data["venue_address"]} ({data["venue_type"]})',
        "Schedule": {
            "Cocktail": f'{data["cocktail_start"]} – {data["cocktail_end"]}',
            "Reception": f'{data["reception_start"]} – {data["reception_end"]}',
            "Duration (hrs)": data["performance_duration_hours"],
        },
        "Package": f'{data["package_name"]} ({data["package_price"]})',
        "Band": f'{data["band_size"]}-piece, {data["num_vocalists"]} vocalist(s)',
        "Overtime": data["overtime_rate"] + "/hour",
        "Total": data["contract_total"],
        "Deposits": [
            (data["deposit1_amt"], data["deposit1_due"]),
            (data["deposit2_amt"], data["deposit2_due"]),
            (data["deposit3_amt"], data["deposit3_due"]),
        ],
        "Final Payment": (data["final_payment_amt"], data["final_payment_due"]),
        "Hosts": data["host_names"],
        "Emails": data["host_email"],
    })

    with st.expander("Optional Admin Overrides (+ OTHER pattern)"):
        # Package dropdown from DB
        pkg_rows = sb.table("packages").select("name, default_price").eq("is_active", True).order("name").execute().data or []
        pkg_names = [r["name"] for r in pkg_rows] + ["OTHER"]
        sel = st.selectbox("Package", pkg_names, index=(pkg_names.index(data["package_name"]) if data.get("package_name") in pkg_names else len(pkg_names)-1))
        if sel == "OTHER":
            new_pkg = st.text_input("New Package Name", value=data.get("package_name",""))
            new_price = st.text_input("New Package Default Price", value=str(data.get("package_price","")))
            if st.button("Save NEW Package to DB"):
                if new_pkg.strip():
                    upsert_package(new_pkg, new_price or 0)
                    st.success("Saved. Reload to see in dropdown.")
                else:
                    st.warning("Package name required.")
            # use current entry for this document
            data["package_name"] = new_pkg or data["package_name"]
            data["package_price"] = new_price or data["package_price"]
        else:
            data["package_name"] = sel
            match = next((r for r in pkg_rows if r["name"] == sel), None)
            data["package_price"] = match["default_price"] if match else data["package_price"]

        # Editable totals
        data["contract_total"] = st.text_input("Contract Total", str(data["contract_total"]))
        data["venue_type"] = st.selectbox("Indoor/Outdoor", ["Indoor","Outdoor","Mixed"], index=["Indoor","Outdoor","Mixed"].index(data.get("venue_type","Indoor")))
        data["overtime_rate"] = st.text_input("Overtime Rate", str(data["overtime_rate"]))

with colR:
    st.subheader("Generate Draft")
    if not os.path.exists(TEMPLATE_PATH):
        st.error("Template file missing. Upload PRS_Contract_Template.docx to app root.")
        st.stop()
    draft_bytes = merge_docx(TEMPLATE_PATH, data)
    st.download_button("⬇️ Download Draft DOCX",
                       draft_bytes,
                       file_name=f"PRS_Contract_{gig_id}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       use_container_width=True)

st.markdown("---")
st.subheader("Admin Review")
ok_1 = st.checkbox("Verified dates/times/venue and Indoor/Outdoor.")
ok_2 = st.checkbox("Verified package, price, total, deposits, final payment.")
ok_3 = st.checkbox("Verified host names and recipient email(s).")
ready = ok_1 and ok_2 and ok_3

col1, col2, col3 = st.columns([1,1,2])
with col1:
    store_now = st.button("💾 Save to Storage (Draft)")
with col2:
    send_now = st.button("✅ Approve & Send", disabled=not ready)

storage_url = None
if store_now:
    storage_url = upload_contract_to_storage(gig_id, f"PRS_Contract_{gig_id}.docx", draft_bytes)
    st.success(f"Saved draft to storage:\n{storage_url}")

if send_now and ready:
    storage_url = upload_contract_to_storage(gig_id, f"PRS_Contract_{gig_id}.docx", draft_bytes)
    mark_contract_sent(gig_id, storage_url)
    to_emails = [e.strip() for e in (data.get("host_email") or "").split(",") if e.strip()]
    cc_emails = [SENDER_EMAIL]
    subj = f"Philly Rock and Soul – Contract for {data['event_type']} on {data['event_date']}"
    html = f"""
    <p>Hi {data['host_names']},</p>
    <p>Attached is your contract for <b>{data['event_type']}</b> on <b>{data['event_date']}</b> at <b>{data['venue_name']}</b>.</p>
    <p>Please reply to confirm; we’ll countersign. Thank you!</p>
    <p>— Philly Rock and Soul</p>
    <p><small>Download link: <a href="{storage_url}">{storage_url}</a></small></p>
    """
    try:
        send_email_smtp(to_emails, subj, html, f"PRS_Contract_{gig_id}.docx", draft_bytes, cc_addrs=cc_emails)
        st.success("Approved and sent. A copy was CC’d internally.")
    except Exception as e:
        st.error(f"Email failed: {e}")
