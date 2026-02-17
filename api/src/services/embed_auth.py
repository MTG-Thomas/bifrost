"""HMAC verification for embedded app authentication."""

import hashlib
import hmac as hmac_module


def compute_embed_hmac(params: dict[str, str], secret: str) -> str:
    """Compute HMAC-SHA256 over sorted query params (Shopify-style).

    Args:
        params: Query parameters (excluding 'hmac' key).
        secret: Shared secret.

    Returns:
        Hex-encoded HMAC digest.
    """
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def verify_embed_hmac(query_params: dict[str, str], secret: str) -> bool:
    """Verify an HMAC-signed set of query parameters.

    Removes the 'hmac' key, sorts the remaining params alphabetically,
    joins as key=value&key=value, and verifies against HMAC-SHA256.

    Args:
        query_params: All query parameters including 'hmac'.
        secret: Shared secret.

    Returns:
        True if the HMAC is valid.
    """
    received_hmac = query_params.get("hmac")
    if not received_hmac:
        return False

    remaining = {k: v for k, v in query_params.items() if k != "hmac"}
    expected = compute_embed_hmac(remaining, secret)
    return hmac_module.compare_digest(expected, received_hmac)
