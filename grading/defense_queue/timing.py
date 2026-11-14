"""Граница обслуживания определяется календарём занятия, а не часами сервера."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def service_ends_at(session):
    zone = ZoneInfo(session['timezone'])
    # Пара, окончившаяся ровно в полночь, относится к предыдущему дню.
    last_day = datetime.fromtimestamp(session['ends_at'] - 1, zone).date()
    return int(datetime.combine(last_day + timedelta(days=1), time.min, zone).timestamp())
