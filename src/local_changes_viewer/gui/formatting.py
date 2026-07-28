from datetime import datetime


def format_timestamp(iso_timestamp: str) -> str:
    try:
        return datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return iso_timestamp
