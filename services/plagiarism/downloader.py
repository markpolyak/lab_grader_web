import os
import requests
from pathlib import Path
from typing import Optional, Dict

class GitHubFileDownloader:
    def __init__(self, github_token: Optional[str], course_config: Dict):
        if github_token is None:
            github_token = os.getenv("GITHUB_TOKEN_PLAGIARISM") or os.getenv("GITHUB_TOKEN")
        
        self.headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.course_config = course_config

    def download_submission(self, lab_config: Dict, github_user: str, save_dir: Path) -> Optional[Path]:
        prefix = self.course_config["github"].get("prefix", "")  # fallback to empty if missing
        prefix = lab_config.get("github-prefix", prefix) 
        repo = f"{prefix}-{github_user}"
        org = self.course_config["github"]["organization"]

        if not self._check_ci_passed(org, repo):
            print(f"Skipping {repo}: CI check failed.")
            return None

        downloaded_files = []
        for filename in lab_config["files"]:
            file_path = self._download_file(org, repo, filename, save_dir / github_user)
            if file_path:
                downloaded_files.append(file_path)

        return downloaded_files[0] if downloaded_files else None


    def _check_ci_passed(self, org: str, repo: str) -> bool:
        url = f"https://api.github.com/repos/{org}/{repo}/actions/runs?per_page=1"
        #print(f"Checking CI status: ORG={org}, REPO={repo}, URL={url}")

        try:
            resp = requests.get(url, headers=self.headers)

            if resp.status_code != 200:
                print(f"Failed to fetch CI status for {org}/{repo}. Status Code: {resp.status_code}")
                return False

            runs = resp.json().get("workflow_runs", [])

            if not runs:
                print(f"No CI runs found for {org}/{repo}.")
                return False

            # ✅ Only look at the most recent run
            latest_run = runs[0]

            print(f"Latest CI run status: {latest_run.get('status')}, conclusion: {latest_run.get('conclusion')}")

            return (
                latest_run.get("status") == "completed" and
                latest_run.get("conclusion") == "success"
            )

        except Exception as e:
            print(f"Error fetching CI status for {org}/{repo}: {e}")
            return False


    def _download_file(self, org: str, repo: str, filename: str, save_dir: Path) -> Optional[Path]:
        try:
            url = f"https://api.github.com/repos/{org}/{repo}/contents/{filename}"
            response = requests.get(url, headers={**self.headers, "Accept": "application/vnd.github.v3.raw"}, timeout=10)

            if response.status_code == 200:
                save_dir.mkdir(parents=True, exist_ok=True)
                save_path = save_dir / filename
                save_path.write_bytes(response.content)
                return save_path
            else:
                print(f"❌ {filename} not found in {repo} (status {response.status_code})")
        except Exception as e:
            print(f"❗ Exception downloading {filename} from {repo}: {e}")
        return None

