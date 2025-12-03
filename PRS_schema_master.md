
# PRS Database Schema (Consolidated Reference)

This document consolidates the full PRS schema as provided on 2025‑12‑01.  
Use this as the master reference when updating, extending, or debugging the PRS app.

---

## 1. agents
```
create table public.agents (
  id uuid primary key default gen_random_uuid(),
  first_name text,
  last_name text,
  display_name text,
  company text,
  name_for_1099 text,
  role text,
  phone text,
  email text,
  website text,
  address text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default auth.uid()
);
```

---

## 2. email_audit
```
create table public.email_audit (
  id bigserial primary key,
  ts timestamptz not null default now(),
  kind text not null,
  event_id bigint,
  recipient_email text not null,
  token text not null,
  status text not null default 'sent',
  gig_id uuid,
  detail jsonb,
  clicked_at timestamptz
);
```

---

## 3. gig_deposits
```
create table public.gig_deposits (
  id uuid primary key default gen_random_uuid(),
  gig_id uuid not null references gigs(id) on delete cascade,
  seq integer not null,
  due_date date not null,
  amount numeric(10,2) not null,
  is_percentage boolean default false,
  created_at timestamptz default now()
);
```

---

## 4. gig_musicians
```
create table public.gig_musicians (
  id uuid primary key default gen_random_uuid(),
  gig_id uuid not null references gigs(id) on delete cascade,
  musician_id uuid not null references musicians(id) on delete restrict,
  role text,
  amount_paid numeric(12,2),
  paid_on date,
  method text,
  created_at timestamptz not null default now()
);
```

---

## 5. gig_payments
```
create table public.gig_payments (
  id uuid primary key default gen_random_uuid(),
  gig_id uuid not null references gigs(id) on delete cascade,
  kind text not null,
  due_on date not null,
  amount numeric(12,2) not null,
  paid_on date,
  method text,
  reference text,
  created_at timestamptz not null default now(),
  payee_id uuid,
  payee_name text,
  role text,
  fee_withheld numeric(12,2) default 0,
  eligible_1099 boolean default true,
  net_amount numeric(12,2) generated always as (amount - coalesce(fee_withheld,0)) stored
);
```

---

## 6. gigs
```
create table public.gigs (
  id uuid primary key default gen_random_uuid(),
  venue_id uuid references venues(id) on delete set null,
  agent_id uuid references agents(id) on delete set null,
  sound_tech_id uuid references sound_techs(id) on delete set null,
  title text,
  event_date date not null,
  start_time time,
  end_time time,
  overnight boolean default false,
  package_name text,
  total_fee numeric(12,2),
  contract_status text default 'draft',
  private_flag boolean default false,
  notes text,
  created_by uuid default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  fee numeric(12,2),
  is_private boolean default false,
  sound_by_venue_name text,
  sound_by_venue_phone text,
  sound_provided boolean default false,
  sound_fee numeric,
  closeout_status text default 'open',
  closeout_notes text,
  closeout_at timestamptz,
  final_venue_gross numeric(12,2),
  final_venue_paid_date date,
  google_calendar_event_id text,
  is_test boolean default false
);
```

---

## 7. gigs_private
```
create table public.gigs_private (
  gig_id uuid primary key references gigs(id) on delete cascade,
  organizer text,
  event_type text,
  honoree text,
  special_instructions text,
  client_name text,
  client_email text,
  client_phone text,
  client_mailing_address text,
  contract_total_amount numeric(10,2),
  deposit1_amount numeric(10,2),
  deposit1_due_date date,
  deposit2_amount numeric(10,2),
  deposit2_due_date date,
  final_payment_due_date date,
  payment_method_notes text,
  package_name text,
  band_size integer,
  num_vocalists integer,
  ceremony_coverage text,
  cocktail_coverage text,
  reception_start_time time,
  reception_end_time time,
  overtime_rate_per_half_hour numeric(10,2),
  contract_status text default 'draft',
  contract_sent_at timestamptz,
  contract_last_sent_at timestamptz,
  contract_signed_at date,
  contract_pdf_path text,
  contract_version integer default 1
);
```

---

## 8. musicians
```
create table public.musicians (
  id uuid primary key default gen_random_uuid(),
  first_name text,
  middle_name text,
  last_name text,
  display_name text,
  name_for_1099 text,
  instrument text,
  phone text,
  email text,
  address text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default auth.uid(),
  stage_name text
);
```

---

## 9. notif_staffing_log
```
create table public.notif_staffing_log (
  id bigserial primary key,
  run_at timestamptz default now(),
  recipients integer,
  gigs_listed integer,
  status text,
  notes text
);
```

---

## 10. notif_staffing_subscribers
```
create table public.notif_staffing_subscribers (
  email text primary key,
  name text,
  active boolean default true,
  created_at timestamptz default now()
);
```

---

## 11. profiles
```
create table public.profiles (
  id uuid primary key,
  email text,
  full_name text,
  role text not null default 'standard',
  created_at timestamptz not null default now(),
  unique(email)
);
```

---

## 12. sound_techs
```
create table public.sound_techs (
  id uuid primary key default gen_random_uuid(),
  first_name text,
  last_name text,
  display_name text,
  company text,
  name_for_1099 text,
  role text,
  phone text,
  email text,
  address text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default auth.uid()
);
```

---

## 13. venues
```
create table public.venues (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  address_line1 text,
  address_line2 text,
  city text,
  state text,
  postal_code text,
  country text default 'USA',
  contact_name text,
  contact_phone text,
  contact_email text,
  agent_id uuid references agents(id) on delete set null,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default auth.uid()
);
```

---

## 14–17. Views (unrestricted)
The following objects exist as **read-only views**:
- `v_1099_payee_totals`
- `vw_1099_rollup`
- `vw_people_dropdown`
- `vw_understaffed_gigs`

Schemas cannot be exported from Supabase directly but they do not store data and have no impact on deletes.

---
## Change Log
2025-12-01 - Initial snapshot (is_test added)
2025-xx-xx - <future updates>


# END OF DOCUMENT
