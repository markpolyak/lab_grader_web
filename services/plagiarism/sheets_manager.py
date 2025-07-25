import gspread
from oauth2client.service_account import ServiceAccountCredentials
from typing import List, Dict

def excel_col_to_index(col: str) -> int:
    """Convert Excel-style column label to 1-based index."""
    col = col.upper()
    index = 0
    for char in col:
        index = index * 26 + (ord(char) - ord('A') + 1)
    return index

class SheetsManager:
    def __init__(self, config: Dict):
        self.scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        self.creds = ServiceAccountCredentials.from_json_keyfile_name(
            "credentials.json", self.scope
        )
        self.client = gspread.authorize(self.creds)
        self.config = config

    def get_student_repos(self) -> List[Dict]:
        sheet = self.client.open_by_key(self.config["google"]["spreadsheet"])
        worksheet = sheet.get_worksheet(0)
        github_col = excel_col_to_index(self.config["google"]["github-column"])
        start_row = self.config["google"]["start-row"]

        all_rows = worksheet.get_all_values()
        valid_students = []

        for idx, row in enumerate(all_rows):
            if idx < start_row - 1:
                continue
            if len(row) < github_col:
                continue  # Row too short
            github = row[github_col - 1].strip()
            if not github:
                continue  # Empty GitHub cell

            valid_students.append({
                "github": github,
                "row": idx + 1  # 1-based row index in Sheets
            })

        return valid_students

    def update_status(self, row: int, message: str):
        sheet = self.client.open_by_key(self.config["google"]["spreadsheet"])
        worksheet = sheet.get_worksheet(0)
        status_col = excel_col_to_index(self.config["google"]["status-column"])
        print(f"Updating status at row {row}: {message}")  # Debug line
        worksheet.update_cell(row, status_col, message)
    


    def flag_plagiarism(self, source: str, target: str, score: float):
        """
        Update Google Sheets with plagiarism results.
        Flags the pair (source, target) with their plagiarism score.
        """
        sheet = self.client.open_by_key(self.config["google"]["spreadsheet"])
        worksheet = sheet.get_worksheet(0)
        print(f"Flagging plagiarism between {source} and {target} with score {score}")

        # Find rows for the source and target students
        source_row = self._find_student_row(source, worksheet)
        target_row = self._find_student_row(target, worksheet)

        if source_row:
            worksheet.update_cell(source_row, self.config["google"]["status-column"], f"⚠️ Detected: {score}")

        if target_row:
            worksheet.update_cell(target_row, self.config["google"]["status-column"], f"⚠️ Detected: {score}")


    def _find_student_row(self, github: str, worksheet) -> int:
        """
        Find the row for a student in the sheet based on their GitHub username.
        """
        github_col = excel_col_to_index(self.config["google"]["github-column"]) - 1  # 0-indexed
        all_rows = worksheet.get_all_values()

        for idx, row in enumerate(all_rows):
            if row[github_col].strip() == github:
                return idx + 1  # Return 1-based row index
        return None

