# Philly Rock & Soul (PRS) App

This repository contains the full Streamlit-based management system for
**Philly Rock & Soul**, including features for gig scheduling, staffing,
contract generation, payments, deposits, and automated email workflows.

The application integrates with **Supabase** for authentication,
database storage, contract assets, and email audit tracking.

---

## Features

* Gig creation and editing (public and private workflows)
* Dynamic contract generation (DOCX/PDF)
* Deposit scheduling and payment tracking
* Musician and sound tech management
* Automated email sending with audit logging
* Calendar sync with Google Calendar
* Gig closeout workflow
* Admin utilities (e.g., test gig cleanup)

---

## Repository Structure

/assets Contract templates, images, logos
/lib Utility modules (email, calendar, formatting)
/pages Streamlit multipage app (gigs, edits, admin tools)
.github/workflows Scheduled digests and reminders
PRS_schema_master.md Full Supabase database schema reference
requirements.txt Package requirements
Master Gig App.py Streamlit entry point

yaml
Copy code

---

## Database Schema

The entire PRS database structure — including all 17 tables,
views, relationships, and cascade behaviors — is fully documented in:

➡ **PRS_schema_master.md**

All schema changes must be reflected there and require approval
per `.github/CODEOWNERS`.

---

## Contributing

Changes to contract logic, gig workflows, or database schema should
generally pass through the **release branch** (protected) via pull request.

For schema updates in particular:

* Update `PRS_schema_master.md`
* Commit changes in the same PR
* Approval is required via CODEOWNERS

---

## Future Enhancements

This README will expand as we continue to develop features such as:

* Multi-band support
* Enhanced admin dashboard
* PDF batch generation (contracts, deposit schedules)
* Reconciliation workflows
* Inline edits for payments and closeouts

---

## License

Internal use by Philly Rock & Soul and Lutine Management Associates.

---

## Contract System – Private Gig Architecture (2025 Update)

This section documents the stabilized architecture for **private gigs**, including database schema conventions, merge-field mapping, and deprecated (legacy) fields.

### Private Gig Storage Model

Private gigs continue to be stored primarily in:

* `gigs` — master gig record (date, times, fee, venue, agent, etc.)
* `gigs_private` — private-gig specific metadata

### Active Private Gig Columns

| Column                 | Purpose                           |
| ---------------------- | --------------------------------- |
| organizer              | Organization / host               |
| event_type             | Wedding / Birthday / Corporate    |
| honoree                | Bride/Groom / guest of honor      |
| special_instructions   | Contract-only notes / run-of-show |
| cocktail_coverage      | Special handling in template      |
| client_name            | Primary booking contact           |
| client_email           | Email                             |
| client_phone           | Phone                             |
| client_mailing_address | Mailing / billing address         |

### Organizer Address (from `gigs`)

| Column           |
| ---------------- |
| organizer_street |
| organizer_city   |
| organizer_state  |
| organizer_zip    |

### Deprecated Legacy Columns (safe to keep, unused)

| Legacy Column                   | Status                                                    |
| ------------------------------- | --------------------------------------------------------- |
| overtime_rate_per_half_hour     | Deprecated — replaced by `gigs.overtime_rate`             |
| deposit1_* and deposit2_*       | Deprecated — replaced by gig_deposits table               |
| reception_start_time / end_time | Deprecated                                                |
| ceremony_coverage               | Not used                                                  |
| band_size / num_vocalists       | Not used                                                  |
| payment_method_notes            | Not used                                                  |
| package_name (private)          | Not used (public package_name in gigs is source of truth) |

---

## Word Merge Fields (Full Contract Template Dictionary)

### Standard Gig Fields

```
{{title}}
{{event_date}}
{{event_date_long}}
{{day_of_week}}
{{start_time}}
{{end_time}}
{{start_time_formatted}}
{{end_time_formatted}}

{{fee}}
{{fee_formatted}}
{{total_fee}}
{{total_fee_formatted}}
{{final_payment_formatted}}

{{venue_name}}
{{venue_address}}
{{venue_full_address}}
{{venue_city}}
{{venue_state}}
{{venue_zip}}
{{venue_contact_name}}
{{venue_contact_phone}}
{{venue_contact_email}}

{{agent_name}}
{{agent_company}}
{{agent_email}}
{{agent_phone}}

{{band_name}}
{{notes}}
{{sound_provided}}
{{sound_fee}}

{{overtime_rate}}
{{overtime_rate_formatted}}
```

### Deposit Fields

```
{{deposit1_display}}
{{deposit2_display}}
{{final_payment_formatted}}
```

### Private Gig Fields (from gigs_private)

```
{{organizer}}
{{event_type}}
{{honoree}}

{{client_name}}
{{client_email}}
{{client_phone}}
{{client_mailing_address}}

{{special_instructions}}
{{cocktail_coverage}}
```

### Organizer Address Fields (from gigs)

```
{{organizer_street}}
{{organizer_city}}
{{organizer_state}}
{{organizer_zip}}
```

### Branding

```
{{signature_image_path}}
{{logo_image_path}}
```

---
