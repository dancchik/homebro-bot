"""Parsing of free-text user input: numbers, floors, dates, price ranges."""
import re
from datetime import date, timedelta

FLOOR_RE = re.compile(r"(\d{1,2})\s*(?:/|\\|из|din|of|,|\s)\s*(\d{1,2})", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}|\d{2}))?")


def parse_number(text: str) -> int | None:
    """'58', '58.5 м²', '1 200 €' -> 58, 58, 1200."""
    match = re.search(r"\d[\d ]*(?:[.,]\d+)?", text)
    if not match:
        return None
    return round(float(match.group().replace(" ", "").replace(",", ".")))


def parse_floor(text: str) -> tuple[int, int] | None:
    """'5/9', '5 из 9', '5 9' -> (5, 9)."""
    match = FLOOR_RE.fullmatch(text.strip())
    if not match:
        return None
    floor, total = int(match[1]), int(match[2])
    if not 1 <= floor <= total <= 40:
        return None
    return floor, total


def parse_date(text: str, today: date | None = None) -> date | None:
    """'01.12', '1.12.2026', '01/12/26' -> date within the next year."""
    today = today or date.today()
    match = DATE_RE.fullmatch(text.strip())
    if not match:
        return None
    day, month = int(match[1]), int(match[2])
    year = int(match[3]) if match[3] else today.year
    if year < 100:
        year += 2000
    try:
        result = date(year, month, day)
        # "01.02" typed in December means next year
        if not match[3] and result < today:
            result = date(year + 1, month, day)
    except ValueError:
        return None
    if not today <= result <= today + timedelta(days=366):
        return None
    return result


def parse_price_range(text: str) -> tuple[int | None, int] | None:
    """'400' -> (None, 400); '300-500' -> (300, 500); '1 200' -> (None, 1200)."""
    # Join thousands groups like "1 200" before splitting into numbers
    text = re.sub(r"(?<=\d)\s+(?=\d{3}(?!\d))", "", text)
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if len(numbers) == 1 and numbers[0] > 0:
        return None, numbers[0]
    if len(numbers) == 2:
        low, high = sorted(numbers)
        if high > 0:
            return low, high
    return None
