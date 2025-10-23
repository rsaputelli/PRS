"""
Tool: weekly_venue_digest.py
Purpose: Send weekly confirmation email to each venue for upcoming PUBLIC gigs.
"""

import os, datetime as dt, pandas as pd
from supabase import create_client
from lib.ui_format import format_currency

def main():
    print("Weekly Venue Digest Script initialized.")
    # Tomorrow:
    # 1. Query gigs where is_private = False
    # 2. Join venue emails
    # 3. Send grouped Gmail email with gig list

if __name__ == "__main__":
    main()
