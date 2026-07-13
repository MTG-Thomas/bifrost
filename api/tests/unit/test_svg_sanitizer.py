"""Unit tests for SVG sanitizer."""

import pytest

from shared.svg_sanitizer import SvgSanitizationError, sanitize_svg


CLEAN_SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="4"/></svg>'


def test_clean_svg_round_trips():
    out = sanitize_svg(CLEAN_SVG)
    assert b"<circle" in out
    assert b"<svg" in out


def test_script_element_removed():
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="1"/></svg>'
    out = sanitize_svg(payload)
    assert b"script" not in out.lower()
    assert b"<circle" in out


def test_event_handler_attribute_removed():
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1" onclick="alert(1)" onload="x()"/></svg>'
    out = sanitize_svg(payload)
    assert b"onclick" not in out.lower()
    assert b"onload" not in out.lower()
    assert b"<circle" in out


def test_allowlisted_logo_features_are_preserved():
    payload = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
      <defs><linearGradient id="brand"><stop offset="0" stop-color="#123456"/></linearGradient></defs>
      <g transform="translate(1 1)" style="fill:url(#brand);stroke:#fff;stroke-width:2">
        <path d="M0 0h10v10z"/><text x="2" y="8">Bifrost</text>
      </g>
    </svg>"""
    out = sanitize_svg(payload)

    assert b"linearGradient" in out
    assert b"fill:url(#brand)" in out
    assert b"stroke:#fff" in out
    assert b"<path" in out
    assert b"<text" in out


def test_local_references_and_embedded_raster_image_are_preserved():
    payload = b"""<svg xmlns="http://www.w3.org/2000/svg">
      <defs><path id="shape" d="M0 0h1v1z"/></defs>
      <use href="#shape"/>
      <image href="data:image/png;base64,iVBORw0KGgo=" width="1" height="1"/>
    </svg>"""
    out = sanitize_svg(payload)

    assert b'href="#shape"' in out
    assert b"data:image/png;base64,iVBORw0KGgo=" in out


def test_javascript_href_removed():
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><circle r="1"/></a></svg>'
    out = sanitize_svg(payload)
    assert b"javascript:" not in out.lower()


def test_xlink_javascript_href_removed():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<a xlink:href="javascript:alert(1)"><circle r="1"/></a></svg>'
    )
    out = sanitize_svg(payload)
    assert b"javascript:" not in out.lower()


def test_foreign_object_and_active_elements_are_removed_with_their_content():
    payload = b"""<svg xmlns="http://www.w3.org/2000/svg">
      <foreignObject><div xmlns="http://www.w3.org/1999/xhtml"><script>alert(1)</script></div></foreignObject>
      <animate attributeName="href" values="safe;javascript:alert(1)"/>
      <set attributeName="onload" to="alert(1)"/>
      <rect width="10" height="10"/>
    </svg>"""
    out = sanitize_svg(payload)

    assert b"foreignObject" not in out
    assert b"script" not in out.lower()
    assert b"animate" not in out.lower()
    assert b"<set" not in out.lower()
    assert b"<rect" in out


def test_active_urls_events_and_unsafe_css_are_removed():
    payload = b"""<svg xmlns="http://www.w3.org/2000/svg">
      <a href="javascript&#x3a;alert(1)" target="_top"><rect onload="alert(1)"/></a>
      <use href="https://attacker.example/payload.svg#x"/>
      <image href="data:image/svg+xml;base64,PHN2Zy8+"/>
      <path fill="url(https://attacker.example/p.svg#x)"
            clip-path="url(javascript:alert(1))"
            style="stroke:url(#safe);fill:url(javascript:alert(1));background:url(https://attacker.example/x)"/>
    </svg>"""
    out = sanitize_svg(payload)

    assert b"javascript" not in out.lower()
    assert b"attacker.example" not in out
    assert b"data:image/svg+xml" not in out
    assert b"onload" not in out.lower()
    assert b"target=" not in out
    assert b"stroke:url(#safe)" in out


def test_css_escape_and_comment_url_obfuscation_are_removed():
    payload = rb"""<svg xmlns="http://www.w3.org/2000/svg">
      <path fill="u\72l(javascript:alert(1))"
            stroke="u/**/rl(https://attacker.example/x)"
            style="fill:u\72l(javascript:alert(1));stroke:u/**/rl(https://attacker.example/x);color:#123"/>
    </svg>"""
    out = sanitize_svg(payload)

    assert b"javascript" not in out.lower()
    assert b"attacker.example" not in out
    assert b"fill=" not in out
    assert b"stroke=" not in out
    assert b"color:#123" in out


def test_non_svg_and_foreign_namespace_roots_are_rejected():
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(b'<html xmlns="http://www.w3.org/1999/xhtml"/>')
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(b'<svg xmlns="https://attacker.example/svg"/>')


def test_xxe_blocked():
    payload = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
    )
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(payload)


def test_malformed_xml_rejected():
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(b"<svg><not-closed>")


def test_inkscape_style_doctype_accepted():
    """SVG editors (Inkscape, Illustrator) emit a benign DOCTYPE referencing the
    SVG 1.1 DTD; we must accept it because real-world logo files commonly include
    it. The XXE / billion-laughs vectors are entity declarations and external
    resolution, both of which remain blocked."""
    payload = (
        b'<?xml version="1.0" standalone="no"?>'
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        b'<circle cx="5" cy="5" r="4"/></svg>'
    )
    out = sanitize_svg(payload)
    assert b"<circle" in out


def test_billion_laughs_blocked():
    payload = (
        b'<?xml version="1.0"?>'
        b"<!DOCTYPE lolz ["
        b'<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b"]>"
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&lol2;</text></svg>'
    )
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(payload)
