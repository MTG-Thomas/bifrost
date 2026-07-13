"""Allowlist-based sanitization for SVG images served from Bifrost origins."""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element, register_namespace, tostring

from defusedxml import ElementTree as DefusedET

_SVG_NS = "http://www.w3.org/2000/svg"  # NOSONAR - W3C namespace identifier.
_XLINK_NS = "http://www.w3.org/1999/xlink"  # NOSONAR - W3C namespace identifier.
_XML_NS = "http://www.w3.org/XML/1998/namespace"  # NOSONAR - W3C namespace identifier.

# These cover ordinary vector logos, gradients, clipping, masks, patterns, and
# embedded raster artwork. Active SVG features (scripts, animation, foreign
# content, filters, and external resources) are deliberately absent.
_ALLOWED_ELEMENTS = frozenset(
    {
        "a",
        "circle",
        "clipPath",
        "defs",
        "desc",
        "ellipse",
        "g",
        "image",
        "line",
        "linearGradient",
        "marker",
        "mask",
        "path",
        "pattern",
        "polygon",
        "polyline",
        "radialGradient",
        "rect",
        "stop",
        "svg",
        "symbol",
        "text",
        "title",
        "tspan",
        "use",
    }
)

_ALLOWED_ATTRIBUTES = frozenset(
    {
        "aria-label",
        "class",
        "clip-path",
        "clip-rule",
        "color",
        "color-interpolation",
        "cx",
        "cy",
        "d",
        "display",
        "dominant-baseline",
        "dx",
        "dy",
        "fill",
        "fill-opacity",
        "fill-rule",
        "focusable",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "gradientTransform",
        "gradientUnits",
        "height",
        "id",
        "lengthAdjust",
        "marker-end",
        "marker-mid",
        "marker-start",
        "markerHeight",
        "markerUnits",
        "markerWidth",
        "mask",
        "maskContentUnits",
        "maskUnits",
        "offset",
        "opacity",
        "orient",
        "overflow",
        "pathLength",
        "patternContentUnits",
        "patternTransform",
        "patternUnits",
        "points",
        "preserveAspectRatio",
        "r",
        "refX",
        "refY",
        "role",
        "rotate",
        "rx",
        "ry",
        "shape-rendering",
        "spreadMethod",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "style",
        "text-anchor",
        "text-rendering",
        "textLength",
        "transform",
        "vector-effect",
        "viewBox",
        "visibility",
        "width",
        "x",
        "x1",
        "x2",
        "y",
        "y1",
        "y2",
    }
)

_ALLOWED_STYLE_PROPERTIES = frozenset(
    {
        "clip-path",
        "clip-rule",
        "color",
        "display",
        "dominant-baseline",
        "fill",
        "fill-opacity",
        "fill-rule",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
        "opacity",
        "overflow",
        "shape-rendering",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "text-anchor",
        "text-rendering",
        "vector-effect",
        "visibility",
    }
)

_REFERENCE_ATTRIBUTES = frozenset(
    {
        "clip-path",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
    }
)
_PAINT_ATTRIBUTES = frozenset({"fill", "stroke"})
_COLOR_ATTRIBUTES = _PAINT_ATTRIBUTES | {"color", "stop-color"}
_FRAGMENT = r"#[A-Za-z_][A-Za-z0-9_.:-]*"
_FRAGMENT_HREF = re.compile(rf"^{_FRAGMENT}$")
_LOCAL_REFERENCE = re.compile(rf"^url\(\s*{_FRAGMENT}\s*\)$", re.IGNORECASE)
_LOCAL_PAINT = re.compile(
    rf"^url\(\s*{_FRAGMENT}\s*\)(?:\s+(?:none|currentColor|#[0-9a-f]{{3,8}}|[a-z]+))?$",
    re.IGNORECASE,
)
_SAFE_COLOR = re.compile(
    r"^(?:none|currentColor|context-fill|context-stroke|transparent|#[0-9a-f]{3,8}|[a-z]+|(?:rgb|rgba|hsl|hsla)\([0-9.,%+\-\s]+\))$",
    re.IGNORECASE,
)
_RASTER_DATA_URL = re.compile(
    r"^data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$",
    re.IGNORECASE,
)
_UNSAFE_CSS = re.compile(
    r"(?:\\|/\*|\*/|@|expression\s*\(|javascript\s*:|data\s*:|https?\s*:|file\s*:)",
    re.IGNORECASE,
)

register_namespace("", _SVG_NS)
register_namespace("xlink", _XLINK_NS)


class SvgSanitizationError(ValueError):
    """Raised when SVG input cannot be parsed into a safe SVG document."""


def _qualified_name(name: str) -> tuple[str | None, str]:
    if name.startswith("{") and "}" in name:
        namespace, local_name = name[1:].split("}", 1)
        return namespace, local_name
    return None, name


def _sanitize_url_value(element_name: str, attribute: str, value: str) -> str | None:
    value = value.strip()
    if attribute == "href":
        if element_name in {"use", "linearGradient", "radialGradient", "pattern"}:
            return value if _FRAGMENT_HREF.fullmatch(value) else None
        if element_name == "image":
            return value if _RASTER_DATA_URL.fullmatch(value) else None
        return None
    if attribute in _REFERENCE_ATTRIBUTES:
        return value if _LOCAL_REFERENCE.fullmatch(value) else None
    if attribute in _COLOR_ATTRIBUTES:
        if attribute in _PAINT_ATTRIBUTES and _LOCAL_PAINT.fullmatch(value):
            return value
        return value if _SAFE_COLOR.fullmatch(value) else None
    return value


def _sanitize_style(value: str) -> str | None:
    declarations: list[str] = []
    for declaration in value.split(";"):
        if not declaration.strip() or ":" not in declaration:
            continue
        property_name, property_value = declaration.split(":", 1)
        property_name = property_name.strip().lower()
        property_value = property_value.strip()
        if property_name not in _ALLOWED_STYLE_PROPERTIES or not property_value:
            continue
        if _UNSAFE_CSS.search(property_value):
            continue
        sanitized = _sanitize_url_value("", property_name, property_value)
        if sanitized is None or (
            "url" in sanitized.lower()
            and property_name not in _REFERENCE_ATTRIBUTES | _PAINT_ATTRIBUTES
        ):
            continue
        declarations.append(f"{property_name}:{sanitized}")
    return ";".join(declarations) or None


def _sanitize_attributes(element: Element, element_name: str) -> None:
    sanitized: dict[str, str] = {}
    for qualified_attribute, value in element.attrib.items():
        namespace, attribute = _qualified_name(qualified_attribute)
        if namespace == _XML_NS and attribute == "space":
            sanitized[qualified_attribute] = value
            continue
        if namespace not in {None, _XLINK_NS}:
            continue
        if namespace == _XLINK_NS:
            if attribute != "href":
                continue
            output_attribute = qualified_attribute
        else:
            if attribute == "href":
                output_attribute = attribute
            elif attribute not in _ALLOWED_ATTRIBUTES:
                continue
            else:
                output_attribute = attribute

        if attribute == "style":
            safe_value = _sanitize_style(value)
        else:
            safe_value = _sanitize_url_value(element_name, attribute, value)
        if safe_value is not None:
            sanitized[output_attribute] = safe_value
    element.attrib.clear()
    element.attrib.update(sanitized)


def _sanitize_element(element: Element) -> None:
    _, element_name = _qualified_name(element.tag)
    _sanitize_attributes(element, element_name)
    for child in list(element):
        namespace, child_name = _qualified_name(child.tag)
        if namespace not in {None, _SVG_NS} or child_name not in _ALLOWED_ELEMENTS:
            element.remove(child)
            continue
        _sanitize_element(child)


def sanitize_svg(data: bytes) -> bytes:
    """Return SVG bytes containing only inert, allowlisted SVG constructs."""
    try:
        root = DefusedET.fromstring(
            data,
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
    except Exception as exc:
        raise SvgSanitizationError(f"unparseable svg: {exc}") from exc

    namespace, root_name = _qualified_name(root.tag)
    if root_name != "svg" or namespace not in {None, _SVG_NS}:
        raise SvgSanitizationError("root element must be svg")

    _sanitize_element(root)
    return tostring(root, encoding="unicode").encode("utf-8")
