from local_changes_viewer.core.services.file_info import detect_encoding, detect_line_ending


def test_detects_lf() -> None:
    assert detect_line_ending(b"a\nb\nc\n") == "LF"


def test_detects_crlf() -> None:
    assert detect_line_ending(b"a\r\nb\r\nc\r\n") == "CRLF"


def test_detects_mixed_line_endings() -> None:
    assert detect_line_ending(b"a\r\nb\nc\n") == "Mixed"


def test_no_line_endings() -> None:
    assert detect_line_ending(b"no newlines here") == "N/A"


def test_detects_utf8() -> None:
    assert detect_encoding("hello".encode("utf-8")) == "UTF-8"


def test_detects_utf8_bom() -> None:
    assert detect_encoding(b"\xef\xbb\xbfhello") == "UTF-8 (BOM)"


def test_detects_binary() -> None:
    assert detect_encoding(b"\x00\x01\x02") == "Binary"


def test_detects_latin1_fallback() -> None:
    assert detect_encoding(b"caf\xe9") == "Latin-1"
