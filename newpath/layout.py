"""Validate the independent, pixel-based single-slide scene contract.

No prior scene format is accepted or translated here. All coordinates are in
the authored 1920 by 1080 space; rendering is a direct unit conversion.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

FORMAT = "newpath.scene/1"
FONT = "Microsoft YaHei"
SHAPES = {"rect", "roundRect", "ellipse", "triangle", "chevron", "leftArrow",
          "rightArrow", "upArrow", "downArrow", "leftRightArrow", "upDownArrow"}
ARROWS = {"none", "triangle", "stealth", "diamond", "oval"}


def _number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{label} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{label} must be nonnegative")
    return value


def _box(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} must be [x, y, width, height]")
    return [_number(v, f"{label}[{i}]", positive=i >= 2) for i, v in enumerate(value)]


def _color(value, label, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"#?[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{label} must be a six-digit RGB color")
    return value.lstrip("#").upper()


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{label} must be one of {', '.join(sorted(choices))}")
    return value


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _text(value, label, *, single=True, nonempty=True):
    if not isinstance(value, str) or (nonempty and not value):
        raise ValueError(f"{label} must be a nonempty string" if nonempty else f"{label} must be a string")
    if single and any(character in value for character in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
        raise ValueError(f"{label} must contain one visible line; use a separate text item for each line")
    return value


def _font(value, expected, label):
    if value != expected:
        raise ValueError(f"{label} font must be {expected}")
    return expected


def _typography(src, label):
    return {
        "size": _number(src.get("size", 32), f"{label}.size", positive=True),
        "color": _color(src.get("color", "000000"), f"{label}.color"),
        "bold": _boolean(src.get("bold", False), f"{label}.bold"),
        "italic": _boolean(src.get("italic", False), f"{label}.italic"),
        "underline": _boolean(src.get("underline", False), f"{label}.underline"),
    }


def normalize_layout(payload: dict) -> dict:
    """Expand named styles, validate values and return one canonical scene.

    This function is pure. IDs, derived order and defaults are stable on a
    second call, so a saved canonical scene can be rendered directly.
    """
    if not isinstance(payload, dict):
        raise ValueError("layout must be an object")
    if payload.get("format", FORMAT) != FORMAT:
        raise ValueError(f"format must be {FORMAT}")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("layout.items must be a list")
    styles = payload.get("styles", {})
    if not isinstance(styles, dict) or any(not isinstance(v, dict) for v in styles.values()):
        raise ValueError("styles must map names to style objects")
    used = set()
    explicit = {i.get("id") for i in raw_items if isinstance(i, dict) and isinstance(i.get("id"), str)}
    result = []
    common = {"kind", "id", "box", "order", "z", "style"}
    fields = {
        "text": {"text", "runs", "size", "color", "bold", "italic", "underline", "font", "align", "vertical", "rotation", "tracking"},
        "shape": {"shape", "fill", "stroke", "stroke_width", "dash", "opacity", "rotation"},
        "line": {"points", "stroke", "stroke_width", "dash", "start_arrow", "end_arrow"},
        "table": {"rows", "column_widths", "row_heights", "size", "color", "fill", "gridcolor", "grid_width", "font"},
        "art": {"description", "target", "presentation", "lettering"},
        "formula": {"text", "size", "color", "font"},
    }
    next_id = 1
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"item {index + 1} must be an object")
        src = deepcopy(raw)
        if "style" in src:
            name = src["style"]
            if not isinstance(name, str) or name not in styles:
                raise ValueError(f"item {index + 1} references an unknown style")
            src = {**deepcopy(styles[name]), **src}
            src.pop("style", None)
        kind = src.get("kind")
        if not isinstance(kind, str) or kind not in fields:
            raise ValueError(f"item {index + 1} kind must be text, shape, line, table, art or formula")
        unknown = set(src) - common - fields[kind]
        if unknown:
            raise ValueError(f"item {index + 1} has unsupported {kind} fields: {', '.join(sorted(unknown))}")
        ident = src.get("id")
        if ident is None:
            while f"o{next_id:03d}" in explicit or f"o{next_id:03d}" in used:
                next_id += 1
            ident = f"o{next_id:03d}"
            next_id += 1
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", ident):
            raise ValueError(f"item {index + 1} id must be a safe unique name")
        if ident in used:
            raise ValueError(f"duplicate item id: {ident}")
        used.add(ident)
        item = {"id": ident, "kind": kind, "order": index}
        if "z" in src:
            item["z"] = _number(src["z"], f"{ident}.z")
        if kind != "line":
            item["box"] = _box(src.get("box"), f"{ident}.box")
        elif "box" in src:
            item["box"] = _box(src["box"], f"{ident}.box")
        if kind == "text":
            item.update(_typography(src, ident))
            item["font"] = _font(src.get("font", FONT), FONT, ident)
            item["align"] = _choice(src.get("align", "left"), {"left", "center", "right"}, f"{ident}.align")
            item["vertical"] = _boolean(src.get("vertical", False), f"{ident}.vertical")
            item["rotation"] = _number(src.get("rotation", 0), f"{ident}.rotation")
            item["tracking"] = _number(src.get("tracking", 0), f"{ident}.tracking")
            if item["vertical"] and item["rotation"] != 0:
                raise ValueError(f"{ident}: vertical text rotation must be zero")
            if "runs" in src:
                runs = src["runs"]
                if not isinstance(runs, list) or not runs:
                    raise ValueError(f"{ident}.runs must be a nonempty list")
                clean = []
                for ri, run in enumerate(runs):
                    if not isinstance(run, dict) or set(run) - {"text", "size", "color", "bold", "italic", "underline"}:
                        raise ValueError(f"{ident}.runs[{ri}] has unsupported fields")
                    nr = {"text": _text(run.get("text"), f"{ident}.runs[{ri}].text", nonempty=False)}
                    for key in ("size", "color", "bold", "italic", "underline"):
                        if key in run:
                            nr[key] = _typography({**item, **run}, ident)[key]
                    clean.append(nr)
                joined = "".join(r["text"] for r in clean)
                if "text" in src and src["text"] != joined:
                    raise ValueError(f"{ident}.runs must concatenate exactly to text")
                item["runs"] = clean
                item["text"] = _text(joined, f"{ident}.text")
            else:
                item["text"] = _text(src.get("text"), f"{ident}.text")
        elif kind == "shape":
            item["shape"] = _choice(src.get("shape", "rect"), SHAPES, f"{ident}.shape")
            item["fill"] = _color(src.get("fill"), f"{ident}.fill", nullable=True)
            item["stroke"] = _color(src.get("stroke"), f"{ident}.stroke", nullable=True)
            item["stroke_width"] = _number(src.get("stroke_width", 1), f"{ident}.stroke_width", nonnegative=True)
            item["dash"] = _choice(src.get("dash", "solid"), {"solid", "dash", "dot"}, f"{ident}.dash")
            item["opacity"] = _number(src.get("opacity", 1), f"{ident}.opacity", nonnegative=True)
            if item["opacity"] > 1:
                raise ValueError(f"{ident}.opacity must be between zero and one")
            item["rotation"] = _number(src.get("rotation", 0), f"{ident}.rotation")
        elif kind == "line":
            points = src.get("points")
            if not isinstance(points, (list, tuple)) or len(points) != 4:
                raise ValueError(f"{ident}.points must be [x1, y1, x2, y2]")
            item["points"] = [_number(v, f"{ident}.points") for v in points]
            if points[:2] == points[2:]:
                raise ValueError(f"{ident}.points must define a nonzero straight line")
            item["stroke"] = _color(src.get("stroke", "000000"), f"{ident}.stroke")
            item["stroke_width"] = _number(src.get("stroke_width", 1), f"{ident}.stroke_width", nonnegative=True)
            item["dash"] = _choice(src.get("dash", "solid"), {"solid", "dash", "dot"}, f"{ident}.dash")
            for key in ("start_arrow", "end_arrow"):
                item[key] = _choice(src.get(key, "none"), ARROWS, f"{ident}.{key}")
        elif kind == "table":
            rows = src.get("rows")
            if not isinstance(rows, list) or not rows or any(not isinstance(row, list) or not row for row in rows):
                raise ValueError(f"{ident}.rows must be a nonempty rectangular array")
            columns = len(rows[0])
            if any(len(row) != columns for row in rows):
                raise ValueError(f"{ident}.rows must be rectangular")
            item["rows"] = [[_text(v, f"{ident}.cell", single=False, nonempty=False) for v in row] for row in rows]
            for key, count, total in (("column_widths", columns, item["box"][2]), ("row_heights", len(rows), item["box"][3])):
                dimensions = src.get(key, [total / count] * count)
                if not isinstance(dimensions, (list, tuple)) or len(dimensions) != count:
                    raise ValueError(f"{ident}.{key} must have {count} entries")
                item[key] = [_number(v, f"{ident}.{key}", positive=True) for v in dimensions]
                if not math.isclose(sum(dimensions), total, rel_tol=1e-7, abs_tol=1e-5):
                    raise ValueError(f"{ident}.{key} must sum to the table box dimension")
            item["size"] = _number(src.get("size", 32), f"{ident}.size", positive=True)
            item["color"] = _color(src.get("color", "000000"), f"{ident}.color")
            item["fill"] = _color(src.get("fill"), f"{ident}.fill", nullable=True)
            item["gridcolor"] = _color(src.get("gridcolor", "000000"), f"{ident}.gridcolor")
            item["grid_width"] = _number(src.get("grid_width", 1), f"{ident}.grid_width", nonnegative=True)
            item["font"] = _font(src.get("font", FONT), FONT, ident)
        elif kind == "art":
            item["description"] = _text(src.get("description"), f"{ident}.description", single=False)
            item["target"] = _box(src.get("target", item["box"]), f"{ident}.target")
            item["presentation"] = _choice(src.get("presentation", "cutout"), {"cutout", "rectangle"}, f"{ident}.presentation")
            if src.get("lettering") is not None:
                item["lettering"] = _text(src["lettering"], f"{ident}.lettering", single=False)
        else:
            item["text"] = _text(src.get("text"), f"{ident}.text")
            item["size"] = _number(src.get("size", 32), f"{ident}.size", positive=True)
            item["color"] = _color(src.get("color", "000000"), f"{ident}.color")
            item["font"] = _font(src.get("font", "Cambria Math"), "Cambria Math", ident)
        result.append(item)
    return {"format": FORMAT, "items": result}
