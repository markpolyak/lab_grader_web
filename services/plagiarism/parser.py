# parser.py
import re
import json
from typing import List, Tuple

def extract_matches_from_html(html: str) -> List[Tuple[str, str, float]]:
    match = re.search(r'var\s+GRAPH\s*=\s*({.*?})\s*;', html, re.DOTALL)
    if not match:
        return []

    graph = json.loads(match.group(1))
    return [
        (
            link['source']['id'] if isinstance(link['source'], dict) else link['source'],
            link['target']['id'] if isinstance(link['target'], dict) else link['target'],
            float(link['value'])
        )
        for link in graph.get('links', [])
    ]
