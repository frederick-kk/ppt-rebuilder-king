"""Deterministic reference boards and strict color-key asset extraction.

This module only processes local images. It neither writes generation prompts
nor invokes a model. The source image is used solely for color collision audits;
all reference-board artwork pixels come from the generated foreground image.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


_CAPACITIES = (1, 2, 3, 4, 6, 9, 16, 24)
_GRIDS = {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2),
          6: (3, 2), 9: (3, 3), 16: (4, 4), 24: (6, 4)}
_KEYS = {"magenta": (255, 0, 255), "green": (0, 255, 0),
         "cyan": (0, 255, 255), "yellow": (255, 255, 0)}
_MARGIN = 16
_GAP = 16
_INSET = 0.08


def _read_png(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"Expected a PNG image: {path}")
            if image.width <= 0 or image.height <= 0:
                raise ValueError(f"Image has no pixels: {path}")
            image.load()
            return image.convert("RGBA")
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError(f"Cannot read PNG image: {path}") from error


def _key_mask(pixels: np.ndarray, key: str) -> np.ndarray:
    if key not in _KEYS:
        raise ValueError(f"Unsupported key color: {key}")
    red, green, blue = (pixels[..., index].astype(np.int16) for index in range(3))
    if key == "magenta":
        return (red >= 150) & (blue >= 150) & (np.minimum(red, blue) - green >= 18) & (np.abs(red - blue) <= 100)
    if key == "green":
        return (green >= 150) & (green - np.maximum(red, blue) >= 18) & (np.abs(red - blue) <= 100)
    if key == "cyan":
        return (green >= 150) & (blue >= 150) & (np.minimum(green, blue) - red >= 18) & (np.abs(green - blue) <= 100)
    return (red >= 150) & (green >= 150) & (np.minimum(red, green) - blue >= 18) & (np.abs(red - green) <= 100)


def validate_output(path: Path, key: str | None = None) -> dict:
    """Reject unusable whole outputs; low key coverage is a warning only.

    The global key fraction deliberately uses RGB matches over *all* W*H
    pixels, including transparent pixels. Collision audits use a different,
    visible-pixel denominator in ``_choose_key``.
    """
    path = Path(path).resolve()
    image = _read_png(path)
    pixels = np.asarray(image)
    visible = int(np.count_nonzero(pixels[..., 3] > 0))
    if not visible:
        raise ValueError(f"Image is completely transparent: {path}")
    warnings = []
    result = {"path": str(path), "size": list(image.size), "format": "PNG",
              "visible_pixels": visible, "warnings": warnings}
    if key is not None:
        matched = _key_mask(pixels, key)
        # An opaque/partly opaque image with no visible non-key content is
        # unusable even if its invisible pixels carry another RGB value.
        if not np.any((pixels[..., 3] > 0) & ~matched):
            raise ValueError(f"Image contains only the selected key color: {path}")
        fraction = float(np.count_nonzero(matched) / matched.size)
        result.update(key=key, key_pixels=int(np.count_nonzero(matched)), key_fraction=fraction)
        if fraction < 0.02:
            warnings.append("key_coverage_below_2_percent")
    return result


def _capacity_limit(box: list) -> int:
    edge = max(float(box[2]), float(box[3]))
    for threshold, capacity in ((120, 24), (180, 16), (240, 9), (340, 6),
                                (480, 4), (620, 3), (780, 2)):
        if edge <= threshold:
            return capacity
    return 1


def _slots(capacity: int, size: tuple[int, int]) -> list[list[int]]:
    columns, rows = _GRIDS[capacity]
    width, height = size
    available_w = width - 2 * _MARGIN - (columns - 1) * _GAP
    available_h = height - 2 * _MARGIN - (rows - 1) * _GAP
    result = []
    for row in range(rows):
        for column in range(columns):
            left = _MARGIN + column * _GAP + column * available_w // columns
            top = _MARGIN + row * _GAP + row * available_h // rows
            right = _MARGIN + column * _GAP + (column + 1) * available_w // columns
            bottom = _MARGIN + row * _GAP + (row + 1) * available_h // rows
            result.append([left, top, right - left, bottom - top])
    return result


def _size_for(capacity: int, arts: list[dict]) -> tuple[int, int]:
    if capacity in (2, 3, 6, 24):
        return (1536, 1024)
    if capacity != 4:
        return (1024, 1024)
    # Compare the actual contextual pixels that will be contained in each slot.
    # Unpadded source boxes can overstate the gain of a wide reference board.
    crops = [_crop_box(art, (1920, 1080)) for art in arts]
    def score(size):
        return sum(min(slot[2] * (1 - 2 * _INSET) / (crop[2] - crop[0]),
                       slot[3] * (1 - 2 * _INSET) / (crop[3] - crop[1]))
                   for slot, crop in zip(_slots(4, size), crops))
    square, wide = (1024, 1024), (1536, 1024)
    return wide if score(wide) >= 1.08 * score(square) else square


def _crop_box(art: dict, size: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y, width, height = art["box"]
    if width <= 0 or height <= 0 or not all(math.isfinite(float(v)) for v in (x, y, width, height)):
        raise ValueError(f"Invalid source box for {art['id']}")
    rate = 0.06 if art.get("presentation", "cutout") == "rectangle" else 0.10
    padding = max(8, min(36, round(max(width, height) * rate)))
    crop = (max(0, math.floor(x - padding)), max(0, math.floor(y - padding)),
            min(size[0], math.ceil(x + width + padding)), min(size[1], math.ceil(y + height + padding)))
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise ValueError(f"Source box is outside the canvas for {art['id']}")
    return crop


def _choose_key(original_crops: list[np.ndarray]) -> str:
    audits = {}
    for key in _KEYS:
        acceptable = True
        total = 0.0
        for crop in original_crops:
            visible = crop[..., 3] > 0
            count = int(np.count_nonzero(_key_mask(crop, key) & visible))
            visible_count = int(np.count_nonzero(visible))
            fraction = count / visible_count if visible_count else 0.0
            acceptable &= count == 0 or (count <= 8 and fraction <= 0.002)
            total += fraction
        audits[key] = (acceptable, total)
    return next((key for key, (safe, _) in audits.items() if safe),
                min(audits, key=lambda key: audits[key][1]))


def _components(mask: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    """Label eight-connected runs without scipy or per-pixel Python floods."""
    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    parents = [0]
    runs = []
    previous = []
    def find(label):
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label
    for y in range(height):
        padded = np.pad(mask[y], (1, 1), constant_values=False)
        changes = np.flatnonzero(padded[1:] != padded[:-1])
        current = []
        previous_index = 0
        for start, end in zip(changes[::2].tolist(), changes[1::2].tolist()):
            label = len(parents)
            parents.append(label)
            while previous_index < len(previous) and previous[previous_index][1] < start:
                previous_index += 1
            index = previous_index
            while index < len(previous) and previous[index][0] <= end:
                other = find(previous[index][2])
                root = find(label)
                if root != other:
                    parents[max(root, other)] = min(root, other)
                index += 1
            labels[y, start:end] = label
            current.append((start, end, label))
            runs.append((y, start, end, label))
        previous = current
    roots = np.asarray([find(label) for label in range(len(parents))], dtype=np.int32)
    labels = roots[labels]
    groups = {}
    for y, start, end, label in runs:
        root = int(roots[label])
        length = end - start
        if root not in groups:
            groups[root] = dict(label=root, area=0, box=[start, y, end, y + 1],
                                sum_x=0.0, sum_y=0.0, touches_edge=False)
        group = groups[root]
        group["area"] += length
        group["sum_x"] += (start + end - 1) * length / 2
        group["sum_y"] += y * length
        box = group["box"]
        box[:] = [min(box[0], start), min(box[1], y), max(box[2], end), max(box[3], y + 1)]
        group["touches_edge"] |= start == 0 or end == width or y == 0 or y == height - 1
    for group in groups.values():
        group["center"] = (group.pop("sum_x") / group["area"], group.pop("sum_y") / group["area"])
    return labels, [groups[key] for key in sorted(groups)]


def _reference_crop(crop: Image.Image, key: str) -> Image.Image:
    pixels = np.array(crop)
    magenta = _key_mask(pixels, "magenta")
    if key == "magenta":
        pixels[magenta] = 0
    else:
        labels, components = _components(magenta)
        edge_labels = [component["label"] for component in components if component["touches_edge"]]
        replace = np.isin(labels, edge_labels)
        pixels[..., :3][replace] = _KEYS[key]
    return Image.fromarray(pixels)


def _draw_separators(board: Image.Image, capacity: int):
    """Four-pixel guides remain strictly outside all complete slot crops."""
    draw = ImageDraw.Draw(board)
    columns, rows = _GRIDS[capacity]
    slots = _slots(capacity, board.size)
    vertical = [8, board.width - 8]
    horizontal = [8, board.height - 8]
    vertical.extend(slots[column][0] - _GAP // 2 for column in range(1, columns))
    horizontal.extend(slots[row * columns][1] - _GAP // 2 for row in range(1, rows))
    for center in vertical:
        draw.rectangle((center - 2, 0, center + 1, board.height - 1), fill=(32, 32, 32, 255))
    for center in horizontal:
        draw.rectangle((0, center - 2, board.width - 1, center + 1), fill=(32, 32, 32, 255))


def prepare_boards(source: Path, foreground: Path, arts: list[dict], out: Path) -> list[dict]:
    """Pack normalized artwork records into deterministic local reference PNGs."""
    if not arts:
        return []
    original, generated = _read_png(Path(source)), _read_png(Path(foreground))
    if original.size != (1920, 1080) or generated.size != (1920, 1080):
        raise ValueError("Source and foreground images must both be 1920x1080")
    if len({art["id"] for art in arts}) != len(arts):
        raise ValueError("Artwork ids must be unique")
    pending = sorted((dict(art) for art in arts),
                     key=lambda art: (_capacity_limit(art["box"]), art.get("order", 0), str(art["id"])))
    batches = []
    while pending:
        limit = _capacity_limit(pending[0]["box"])
        take = min(limit, len(pending))
        capacity = next(value for value in _CAPACITIES if value >= take)
        members = sorted(pending[:take], key=lambda art: (art.get("order", 0), str(art["id"])))
        del pending[:take]
        batches.append((members, capacity))
    batches.sort(key=lambda batch: min(art.get("order", 0) for art in batch[0]))
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    result = []
    for members, capacity in batches:
        size = _size_for(capacity, members)
        slots = _slots(capacity, size)
        crop_boxes = [_crop_box(art, original.size) for art in members]
        key = _choose_key([np.asarray(original.crop(crop)) for crop in crop_boxes])
        reference_crops = [generated.crop(crop) for crop in crop_boxes]
        metadata = []
        signature_members = []
        for art, slot, crop in zip(members, slots, reference_crops):
            member = {"id": art["id"], "slot": slot, "box": list(art["box"]),
                      "target": list(art.get("target", art["box"])),
                      "description": art.get("description", ""),
                      "presentation": art.get("presentation", "cutout")}
            for optional in ("lettering", "z"):
                if optional in art:
                    member[optional] = art[optional]
            metadata.append(member)
            content = {name: value for name, value in member.items() if name not in ("target", "z")}
            content["pixels_sha256"] = hashlib.sha256(crop.tobytes()).hexdigest()
            signature_members.append(content)
        payload = {"version": 1, "size": size, "capacity": capacity, "key": key, "members": signature_members}
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode("utf-8")).hexdigest()
        board_id = f"board-{signature[:20]}"
        path = out / f"{board_id}.png"
        if not path.exists():
            canvas = Image.new("RGBA", size, (*_KEYS[key], 255))
            _draw_separators(canvas, capacity)
            for crop, slot in zip(reference_crops, slots):
                crop = _reference_crop(crop, key)
                x, y, width, height = slot
                safe_w, safe_h = width * (1 - 2 * _INSET), height * (1 - 2 * _INSET)
                scale = min(safe_w / crop.width, safe_h / crop.height)
                resized = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                                      Image.Resampling.LANCZOS)
                left, top = x + (width - resized.width) // 2, y + (height - resized.height) // 2
                canvas.alpha_composite(resized, (left, top))
            canvas.save(path, format="PNG")
        result.append({"id": board_id, "image": str(path), "size": list(size), "capacity": capacity,
                       "key": key, "members": metadata, "signature": signature})
    return result


def _gap(first: list[int], second: list[int]) -> float:
    return math.hypot(max(0, first[0] - second[2], second[0] - first[2]),
                      max(0, first[1] - second[3], second[1] - first[3]))


def _select_pixels(pixels: np.ndarray, source_aspect: float) -> tuple[np.ndarray, list[str]]:
    height, width = pixels.shape[:2]
    labels, components = _components(pixels[..., 3] >= 16)
    if not components:
        return pixels.copy(), ["low_alpha_only"]
    diagonal = math.hypot(width, height)
    center = ((width - 1) / 2, (height - 1) / 2)
    def score(component):
        x0, y0, x1, y1 = component["box"]
        distance = math.dist(component["center"], center)
        aspect = (x1 - x0) / (y1 - y0)
        return (math.log1p(component["area"]) - 2.2 * distance / diagonal
                - 0.35 * abs(math.log(aspect / source_aspect))
                - 0.8 * int(component["touches_edge"]))
    anchor = max(components, key=score)
    selected = [anchor]
    remaining = [component for component in components if component is not anchor]
    changed = True
    while changed:
        changed = False
        for component in remaining[:]:
            if component["touches_edge"] and component["area"] < 0.25 * anchor["area"]:
                continue
            nearby = min(_gap(component["box"], item["box"]) for item in selected) <= max(8, 0.08 * min(width, height))
            substantial = (component["area"] >= max(8, 0.025 * anchor["area"])
                           and math.dist(component["center"], anchor["center"]) <= 0.30 * diagonal)
            if nearby or substantial:
                selected.append(component)
                remaining.remove(component)
                changed = True
    keep = np.isin(labels, [component["label"] for component in selected])
    result = pixels.copy()
    result[~keep] = 0
    return result, []


def _crosses_slot(selected: np.ndarray, global_pixels: np.ndarray, bounds: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = bounds
    alpha = selected[..., 3] > 0
    global_alpha = global_pixels[..., 3] > 0
    if x0 and np.any(alpha[:, 0] & global_alpha[y0:y1, x0 - 1]):
        return True
    if x1 < global_pixels.shape[1] and np.any(alpha[:, -1] & global_alpha[y0:y1, x1]):
        return True
    if y0 and np.any(alpha[0] & global_alpha[y0 - 1, x0:x1]):
        return True
    return bool(y1 < global_pixels.shape[0] and np.any(alpha[-1] & global_alpha[y1, x0:x1]))


def extract_assets(board: dict, actual_image: Path, destination: Path) -> dict:
    """Slice complete scaled slots, remove one global key, and select components.

    Targets are passed through exactly. No fit/contain correction, feathering,
    color repair, semantic classification, or regeneration is performed.
    """
    validation = validate_output(actual_image, board["key"])
    pixels = np.array(_read_png(Path(actual_image)))
    pixels[_key_mask(pixels, board["key"])] = 0
    actual_h, actual_w = pixels.shape[:2]
    scale_x, scale_y = actual_w / board["size"][0], actual_h / board["size"][1]
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    assets = []
    warnings = [{"board": board["id"], "code": warning} for warning in validation["warnings"]]
    for member in board["members"]:
        def warn(code):
            warnings.append({"board": board["id"], "id": member["id"], "code": code})
        x, y, width, height = member["slot"]
        bounds = (max(0, math.floor(x * scale_x)), max(0, math.floor(y * scale_y)),
                  min(actual_w, math.ceil((x + width) * scale_x)),
                  min(actual_h, math.ceil((y + height) * scale_y)))
        x0, y0, x1, y1 = bounds
        if x1 <= x0 or y1 <= y0:
            warn("unreliable_slot_bounds")
            continue
        slot = pixels[y0:y1, x0:x1].copy()
        if not np.any(slot[..., 3]):
            warn("empty_slot")
            continue
        selected, selection_warnings = _select_pixels(slot, member["box"][2] / member["box"][3])
        for warning in selection_warnings:
            warn(warning)
        alpha = selected[..., 3] > 0
        yy, xx = np.nonzero(alpha)
        if not len(xx):
            warn("empty_selected_slot")
            continue
        left, top, right, bottom = int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1
        slot_h, slot_w = slot.shape[:2]
        if left == 0 or top == 0 or right == slot_w or bottom == slot_h:
            warn("touch_slot_edge")
        if (left < slot_w * _INSET or top < slot_h * _INSET
                or right > slot_w * (1 - _INSET) or bottom > slot_h * (1 - _INSET)):
            warn("safe_area_cross")
        if _crosses_slot(selected, pixels, bounds):
            warn("cross_slot")
        crop = selected[top:bottom, left:right]
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(member["id"])).strip("-")[:60] or "art"
        identity = hashlib.sha256(str(member["id"]).encode("utf-8")).hexdigest()[:10]
        path = destination / f"{safe_id}-{identity}.png"
        Image.fromarray(crop).save(path, format="PNG")
        natural_size = [crop.shape[1], crop.shape[0]]
        target = list(member.get("target", member["box"]))
        asset = {"id": member["id"], "path": str(path), "target": target,
                 "source_size": natural_size, "crop_box": [left, top, right, bottom],
                 "actual_slot": [x0, y0, x1 - x0, y1 - y0], "slot_size": [slot_w, slot_h],
                 "stretch": [target[2] / natural_size[0], target[3] / natural_size[1]]}
        if "z" in member:
            asset["z"] = member["z"]
        assets.append(asset)
    return {"assets": assets, "warnings": warnings}
