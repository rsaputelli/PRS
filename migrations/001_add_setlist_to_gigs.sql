-- Migration: Add set list support to gigs table
-- Date: 2025-05-30
-- Description: Adds columns to store set list file URLs and upload timestamps

ALTER TABLE public.gigs
ADD COLUMN setlist_url text,
ADD COLUMN setlist_uploaded_at timestamptz;

-- These columns are nullable and additive; no data loss occurs
-- setlist_url: Public Supabase URL to the set list file
-- setlist_uploaded_at: Timestamp of when the file was last uploaded
