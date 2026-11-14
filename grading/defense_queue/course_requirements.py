"""Список работ для локальной защиты по баллам из листа «График»."""
import re
import math


def matches_group(pattern, group):
    for part in re.split(r'[,;]', pattern):
        expression = ''.join('[0-9]' if char in 'XxХх' else re.escape(char) for char in part.strip())
        if expression and re.fullmatch(expression, group, flags=re.I):
            return True
    return False


def scored_labs(graph, group):
    """Вернуть short-name → наличие баллов; неизвестные строки не выдумываются."""
    header = next((index for index, row in enumerate(graph) if any('Баллы (макс.)' in cell for cell in row)), None)
    if header is None or header+1 >= len(graph):
        return {}
    labels = graph[header+1]
    score_start = next(index for index, cell in enumerate(graph[header]) if 'Баллы (макс.)' in cell)
    date_start = next((index for index, cell in enumerate(graph[header]) if 'Срок сдачи' in cell), None)
    column = None
    for index in range(score_start, len(labels)):
        label = labels[index]
        if not label.strip():
            break
        alias = labels[date_start + index-score_start] if date_start is not None and date_start+index-score_start < len(labels) else ''
        if matches_group(label, group) or matches_group(alias, group):
            column = index
            break
    if column is None:
        return {}
    result = {}
    for row in graph[header+2:]:
        label = next((re.match(r'^(ЛР\d+(?:\.\d+)?)(?:[.\s]|$)', cell.strip()) for cell in row[:score_start]
                      if re.match(r'^ЛР\d', cell.strip())), None)
        if label is None:
            continue
        raw = row[column].strip() if column < len(row) else ''
        if raw in ('', '-'):
            result[label[1]] = False
        else:
            try:
                points = float(raw.replace(',', '.'))
                if math.isfinite(points):
                    result[label[1]] = points > 0
            except ValueError:
                continue
    return result
