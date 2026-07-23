def detect_line_ending(content: bytes) -> str:
    has_crlf = b"\r\n" in content
    has_lf = b"\n" in content.replace(b"\r\n", b"")
    if has_crlf and has_lf:
        return "Mixed"
    if has_crlf:
        return "CRLF"
    if has_lf:
        return "LF"
    return "N/A"


def detect_encoding(content: bytes) -> str:
    if b"\x00" in content:
        return "Binary"
    if content.startswith(b"\xef\xbb\xbf"):
        return "UTF-8 (BOM)"
    try:
        content.decode("utf-8")
        return "UTF-8"
    except UnicodeDecodeError:
        pass
    try:
        content.decode("latin-1")
        return "Latin-1"
    except UnicodeDecodeError:
        return "Unknown"
