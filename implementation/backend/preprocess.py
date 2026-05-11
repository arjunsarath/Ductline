from __future__ import annotations

import io
import re
from xml.etree import ElementTree as ET

import fitz  # PyMuPDF

BBox = tuple[float, float, float, float]  # (x0, top, x1, bottom)

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
# ET parses out the namespace into Clark notation `{ns}tag` on every element.
_TAG = re.compile(r"^\{[^}]+\}")


def _color_str_to_luma(s: str | None) -> float | None:
    """Parse an SVG colour into max-channel luma in [0, 1]. None = unset."""
    if s is None:
        return None
    v = s.strip().lower()
    if v == "" or v == "none" or v == "transparent" or v == "currentcolor":
        return None
    if v == "black":
        return 0.0
    if v == "white":
        return 1.0
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            r, g, b = (int(h[i] * 2, 16) for i in range(3))
        elif len(h) == 6:
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
        else:
            return None
        return max(r, g, b) / 255.0
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", v)
    if m:
        return max(int(c) for c in m.groups()) / 255.0
    return None


def _style_props(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in style.split(";"):
        if ":" in part:
            k, _, val = part.partition(":")
            out[k.strip().lower()] = val.strip()
    return out


def _is_black_paint(value: str | None, max_luma: float) -> bool:
    luma = _color_str_to_luma(value)
    if luma is None:
        # SVG default fill = black, default stroke = none. Treat unset as black
        # only when used for fill — strokes that aren't specified aren't drawn.
        return True
    return luma <= max_luma


def _local(tag: str) -> str:
    return _TAG.sub("", tag)


def _filter_node(node: ET.Element, max_luma: float) -> bool:
    """Return True if the node should be kept. Style is mutated in place when
    needed (e.g. setting stroke or fill to none to suppress only one of them)."""
    style = node.get("style", "")
    style_props = _style_props(style) if style else {}

    fill_attr = style_props.get("fill") or node.get("fill")
    stroke_attr = style_props.get("stroke") or node.get("stroke")

    # SVG semantics: if both are explicitly "none" the element is invisible —
    # often a clip-path or layout helper, keep it.
    if fill_attr == "none" and stroke_attr == "none":
        return True

    fill_black = (
        fill_attr is not None and fill_attr != "none"
        and _color_str_to_luma(fill_attr) is not None
        and _is_black_paint(fill_attr, max_luma)
    )
    stroke_black = (
        stroke_attr is not None and stroke_attr != "none"
        and _color_str_to_luma(stroke_attr) is not None
        and _is_black_paint(stroke_attr, max_luma)
    )

    # If the element has no fill/stroke information at all it inherits — leave
    # it (group containers, defs, etc.).
    if fill_attr is None and stroke_attr is None:
        return True

    keep = fill_black or stroke_black
    if not keep:
        return False
    # Suppress the non-black side so a half-black-half-grey element doesn't
    # leak the grey through. Style attribute wins over presentation attributes
    # in SVG, so prefer modifying style if it exists.
    if fill_attr is not None and not fill_black:
        if "fill" in style_props:
            style_props["fill"] = "none"
            node.set("style", "; ".join(f"{k}: {v}" for k, v in style_props.items()))
        else:
            node.set("fill", "none")
    if stroke_attr is not None and not stroke_black:
        if "stroke" in style_props:
            style_props["stroke"] = "none"
            node.set("style", "; ".join(f"{k}: {v}" for k, v in style_props.items()))
        else:
            node.set("stroke", "none")
    return True


def _walk_and_filter(node: ET.Element, max_luma: float) -> None:
    children = list(node)
    for child in children:
        # Skip <defs> contents — we only want to filter visible graphics.
        if _local(child.tag) == "defs":
            continue
        keep = _filter_node(child, max_luma)
        if not keep:
            node.remove(child)
        else:
            _walk_and_filter(child, max_luma)


def build_preprocessed_svg(
    pdf_bytes: bytes,
    page_number: int,
    crop_bbox: BBox,
    black_threshold: float,
) -> str:
    """Render the cropped page to SVG via PyMuPDF, drop every non-black
    drawing element. Returns an SVG string for the frontend to embed inline."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_number - 1]
        x0, top, x1, bottom = crop_bbox
        clip = fitz.Rect(x0, top, x1, bottom)
        svg = page.get_svg_image(matrix=fitz.Matrix(1, 1), text_as_path=False)
        # PyMuPDF doesn't honour `clip` for get_svg_image in all versions, but
        # the SVG carries a viewBox we can rewrite — see below.
    finally:
        doc.close()

    tree = ET.fromstring(svg)
    _walk_and_filter(tree, black_threshold)

    # Crop the SVG to the user-selected region and let CSS size it — the
    # viewer renders the SVG inside a div that owns its physical dimensions.
    # We strip width/height entirely so the browser uses the host element's
    # CSS box (set by the viewer) instead of the SVG's intrinsic size.
    crop_w = x1 - x0
    crop_h = bottom - top
    tree.set("viewBox", f"{x0} {top} {crop_w} {crop_h}")
    tree.set("preserveAspectRatio", "xMinYMin meet")
    tree.attrib.pop("width", None)
    tree.attrib.pop("height", None)

    return ET.tostring(tree, encoding="unicode")
