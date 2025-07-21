import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
import glob

from dotenv import load_dotenv
from .models import PlagiarismResult, ComparisonConfig
from .downloader import GitHubFileDownloader
from .sheets_manager import SheetsManager
from .parser import extract_matches_from_html

def _resolve_lab_key(course_config: Dict, lab_id: str) -> str:
    for key, value in course_config["labs"].items():
        if key == lab_id or value.get("short-name") == lab_id:
            return key
    raise KeyError(f"Could not resolve lab_id: '{lab_id}'")



class PlagiarismChecker:
    def __init__(self):
        self.downloader = None
        self.sheets = None

    def _normalize_path(self, path: str) -> str:
        """Normalize paths to consistent format for matching"""
        path = str(path).replace('\\', '/').lower()  # Convert to unix-style and lowercase
        # Remove any trailing filenames to focus on directory paths
        if '/' in path:
            path = path.rsplit('/', 1)[0]
        return path

    def run_pipeline(self, course_config: Dict, lab_id: str) -> List[PlagiarismResult]:
        self.sheets = SheetsManager(course_config)
        self.downloader = GitHubFileDownloader(
            github_token=os.getenv("GITHUB_TOKEN_PLAGIARISM") or os.getenv("GITHUB_TOKEN"),
            course_config=course_config
        )

        lab_key = _resolve_lab_key(course_config, lab_id)
        #lab_config = course_config["labs"][lab_key]
        lab_config = course_config["labs"].get(lab_id)
        if not lab_config or not lab_config.get("plagiarism", {}).get("enabled", False):
            print(f"Skipping lab {lab_id} - plagiarism checking not enabled")
            return []

        download_dir = Path(f"data/submissions/{course_config['name']}/{lab_key}")
        download_dir.mkdir(parents=True, exist_ok=True)

        students = self._get_valid_submissions(lab_config, download_dir)

        config = ComparisonConfig(
            lab_id=lab_id,
            course_id=course_config["name"],
            threshold=lab_config["plagiarism"]["threshold"],
            reference_files=[Path(p) for p in lab_config["plagiarism"]["reference_files"]],
            compare50_args=lab_config["plagiarism"].get("compare50_args", []),
            language=lab_config["plagiarism"]["language"],
            max_matches=lab_config["plagiarism"]["max-matches"],
            local_path=lab_config.get("github-prefix", f"lab-{lab_id}"),
            additional_orgs=lab_config["plagiarism"]["additional"],
            basefiles=lab_config["plagiarism"]["basefiles"],
            download_dir=download_dir,
            output_dir=Path(f"reports/comparisons/{course_config['name']}/{lab_key}")
        )
        threshold = config.threshold
        print(f"Using threshold: {threshold}")  # Debug line

        self.run_comparison(config)
        self._mark_reports_in_sheet(students, config)
        return []

    def _get_valid_submissions(self, lab_config: Dict, download_dir: Path) -> List[Dict]:
        valid = []
        for student in self.sheets.get_student_repos():
            if self.downloader.download_submission(lab_config, student["github"], download_dir):
                valid.append(student)
        return valid


    def _mark_reports_in_sheet(self, students: List[Dict], config: ComparisonConfig):
        for student in students:
            self.sheets.update_status(student['row'], "Not done")
            print(f"Set default status for {student['github']} (row {student['row']}): Not done")

        index_html = config.output_dir / "index.html"
        if not index_html.exists():
            print(f"❌ Expected HTML report not found at {index_html}")
            return

        with open(index_html, "r", encoding="utf-8") as f:
            html = f.read()

        matches = extract_matches_from_html(html)
        threshold = config.threshold

        print("\n=== DEBUGGING INFORMATION ===")
        print(f"Threshold: {threshold}")
        print("\nAll students in sheet:")
        for student in students:
            print(f"- {student['github']} (row {student['row']})")

        print("\nRaw matches from compare50:")
        for i, (source, target, score) in enumerate(matches, 1):
            print(f"{i}. {source} ↔ {target} ({score})")

        # Create student mapping
        student_map = {student['github']: student for student in students}
        flagged_students = set()

        def extract_username(path: str) -> Optional[str]:
            """Flexible username extraction from various path formats"""
            path = path.replace('\\', '/').lower()
            parts = [p for p in path.split('/') if p]

            # Try multiple extraction strategies
            for i, part in enumerate(parts):
                # Match known student usernames in any path position
                if part in student_map:
                    return part

            # Fallback: look for username-like patterns
            if len(parts) >= 2:
                # Try second-to-last component
                candidate = parts[-2]
                if any(candidate in s['github'].lower() for s in students):
                    return candidate

            return None

        # Process each match
        for source, target, score in matches:
            if score >= threshold:
                print(f"\nProcessing high-score match: {source} ↔ {target} ({score})")

                source_user = extract_username(source)
                target_user = extract_username(target)

                print(f"Extracted usernames: source={source_user}, target={target_user}")

                # Skip if either username is invalid
                if not source_user or not target_user:
                    print("Skipping - could not extract both usernames")
                    continue

                # Skip distribution matches
                if 'distribution' in source.lower() or 'distribution' in target.lower():
                    print("Skipping - matches distribution code")
                    continue

                # Flag both students involved
                for username in [source_user, target_user]:
                    if username in student_map:
                        flagged_students.add(username)
                        print(f"Flagging {username} (row {student_map[username]['row']})")
                    else:
                        print(f"Username {username} not found in student records")

        # Update Google Sheets
        print("\nFinal updates to Google Sheets:")
        for student in students:
            status = "⚠️ Detected" if student['github'] in flagged_students else "✓ not detected"
            print(f"Updating {student['github']} (row {student['row']}): {status}")
            self.sheets.update_status(student['row'], status)

    def run_comparison(self, config: ComparisonConfig) -> None:
        # Remove the output directory to avoid overwrite prompts
        if config.output_dir.exists():
            shutil.rmtree(config.output_dir)
    
        config.output_dir.mkdir(parents=True, exist_ok=True)
    
        filename = f"lab{config.lab_id}.cpp"
        reference_file = Path(f"data/distribution/{config.course_id}/{config.lab_id}/{filename}")
        submission_glob = f"data/submissions/{config.course_id}/{config.lab_id}/*/{filename}"
        submission_files = glob.glob(submission_glob)
    
        if not reference_file.exists():
            print(f"❌ Reference file not found: {reference_file}")
            return
    
        if not submission_files:
            print(f"❌ No submission files found using glob: {submission_glob}")
            return
    
        cmd = [
            "compare50",
            "--distro", str(reference_file),
            "--output", str(config.output_dir),
            *submission_files
        ]
    
        print("Running Compare50 with auto-confirm...")
        print(" ".join(cmd))
    
        # Pipe `yes` into compare50 to auto-confirm any prompt
        yes_proc = subprocess.Popen(['yes'], stdout=subprocess.PIPE)
        result = subprocess.run(cmd, stdin=yes_proc.stdout, capture_output=True, text=True)
        yes_proc.stdout.close()  # Allow yes to receive a SIGPIPE if compare50 exits
        yes_proc.wait()
    
        if result.returncode != 0:
            print("❌ Compare50 failed.")
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            raise RuntimeError("compare50 execution failed")
    
        print(f"✔️ Compare50 completed. Report available at: {config.output_dir}")


    def check(self, lab_id: str):
    # 1. Run Compare50 and get the report directory path
        report_path = Path("reports/comparisons/lab2/index.html")

        # 2. Read HTML
        with open(report_path, encoding="utf-8") as f:
            html = f.read()

        # 3. Extract match tuples (source, target, score)
        matches = extract_matches_from_html(html)

        # 4. Filter matches by threshold (e.g., > 0.8)
        flagged = [match for match in matches if match[2] >= self.config.threshold]

        # 5. Flag plagiarism results to Google Sheets
        for source, target, score in flagged:
            self.sheets.flag_plagiarism(source, target, score)

if __name__ == "__main__":
    report_path = Path("reports/comparisons/ld/2/index.html")
    with open(report_path, encoding="utf-8") as f:
        html = f.read()

    matches = extract_matches_from_html(html)
    print("Extracted matches:")
    for source, target, score in matches:
        print(f"{source} ↔ {target}: {score}")

