"""
Tool: send_loadin_reminders.py
Purpose: Send 24-hour pre-gig reminders to sound techs and venues.
"""

import os, datetime as dt, pandas as pd
from supabase import create_client
from lib.ui_format import format_currency

def main():
    print("Daily Load-in Reminder Script initialized.")
    # Tomorrow:
    # 1. Query gigs with event_date = tomorrow
    # 2. Send reminder emails to assigned tech and venue
    # 3. Optional: include “Confirm Received” link

if __name__ == "__main__":
    main()
