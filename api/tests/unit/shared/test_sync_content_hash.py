from shared.sync_content_hash import compute_sync_content_hash, normalize_line_endings


def test_normalize_line_endings_collapses_crlf():
    assert normalize_line_endings(b"a\r\nb") == b"a\nb"


def test_normalize_line_endings_preserves_binary():
    binary = b"\x00\xff\r\n"
    assert normalize_line_endings(binary) == binary


def test_compute_sync_content_hash_matches_cli_semantics():
    crlf = b"line one\r\nline two\r\n"
    lf = b"line one\nline two\n"
    assert compute_sync_content_hash(crlf) == compute_sync_content_hash(lf)
