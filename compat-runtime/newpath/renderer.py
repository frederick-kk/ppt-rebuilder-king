"""Build a single editable PPTX through a local, pinned PptxGenJS runtime.

The checks here inspect package structure only. They do not launch an office
application, render a slide, or claim visual acceptance.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageFont

from .layout import normalize_layout

TEXT_FIT_POLICY = 'bounded-single-line-contain/1'
_MIN_TEXT_SCALE = .75
# Nominal ASCII digit advance when no matching face can be measured. The local
# YaHei digit samples are about .62em; .67em allows modest extra space. This is
# an estimate, never a strict upper bound for arbitrary unavailable fonts.
_ASCII_DIGIT_ADVANCE_EM = .67


def _font_directories():
    """Only inspect installed font locations; never fetch or install fonts."""
    directories = []
    if os.name == "nt":
        directories.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            directories.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    else:
        directories.extend([Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                            Path.home() / ".local" / "share" / "fonts", Path("/Library/Fonts")])
    return [p for p in directories if p.is_dir()]


def _find_font(font_name, bold=False):
    wanted = {"msyhbd.ttc", "msyhbd.ttf"} if bold else {"msyh.ttc", "msyh.ttf", "msyahei.ttf", "microsoft yahei.ttf"}
    if font_name == "Cambria Math":
        wanted = {"cambria.ttc", "cambria math.ttf", "cambriamath.ttf"}
    for directory in _font_directories():
        for path in directory.rglob("*"):
            if path.is_file() and path.name.lower() in wanted:
                return path
    return None


def _measured_width(item):
    runs = item.get("runs") or [{"text": item["text"]}]
    widths = []
    statuses = []
    faces = set()
    for run in runs:
        size = run.get("size", item["size"])
        path = _find_font(item["font"], run.get("bold", item.get("bold", False)))
        font = None
        if path is not None:
            # A collection can contain a different face at index zero (Cambria
            # Math is index one in Windows cambria.ttc). Never label another
            # family as a successful measurement of the requested font.
            for face_index in range(8 if path.suffix.lower() == ".ttc" else 1):
                try:
                    candidate = ImageFont.truetype(str(path), max(1, round(size * 4)), index=face_index)
                except (OSError, ValueError):
                    break
                if candidate.getname()[0].casefold() == item["font"].casefold():
                    font = candidate
                    faces.add(candidate.getname()[0])
                    break
        text = run["text"]
        if font is not None:
            if item.get("vertical"):
                width = max((font.getlength(c) / 4 for c in text), default=0)
            else:
                width = font.getlength(text) / 4
            statuses.append("measured")
        else:
            advance = [size * (1 if unicodedata.east_asian_width(c) in {"F", "W"} else .56) for c in text]
            width = max(advance, default=0) if item.get("vertical") else sum(advance)
            statuses.append("estimated")
        widths.append(width)
    width = max(widths, default=0) if item.get("vertical") else sum(widths)
    if not item.get("vertical"):
        width += max(0, len(item["text"]) - 1) * item.get("tracking", 0)
    return max(0, width), "estimated" if "estimated" in statuses else "measured", ", ".join(sorted(faces)) or None


def _fit_text_legacy(item):
    """Correct measured pixel-rounding errors without redesigning typography.

    A box read from a raster can differ by one pixel across its two edges.
    Only an available matching font can justify a correction beyond that
    uncertainty. A larger font change means the description needs attention,
    rather than authorizing an arbitrary percentage reduction. Font sizes
    written to DrawingML have 0.01pt precision, or 0.02px on this 144dpi page.
    """
    width, status, face = _measured_width(item)
    declared = item["box"][2]
    fitted = deepcopy(item)
    final_width = width
    tolerance = 1.0
    style_unverified = (any(r.get("italic", item.get("italic", False)) for r in item["runs"])
                        if item.get("runs") else item.get("italic", False))
    reason = "within_pixel_uncertainty"
    if width > declared + tolerance:
        reason = "font_unavailable" if status != "measured" else "font_change_exceeds_pixel_budget"
        if style_unverified:
            reason = "font_style_unverified"
        # Tracking is an independent authored spacing, not part of a glyph's
        # scale. Keeping it fixed avoids understating the remaining overflow.
        fixed_spacing = (max(0, len(item["text"]) - 1) * item.get("tracking", 0)
                         if not item.get("vertical") else 0)
        glyph_width = width - fixed_spacing
        if status == "measured" and not style_unverified and glyph_width > 0:
            factor = (declared - fixed_spacing) / glyph_width
            sizes = [item["size"], *[r.get("size", item["size"]) for r in item.get("runs", [])]]
            if 0 < factor < 1 and max(sizes) * (1 - factor) <= 1:
                # Round down to a representable size, then measure the actual
                # result. Font hinting and unscaled spacing make width*factor
                # an insufficient record of what will be emitted.
                candidate = deepcopy(item)
                candidate["size"] = round(math.floor(item["size"] * factor * 50 + 1e-8) / 50, 4)
                actual_factor = candidate["size"] / item["size"]
                for run in candidate.get("runs", []):
                    if "size" in run:
                        run["size"] = round(math.floor(run["size"] * actual_factor * 50 + 1e-8) / 50, 4)
                candidate_sizes = [candidate["size"], *[r.get("size", candidate["size"])
                                                       for r in candidate.get("runs", [])]]
                if all(0 < after <= before and before - after <= 1 + 1e-8
                       for before, after in zip(sizes, candidate_sizes)):
                    checked_width, checked_status, checked_face = _measured_width(candidate)
                    reason = "font_quantization_unresolved"
                    if (checked_status == "measured" and checked_face == face
                            and checked_width <= declared + tolerance):
                        fitted, final_width = candidate, checked_width
                        reason = "measured_pixel_correction"
    factor = fitted["size"] / item["size"]
    measured = {"status": status, "font": item["font"], "face": face, "width_px": round(width, 4),
                "declared_width_px": declared, "fit_factor": round(factor, 8),
                "fitted_width_px": round(final_width, 4),
                "remaining_overflow_px": round(max(0, final_width - declared), 4),
                "fit_reason": reason, "width_tolerance_px": tolerance,
                "font_correction_budget_px": 1.0,
                "confidence": "unverified_italic_style" if style_unverified else status}
    record = {"authored_size_px": item["size"], "effective_size_px": fitted["size"],
              "measurement": measured}
    if "runs" in item:
        record["run_sizes_px"] = [{"authored": r.get("size", item["size"]),
                                  "effective": fr.get("size", fitted["size"])}
                                 for r, fr in zip(item["runs"], fitted["runs"])]
    warnings = []
    if status == "estimated":
        warnings.append(f"{item['id']}: {item['font']} FreeType measurement unavailable; width is estimated")
    if style_unverified and final_width > declared + tolerance:
        warnings.append(f"{item['id']}: italic font face not measured; authored size retained")
    if final_width > declared + tolerance:
        warnings.append(f"{item['id']}: width overflow remains; authored typography retained ({reason})")
    return fitted, record, warnings


def _contain_width(item):
    """Use a matching face or an explicitly labelled script-aware estimate."""
    width,status,face = _measured_width(item)
    if status == 'measured':
        return width,status,face,False
    # Keep the existing wide-letter/symbol allowances, but ordinary ASCII
    # numbering does not occupy a full CJK em. No unavailable-font estimate is
    # a guaranteed bound, and no substitute face is labelled as the author font.
    total = 0.0
    for run in item.get('runs') or [{'text':item['text']}]:
        size = run.get('size',item['size'])
        for char in run['text']:
            if unicodedata.category(char) in {'Mn','Me','Cf'}:
                continue
            advance = (_ASCII_DIGIT_ADVANCE_EM if '0' <= char <= '9'
                       else (2 if ord(char) >= 0x1F000 else 1))
            total += size * advance
    total += max(0,len(item['text'])-1) * item.get('tracking',0)
    return max(width,total,0),'estimated',face,True


def _scaled_text(item,factor):
    candidate = deepcopy(item)
    candidate['size'] = round(math.floor(item['size']*factor*50+1e-8)/50,4)
    actual_factor = candidate['size']/item['size']
    for run in candidate.get('runs',[]):
        if 'size' in run:
            run['size'] = round(math.floor(run['size']*actual_factor*50+1e-8)/50,4)
    return candidate


def _fit_text(item, *, textfit_policy=None):
    if textfit_policy is None:
        return _fit_text_legacy(item)
    if textfit_policy != TEXT_FIT_POLICY:
        raise ValueError('Unknown native text fit policy')
    width,status,face,conservative = _contain_width(item)
    authored_box = list(item['box'])
    fitted = deepcopy(item)
    effective_width = authored_box[2]
    final_width = width
    reason = 'within_authored_bounds'
    warnings = []
    rotated = not math.isclose(item.get('rotation',0)%360,0,abs_tol=1e-12)
    italic = (any(run.get('italic',item.get('italic',False)) for run in item.get('runs',[]))
              or item.get('italic',False))
    supported = not item.get('vertical') and not rotated and not italic

    def containment_error(message):
        return ValueError(f"{message} [text_id={item['id']}; status={status}; "
                          f"font={item['font']}; width_px={width:.4f}; "
                          f"declared_width_px={authored_box[2]}; "
                          f"effective_width_px={effective_width}]")

    if supported:
        left = authored_box[0]
        if left < 0 or left >= 1920:
            raise containment_error('Text containment needs an anchor inside the working page')
        effective_width = min(effective_width,1920-left)
        if not math.isfinite(effective_width) or effective_width <= 0:
            raise containment_error('Text containment has no positive page width')
        fitted['box'][2] = effective_width
        if effective_width != authored_box[2]:
            reason = 'page_width_clamped'
        if width > effective_width:
            spacing = max(0,len(item['text'])-1)*item.get('tracking',0)
            glyph_width = width-spacing
            if glyph_width <= 0 or effective_width <= spacing:
                raise containment_error('Text containment cannot fit the authored character spacing')
            factor = (effective_width-spacing)/glyph_width
            # This is a legibility guard, not permission to redesign a page.
            # Larger changes are surfaced instead of emitting arbitrarily tiny type.
            for unused in range(12):
                candidate = _scaled_text(item,factor)
                sizes = [(candidate['size'],item['size'])]
                sizes.extend((run.get('size',candidate['size']),original.get('size',item['size']))
                             for run,original in zip(candidate.get('runs',[]),item.get('runs',[])))
                if any(after <= 0 or after/before < _MIN_TEXT_SCALE-1e-10 for after,before in sizes):
                    raise containment_error('Text containment exceeds the 25 percent font reduction budget')
                candidate['box'][2] = effective_width
                checked_width,checked_status,checked_face,checked_conservative = _contain_width(candidate)
                if checked_width <= effective_width + 1e-9:
                    fitted,final_width = candidate,checked_width
                    status,face,conservative = checked_status,checked_face,checked_conservative
                    reason = 'measured_box_containment' if status=='measured' else 'estimated_box_containment'
                    break
                ratio = (effective_width-spacing)/max(checked_width-spacing,1e-9)
                next_factor = (candidate['size']/item['size'])*ratio
                factor = min(next_factor,(candidate['size']-.02)/item['size'])
            else:
                raise containment_error('Text containment could not resolve font quantization')
    else:
        # Existing authored geometry is deliberate for rotation/vertical/italic.
        reason = 'unsupported_orientation_or_style_authored_retained'
    factor = fitted['size']/item['size']
    measurement = {'status':status,'font':item['font'],'face':face,'width_px':round(width,4),
                   'declared_width_px':authored_box[2],'effective_width_px':effective_width,
                   'fitted_width_px':round(final_width,4),'remaining_overflow_px':round(max(0,final_width-effective_width),4),
                   'fit_factor':round(factor,8),'fit_reason':reason,'width_tolerance_px':0,
                   'max_font_reduction_fraction':1-_MIN_TEXT_SCALE,
                   'conservative_fallback':conservative,
                   'confidence':'unverified_italic_style' if italic else status}
    if status == 'estimated':
        measurement.update({'estimate_is_strict_upper_bound':False,
                            'estimate_basis':'script_aware_nominal_advances',
                            'ascii_digit_advance_em':_ASCII_DIGIT_ADVANCE_EM})
    record = {'textfit_policy':TEXT_FIT_POLICY,'authored_box':authored_box,
              'authored_size_px':item['size'],'effective_size_px':fitted['size'],'measurement':measurement}
    if 'runs' in item:
        record['run_sizes_px'] = [{'authored':old.get('size',item['size']),
                                 'effective':new.get('size',fitted['size'])}
                                for old,new in zip(item['runs'],fitted['runs'])]
    if status == 'estimated':
        warnings.append(f"{item['id']}: {item['font']} matching face unavailable; width is estimated, not a strict font bound")
    if fitted['size'] != item['size']:
        warnings.append(f"{item['id']}: native text contained by bounded font reduction ({reason})")
    if final_width > effective_width:
        warnings.append(f"{item['id']}: width overflow remains; authored orientation/style retained")
    return fitted,record,warnings


def _asset_target(value, ident):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"asset {ident} target must be [x, y, width, height]")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(f"asset {ident} target must contain finite numbers")
    if value[2] <= 0 or value[3] <= 0:
        raise ValueError(f"asset {ident} target width and height must be positive")
    return list(value)


def _validate_package(path):
    required = ["[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml", "ppt/slides/slide1.xml"]
    with ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"PPTX validation failed: corrupt member {bad}")
        missing = set(required) - set(archive.namelist())
        if missing:
            raise RuntimeError("PPTX validation failed: required parts are missing")
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
        ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
              "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        if root.find("p:cSld/p:bg/p:bgPr/a:blipFill/a:blip", ns) is None:
            raise RuntimeError("PPTX validation failed: slide background picture fill is missing")
    return {"zip_valid": True, "xml_well_formed": True, "required_parts": required,
            "background_picture_fill": True, "office_render_checked": False}


def build_presentation(layout: dict, background: Path, assets: list[dict], output: Path,
                       *, textfit_policy=None) -> dict:
    """Build and atomically publish a new package, refusing existing outputs.

    Each matched art asset becomes exactly one movable picture stretched to its
    four-corner target. Missing art is recorded and omitted, with no substitute.
    """
    if textfit_policy not in (None,TEXT_FIT_POLICY):
        raise ValueError('Unknown native text fit policy')
    scene = normalize_layout(layout)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for PPTX assembly; install/configure it explicitly")
    background = Path(background).resolve()
    if not background.is_file():
        raise FileNotFoundError(f"background image does not exist: {background}")
    with Image.open(background) as image:
        image.verify()
    if not isinstance(assets, list):
        raise ValueError("assets must be a list of asset objects")
    asset_map = {}
    art_ids = {item["id"] for item in scene["items"] if item["kind"] == "art"}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
            raise ValueError("each asset must have an id")
        ident = asset["id"]
        if ident in asset_map:
            raise ValueError(f"duplicate asset id: {ident}")
        if ident not in art_ids:
            raise ValueError(f"asset id does not match an art item: {ident}")
        asset_map[ident] = asset
    objects = []
    warnings = []
    skipped = []
    prepared = []
    equations = {}
    default_z = {"shape": 0, "line": 5, "table": 5, "art": 10, "text": 20, "formula": 20}
    for original in scene["items"]:
        item = deepcopy(original)
        kind, ident = item["kind"], item["id"]
        record = {"id": ident, "kind": kind}
        if kind == "art":
            asset = asset_map.get(ident)
            if asset is None or not asset.get("path") or not Path(asset["path"]).is_file():
                reason = "missing_asset" if asset is None else "missing_asset_file"
                skipped.append({"id": ident, "reason": reason})
                warnings.append(f"{ident}: foreground art skipped ({reason}); no substitute was created")
                continue
            path = Path(asset["path"]).resolve()
            with Image.open(path) as image:
                image.verify()
            item["path"] = str(path)
            item["target"] = _asset_target(asset.get("target", item["target"]), ident)
            if "z" in asset:
                z = asset["z"]
                if isinstance(z, bool) or not isinstance(z, (int, float)) or not math.isfinite(z):
                    raise ValueError(f"asset {ident}.z must be finite")
                item["z"] = z
            record.update(editable_type="foreground_picture", target=item["target"])
        elif kind == "text":
            item, typography, notes = _fit_text(item,textfit_policy=textfit_policy)
            record.update(typography)
            warnings.extend(notes)
            record["editable_type"] = "native_text"
        elif kind == "formula":
            # Literal LaTeX command spelling is not the geometry of native
            # Office Math. Keep the declared equation size; separately report
            # the compatibility string's measurable width without fitting it.
            literal_width, literal_status, face = _measured_width(item)
            record.update(editable_type="native_formula_text", authored_size_px=item["size"],
                          effective_size_px=item["size"], measurement={
                              "status": "native_math_unmeasured", "font": "Cambria Math",
                              "declared_width_px": item["box"][2], "fit_factor": 1.0,
                              "literal_fallback": {"status": literal_status, "font": "Cambria Math",
                                  "face": face, "width_px": round(literal_width, 4),
                                  "declared_width_px": item["box"][2],
                                  "remaining_overflow_px": round(max(0, literal_width - item["box"][2]), 4)}})
            equations[ident] = original["text"]
        else:
            record["editable_type"] = {"shape": "native_shape", "line": "native_line",
                                       "table": "native_table_composite"}[kind]
            if kind == "table":
                item["_cell_lines"] = []
                record["cell_lines"] = []
                x0, y, _, _ = item["box"]
                for row, cells in enumerate(item["rows"]):
                    x = x0
                    for col, cell in enumerate(cells):
                        lines = cell.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                        line_height = item["row_heights"][row] / len(lines)
                        for line_index, text in enumerate(lines):
                            if not text:
                                continue
                            child = {"id": f"{ident}::text.{row}.{col}.{line_index}", "text": text,
                                     "box": [x, y + line_index * line_height, item["column_widths"][col], line_height],
                                     "font": "Microsoft YaHei", "size": item["size"], "color": item["color"]}
                            fitted, typography, notes = _fit_text(child,textfit_policy=textfit_policy)
                            item["_cell_lines"].append(fitted)
                            record["cell_lines"].append({"row": row, "column": col, "line": line_index, **typography})
                            warnings.extend(notes)
                        x += item["column_widths"][col]
                    y += item["row_heights"][row]
        item["_layer"] = item.get("z", default_z[kind])
        record["z"] = item["_layer"]
        if "box" in item:
            record["box"] = item["box"]
        # Default containers at the same layer are ordered by descending area.
        # Explicit z values and authored order take precedence otherwise.
        item["_area_key"] = -item["box"][2] * item["box"][3] if kind == "shape" and "z" not in item else 0
        prepared.append(item)
        objects.append(record)
    prepared.sort(key=lambda item: (item["_layer"], item["_area_key"], item["order"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".newpath-build-", dir=output.parent) as temporary:
        stage = Path(temporary)
        manifest = stage / "render.json"
        temporary_pptx = stage / "presentation.pptx"
        manifest.write_text(json.dumps({"background": str(background), "items": prepared,
                                        "output": str(temporary_pptx)}, ensure_ascii=False), encoding="utf-8")
        script = Path(__file__).with_name("render.cjs")
        try:
            result = subprocess.run([node, str(script), str(manifest)], capture_output=True,
                                    text=True, encoding="utf-8", timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("PPTX assembly exceeded its local 120 second limit") from exc
        if result.returncode:
            raise RuntimeError(f"PPTX assembly failed: {(result.stderr or result.stdout)[-1600:].strip()}")
        try:
            node_report = json.loads(result.stdout.strip())
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("PPTX assembly returned an invalid report") from exc
        count_fields = {"text_frames", "shapes", "lines", "table_groups", "table_cells", "foreground_images"}
        native_counts = node_report.get("native_counts") if isinstance(node_report, dict) else None
        if (not isinstance(native_counts, dict) or not count_fields.issubset(native_counts)
                or any(type(native_counts[name]) is not int or native_counts[name] < 0
                       for name in count_fields)):
            raise RuntimeError("PPTX assembly returned an invalid native_counts report")
        equation_report = {"embedded": [], "fallback": [], "warnings": []}
        if equations:
            from .equations import attach_equations
            equation_report = attach_equations(temporary_pptx, equations)
            warnings.extend(equation_report["warnings"])
            embedded = set(equation_report["embedded"])
            for record in objects:
                if record["id"] in embedded:
                    record["editable_type"] = "office_equation_with_text_fallback"
        try:
            validation = _validate_package(temporary_pptx)
        except (BadZipFile, ET.ParseError, KeyError, OSError) as exc:
            raise RuntimeError("PPTX validation failed: invalid ZIP or XML package") from exc
        digest = hashlib.sha256(temporary_pptx.read_bytes()).hexdigest()
        # Windows rename is an atomic no-replace operation. The hard-link path
        # keeps the same no-replace guarantee on POSIX where rename overwrites.
        if os.name == "nt":
            os.rename(temporary_pptx, output)
        else:
            os.link(temporary_pptx, output)
            temporary_pptx.unlink()
    return {"pptx": str(output), "sha256": digest, "format": scene["format"],
            "slide_size_px": [1920, 1080], "objects": objects,
            "native_counts": native_counts, "warnings": warnings,
            "skipped": skipped, "equations": equation_report, "validation": validation}
