from datetime import datetime, timezone


def format_timestamp(iso_timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso_timestamp


def format_review_time(iso_timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return iso_timestamp

    elapsed_minutes = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    if 0 <= elapsed_minutes < 4 * 60:
        minutes = max(1, round(elapsed_minutes))
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit} ago"
    return format_timestamp(iso_timestamp)
