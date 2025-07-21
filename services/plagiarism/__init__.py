# This __init__.py makes the directory a Python package and allows
# other modules to import from plagiarism using cleaner syntax.
from .checker import PlagiarismChecker
from .downloader import GitHubFileDownloader
from .models import PlagiarismResult, ComparisonConfig, CodeMatch
from .sheets_manager import SheetsManager
from .parser import extract_matches_from_html  

__all__ = [
    'PlagiarismChecker',
    'GitHubFileDownloader',
    'SheetsManager',
    'PlagiarismResult',
    'ComparisonConfig',
    'CodeMatch',
    'extract_matches_from_html'  # <-- Add this line
]
