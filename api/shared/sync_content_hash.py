"""Content hash used by CLI sync/pull/watch to compare local and server files.

Storage providers expose opaque ETags (especially Azure Blob) that do not match
the normalized MD5 the CLI computes after CRLF normalization. Use this helper
everywhere sync semantics need a stable content fingerprint.
"""

from __future__ import annotations

import hashlib


def normalize_line_endings(data: bytes) -> bytes:
    """Normalize CRLF to LF for text files. Binary files pass through unchanged."""
    if b"\x00" in data[:8192]:
        return data
    return data.replace(b"\r\n", b"\n")


def compute_sync_content_hash(raw_bytes: bytes) -> str:
    """Return md5 hex digest of normalized bytes — matches bifrost CLI sync."""
    return hashlib.md5(normalize_line_endings(raw_bytes)).hexdigest()  # NOSONAR - content fingerprint, not crypto
