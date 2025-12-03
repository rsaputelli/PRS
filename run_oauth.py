from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_ID = "1069111756063-s7i4ccvjes7var0q183umktn0j67e7ja.apps.googleusercontent.com"
CLIENT_SECRET = "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"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]

# Use loopback flow; no need to pre-configure redirect URIs for Desktop clients
flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    SCOPES,
)

# If your browser or firewall dislikes a local listener, use run_console() instead
try:
    creds = flow.run_local_server(port=0)
except Exception:
    creds = flow.run_console()

print("\nREFRESH_TOKEN:\n" + (creds.refresh_token or "NO_REFRESH_TOKEN_ISSUED"))
