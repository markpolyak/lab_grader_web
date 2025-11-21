"""
Test script to verify lab column lookup works correctly.
Run: python test_lab_column.py
"""
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import yaml
import os

CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")

def test_lab_column_lookup():
    # Load course config
    with open("courses/operating-systems-2025.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    course = config["course"]
    spreadsheet_id = course["google"]["spreadsheet"]
    labs = course.get("labs", {})

    # Connect to Google Sheets
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open_by_key(spreadsheet_id)

    # Get first worksheet (or specify group name)
    sheet = spreadsheet.worksheets()[0]
    print(f"Testing on sheet: {sheet.title}")
    print("-" * 50)

    # Test each lab
    for lab_id, lab_config in labs.items():
        short_name = lab_config.get("short-name")
        if not short_name:
            print(f"Lab '{lab_id}': NO short-name configured (will use fallback)")
            continue

        # Search for short-name in sheet
        cell = sheet.find(short_name)
        if cell:
            print(f"Lab '{lab_id}' ({short_name}): Found at row {cell.row}, column {cell.col}")

            # Verify by reading the cell value
            value = sheet.cell(cell.row, cell.col).value
            print(f"  -> Cell value: '{value}'")
        else:
            print(f"Lab '{lab_id}' ({short_name}): NOT FOUND!")

if __name__ == "__main__":
    test_lab_column_lookup()
