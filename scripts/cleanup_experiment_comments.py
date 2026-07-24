"""Delete experimental Drive comments created by experiment_sheet_cell_comment.py."""
import os

import requests
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()
CRED = os.getenv("CREDENTIALS_FILE", "credentials.json")
SHEET_ID = "1t2sAxKgXqS4yB7fTM_dj6JKOcW7kGjtmHAQXbdBWQC0"
creds = ServiceAccountCredentials.from_json_keyfile_name(
    CRED,
    [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ],
)
token = creds.get_access_token().access_token
headers = {"Authorization": f"Bearer {token}"}
r = requests.get(
    f"https://www.googleapis.com/drive/v3/files/{SHEET_ID}/comments",
    headers=headers,
    params={"fields": "comments(id,content)", "pageSize": 100},
    timeout=30,
)
comments = r.json().get("comments", [])
deleted = 0
for c in comments:
    content = c.get("content") or ""
    if "plagiarism-anchor-experiment" in content:
        dr = requests.delete(
            f"https://www.googleapis.com/drive/v3/files/{SHEET_ID}/comments/{c['id']}",
            headers=headers,
            timeout=30,
        )
        print("delete", c["id"], dr.status_code)
        deleted += 1
print("deleted", deleted, "of", len(comments))
