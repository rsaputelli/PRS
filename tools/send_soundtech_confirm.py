"""
Tool: send_soundtech_confirm.py
Purpose: Send immediate confirmation email to a sound tech when they are assigned to a gig.
"""

import os, datetime as dt, pandas as pd
from supabase import create_client
from lib.ui_format import format_currency

def main():
    # Placeholder — tomorrow: detect sound_tech_id changes and send Gmail confirmation
    print("Sound Tech Confirmation Script initialized.")
    # Steps tomorrow:
    # 1. Load gigs with assigned sound_tech_id
    # 2. Fetch tech email and gig details
    # 3. Send Gmail confirmation using Gmail API

if __name__ == "__main__":
    main()
