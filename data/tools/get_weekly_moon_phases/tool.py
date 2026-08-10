import datetime
import math

SYNODIC_MONTH = 29.53058867
# Known New Moon epoch: Jan 11, 2024, 11:57 UTC => Julian Day 2460320.99792
EPOCH_JD = 2460320.99792


def _julian_day(year: int, month: int, day: int, hour: float = 12.0) -> float:
    """Calculates the Julian Day number for a given calendar date and time."""
    if month <= 2:
        year -= 1
        month += 12
    a = math.floor(year / 100)
    b = 2 - a + math.floor(a / 4)
    jd = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + (hour / 24.0) + b - 1524.5
    return jd


def _calculate_moon_phase(date_obj: datetime.date):
    """Calculates age, illumination, phase name, and emoji for a date."""
    jd = _julian_day(date_obj.year, date_obj.month, date_obj.day)
    days_since_epoch = jd - EPOCH_JD
    age = days_since_epoch % SYNODIC_MONTH
    normalized = age / SYNODIC_MONTH
    illumination = (1 - math.cos(2 * math.pi * normalized)) / 2 * 100

    if normalized < 0.03 or normalized >= 0.97:
        name, emoji = "New Moon", "🌑"
    elif normalized < 0.22:
        name, emoji = "Waxing Crescent", "🌒"
    elif normalized < 0.28:
        name, emoji = "First Quarter", "🌓"
    elif normalized < 0.47:
        name, emoji = "Waxing Gibbous", "🌔"
    elif normalized < 0.53:
        name, emoji = "Full Moon", "🌕"
    elif normalized < 0.72:
        name, emoji = "Waning Gibbous", "🌖"
    elif normalized < 0.78:
        name, emoji = "Third Quarter", "🌗"
    else:
        name, emoji = "Waning Crescent", "🌘"

    return age, illumination, name, emoji


def get_weekly_moon_phases(start_date: str = "", days: int = 7) -> str:
    """Calculates and returns the moon phase, illumination percentage, and lunar age for each day of the specified or current week.

    Args:
        start_date: The start date in 'YYYY-MM-DD' format. If empty, defaults to the Monday of the current week.
        days: Number of days to calculate (1 to 31, default is 7 for a full week).
    """
    if not start_date or not str(start_date).strip():
        today = datetime.date.today()
        start = today - datetime.timedelta(days=today.weekday())
    else:
        try:
            start = datetime.datetime.strptime(str(start_date).strip(), "%Y-%m-%d").date()
        except ValueError:
            return f"Error: Invalid date format '{start_date}'. Please use 'YYYY-MM-DD'."

    if not isinstance(days, int) or days < 1 or days > 31:
        return "Error: Argument 'days' must be an integer between 1 and 31."

    end_date = start + datetime.timedelta(days=days - 1)

    header = f"### 🌙 Moon Phases ({start.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')})\n\n"
    table_header = "| Date | Day | Phase | Illumination | Lunar Age |\n| :--- | :--- | :--- | :--- | :--- |\n"
    rows = []

    for i in range(days):
        current_date = start + datetime.timedelta(days=i)
        age, illumination, name, emoji = _calculate_moon_phase(current_date)
        day_str = current_date.strftime("%Y-%m-%d")
        day_name = current_date.strftime("%A")
        rows.append(f"| {day_str} | {day_name} | {emoji} {name} | {illumination:.1f}% | {age:.1f} days |")

    return header + table_header + "\n".join(rows)
