"""
Score extraction from GitHub Actions logs.

This module contains functions for extracting student scores (points)
from GitHub Actions job logs using configurable regex patterns.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass
class ScoreResult:
    """Result of score extraction from logs."""
    found: str | None  # Score as string (e.g., "10.5" or "10,5")
    error: str | None = None


def normalize_score(score_str: str) -> str:
    """
    Normalize score string to use consistent decimal separator.

    Accepts both comma and dot as decimal separator in input.
    Returns normalized string with the original separator preserved.

    Args:
        score_str: Score string (e.g., "10.5" or "10,5")

    Returns:
        Normalized score string

    Examples:
        >>> normalize_score("10.5")
        '10.5'
        >>> normalize_score("10,5")
        '10,5'
        >>> normalize_score("10")
        '10'
    """
    return score_str.strip()


def scores_equal(score1: str, score2: str) -> bool:
    """
    Compare two score strings for equality.

    Treats "10.5" and "10,5" as equal values.

    Args:
        score1: First score string
        score2: Second score string

    Returns:
        True if scores represent the same numeric value

    Examples:
        >>> scores_equal("10.5", "10,5")
        True
        >>> scores_equal("10", "10.0")
        True
        >>> scores_equal("10.5", "10.6")
        False
    """
    try:
        # Replace comma with dot for numeric comparison
        val1 = Decimal(score1.replace(',', '.'))
        val2 = Decimal(score2.replace(',', '.'))
        return val1 == val2
    except (InvalidOperation, ValueError):
        # Fallback to string comparison if not valid numbers
        return score1 == score2


def extract_score_from_logs(logs: str, patterns: list[str]) -> ScoreResult:
    """
    Extract score from GitHub Actions job logs using multiple patterns.

    Tries each pattern in order until a match is found.
    If multiple occurrences are found, they must all be the same value.

    GitHub Actions logs have timestamps at the beginning of each line like:
    "2024-01-15T10:30:00.000Z ##[notice]Points 10/10"

    Args:
        logs: Full text of the job logs
        patterns: List of regex patterns to try (first capturing group = score)

    Returns:
        ScoreResult with found score or error message

    Examples:
        >>> logs = "2024-01-15T10:30:00.000Z Points 10.5\\n"
        >>> patterns = [r'Points\\s+([\\d.,]+)']
        >>> result = extract_score_from_logs(logs, patterns)
        >>> result.found
        '10.5'
    """
    import logging
    logger = logging.getLogger(__name__)

    if not logs:
        return ScoreResult(found=None, error="Логи пусты")

    if not patterns:
        return ScoreResult(found=None, error="Паттерны для поиска баллов не указаны")

    # Debug: show sample of logs being searched
    logger.debug(f"Searching for score in logs (size: {len(logs)} chars, {len(logs.splitlines())} lines)")
    logger.debug(f"First 500 chars: {repr(logs[:500])}")
    # Show middle sample
    mid_point = len(logs) // 2
    logger.debug(f"Middle 500 chars (around pos {mid_point}): {repr(logs[mid_point-250:mid_point+250])}")
    logger.debug(f"Last 500 chars: {repr(logs[-500:])}")

    # Debug: Check for common report keywords to diagnose missing output
    # Try both case-sensitive and case-insensitive
    report_keywords = ['ИТОГОВЫЙ ОТЧЁТ', 'ИТОГО:', 'баллов', 'Студент', 'РЕЗУЛЬТАТЫ ПО БЛОКАМ',
                       'ОЦЕНКА', 'ЖУРНАЛ', 'ПРЕДВАРИТЕЛЬНАЯ']
    found_any_keyword = False

    for keyword in report_keywords:
        # Try exact match first
        if keyword in logs:
            logger.debug(f"✓ Found keyword '{keyword}' in logs (case-sensitive)")
            idx = logs.find(keyword)
            start = max(0, idx - 100)
            end = min(len(logs), idx + 200)
            logger.debug(f"  Position: {idx}, Context: {repr(logs[start:end])}")
            found_any_keyword = True
            break
        # Try lowercase
        elif keyword.lower() in logs.lower():
            logger.debug(f"✓ Found keyword '{keyword}' in logs (case-insensitive)")
            logs_lower = logs.lower()
            idx = logs_lower.find(keyword.lower())
            start = max(0, idx - 100)
            end = min(len(logs), idx + 200)
            logger.debug(f"  Position: {idx}, Context: {repr(logs[start:end])}")
            found_any_keyword = True
            break

    if not found_any_keyword:
        logger.warning("⚠️ None of the report keywords found in logs.")
        logger.debug(f"Searched for keywords: {report_keywords}")
        # Show more samples to help diagnose
        logger.debug("Sample lines from different parts of logs:")
        lines = logs.splitlines()
        if len(lines) > 100:
            # Show lines from 25%, 50%, 75% positions
            for pct in [25, 50, 75]:
                idx = len(lines) * pct // 100
                logger.debug(f"  Line {idx} ({pct}%): {repr(lines[idx][:150])}")
        # Check timestamp around expected score time
        if '05:22:34' in logs or '05:22:33' in logs or '05:22:35' in logs:
            logger.debug("✓ Found timestamps around 05:22:34 in logs")
            # Show lines with this timestamp
            matching_lines = [line for line in logs.splitlines() if '05:22:34' in line or '05:22:33' in line or '05:22:35' in line]
            logger.debug(f"Found {len(matching_lines)} lines with timestamps 05:22:33-35. First 5:")
            for line in matching_lines[:5]:
                logger.debug(f"  {repr(line[:200])}")
        else:
            logger.debug("✗ No timestamps around 05:22:34 found")

    all_matches = []
    matched_pattern = None

    # Try each pattern until we find matches
    for idx, pattern in enumerate(patterns, 1):
        logger.debug(f"Trying pattern {idx}/{len(patterns)}: {repr(pattern)}")
        try:
            # Search across all lines
            matches = re.findall(pattern, logs, re.MULTILINE | re.IGNORECASE)

            if matches:
                logger.info(f"✓ Pattern {idx} matched {len(matches)} time(s): {pattern}")
                logger.debug(f"Matched values: {matches}")
                all_matches = matches
                matched_pattern = pattern
                break
            else:
                logger.debug(f"✗ Pattern {idx} had no matches")
                # Debug: show lines containing keywords for troubleshooting
                if 'ОЦЕНКА' in pattern or 'ЖУРНАЛ' in pattern or 'ПРЕДВАРИТЕЛЬНАЯ' in pattern:
                    lines_with_keyword = [line for line in logs.split('\n') if 'ОЦЕНКА' in line or 'ЖУРНАЛ' in line]
                    if lines_with_keyword:
                        logger.debug(f"Found {len(lines_with_keyword)} lines containing ОЦЕНКА or ЖУРНАЛ. First 3:")
                        for line in lines_with_keyword[:3]:
                            logger.debug(f"  {repr(line[:200])}")  # repr to show invisible chars
                    else:
                        logger.debug("No lines found containing ОЦЕНКА or ЖУРНАЛ keywords")
        except re.error as e:
            logger.warning(f"Invalid regex pattern '{pattern}': {e}")
            continue

    if not all_matches:
        logger.debug(f"No matches found for any of {len(patterns)} pattern(s)")
        return ScoreResult(
            found=None,
            error="Баллы не найдены в логах. Убедитесь, что программа выводит набранный балл."
        )

    # Normalize all matches
    normalized_matches = [normalize_score(m) for m in all_matches]

    # Check all matches are the same value (allow different separators)
    unique_scores = []
    for score in normalized_matches:
        # Check if this score is already in unique list (considering "10.5" == "10,5")
        is_duplicate = any(scores_equal(score, existing) for existing in unique_scores)
        if not is_duplicate:
            unique_scores.append(score)

    if len(unique_scores) > 1:
        logger.warning(f"Multiple different scores found: {unique_scores}")
        return ScoreResult(
            found=None,
            error=f"Найдено несколько разных значений баллов в логах: {', '.join(unique_scores)}. Обратитесь к преподавателю."
        )

    found_score = normalized_matches[0]
    logger.info(f"Score extracted from logs: {found_score} (pattern: {matched_pattern}, {len(all_matches)} occurrence(s))")

    return ScoreResult(found=found_score)


def format_score(score: str, separator: str = '.') -> str:
    """
    Format score with the specified decimal separator.

    Args:
        score: Score string (e.g., "10.5" or "10,5")
        separator: Desired decimal separator ('.' or ',')

    Returns:
        Formatted score string

    Examples:
        >>> format_score("10.5", ",")
        '10,5'
        >>> format_score("10,5", ".")
        '10.5'
        >>> format_score("10", ",")
        '10'
    """
    if separator not in ('.', ','):
        raise ValueError(f"Invalid separator: {separator}")

    # Normalize to dot first
    normalized = score.replace(',', '.')

    # Convert to desired separator
    if separator == ',':
        return normalized.replace('.', ',')
    return normalized


def format_grade_with_score(
    base_grade: str,
    score: str,
    penalty: int = 0,
    separator: str = '.'
) -> str:
    """
    Format grade string with score and optional penalty.

    Format: base_grade@score or base_grade@score-penalty

    Args:
        base_grade: Base grade symbol (e.g., "v" for success)
        score: Score value (e.g., "10.5")
        penalty: Number of penalty points (default: 0)
        separator: Decimal separator for score ('.' or ',')

    Returns:
        Grade string, e.g., "v@10.5" or "v@10,5-3"

    Examples:
        >>> format_grade_with_score("v", "10.5", 0, ".")
        'v@10.5'
        >>> format_grade_with_score("v", "10.5", 3, ",")
        'v@10,5-3'
        >>> format_grade_with_score("v", "10", 0, ".")
        'v@10'
    """
    formatted_score = format_score(score, separator)

    if penalty > 0:
        return f"{base_grade}@{formatted_score}-{penalty}"
    return f"{base_grade}@{formatted_score}"
