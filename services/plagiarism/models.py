from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path

# Individual file comparison info inside a plagiarism match
class CodeMatch(BaseModel):
    file1: Path
    file2: Path
    similarity: float
    matching_lines: List[tuple[int, int]]

# The full result between two submissions
class PlagiarismResult(BaseModel):
    submission1: str
    submission2: str
    overall_similarity: float
    matches: List[CodeMatch]
    report_path: Path
    exceeds_threshold: bool

# Top-level config class — driven by parsed YAML
class ComparisonConfig(BaseModel):
    lab_id: str
    course_id: str

    # Detection tuning
    threshold: float                     # Minimum similarity to flag
    language: str                        # Programming language, e.g., cc or py
    max_matches: int                     # Limit number of comparisons
    local_path: str                      # Local name to show in report

    # Files affecting detection
    reference_files: List[Path]          # Full path to extra distribution files
    additional_orgs: List[str]           # Reference submission orgs (see: --add)
    basefiles: List[dict]                # Repo-based basefiles (repo + filename)

    compare50_args: List[str]            # Any extra compare50 CLI args

    # I/O paths
    download_dir: Path                   # Where to fetch student submissions
    output_dir: Path                     # Where to store reports
