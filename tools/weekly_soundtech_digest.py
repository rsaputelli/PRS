"""
Tool: weekly_soundtech_digest.py
Purpose: Send weekly digest email to each sound tech listing their upcoming gigs.
"""

import os, datetime as dt, pandas as pd
from supabase import create_client
from lib.ui_format import format_currency

def main():
    print("Weekly Soundtech Digest Script initialized.")
    # Tomorrow:
    # 1. Query gigs for next 7–14 days with sound_tech_id set
    # 2. Group by tech, compile HTML table
    # 3. Send via Gmail API

if __name__ == "__main__":
    main()
