"""Image-derived matte removal and conservative reference-guided selection.

Public functions accept H x W x 4 uint8 arrays, never mutate their arguments,
and perform no file IO. Existing transparent pixels retain their bytes. The
default clears only observed solid matte pixels. Optional edge unmixing is an
experiment: a colour mixture is not proof that a material is transparent.
Identical foreground/background colours remain unrecoverable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image


_COLORS = {"magenta": (255, 0, 255), "green": (0, 255, 0),
           "cyan": (0, 255, 255), "yellow": (255, 255, 0)}
_AUTO_BACKGROUND = object()


@dataclass(frozen=True)
class MatteBackground:
    """A colour observation made on the complete returned opaque canvas."""
    key: str
    rgb: tuple[float, float, float]
    radius: float


def _rgba(pixels: np.ndarray) -> np.ndarray:
    array = np.asarray(pixels)
    if (array.ndim != 3 or array.shape[2] != 4 or not array.shape[0]
            or not array.shape[1] or array.dtype != np.uint8):
        raise ValueError("Expected a nonempty H x W x 4 uint8 RGBA array")
    return array


def _color(key: str) -> np.ndarray:
    if key not in _COLORS:
        raise ValueError(f"Unknown matte color: {key}")
    return np.asarray(_COLORS[key], dtype=np.float32)


def _hue_distance(rgb: np.ndarray, key: str) -> tuple[np.ndarray, np.ndarray]:
    """Distance on the RGB plane perpendicular to the neutral gray axis."""
    color = _color(key)
    direction = color - color.mean()
    direction /= np.linalg.norm(direction)
    values = rgb.astype(np.float32)
    centered = values - values.mean(axis=-1, keepdims=True)
    length = np.linalg.norm(centered, axis=-1)
    projection = np.sum(centered * direction, axis=-1)
    cosine = np.divide(projection, length, out=np.zeros_like(length), where=length > 0)
    chroma = values.max(axis=-1) - values.min(axis=-1)
    return 1 - cosine, chroma


def collision_mask(pixels: np.ndarray, key: str) -> np.ndarray:
    """Flag visible saturated colors within a 22.5-degree hue neighborhood.

    This intentionally covers dark subject colors too. It estimates *collision
    risk*, not removable background; using it directly as an erasure mask would
    incorrectly delete subject-colored pixels.
    """
    pixels = _rgba(pixels)
    distance, chroma = _hue_distance(pixels[..., :3], key)
    return ((pixels[..., 3] > 0) & (chroma >= 32)
            & (distance <= 1 - math.cos(math.pi / 8)))


def choose_key(crops: list[np.ndarray]) -> str:
    """Keep magenta unless a visible, saturated subject patch conflicts.

    Weak neutral edge tints are not subject colours. Prefer the first safe key
    in a stable order; minimize conflict only when every key has real content.
    """
    risks = dict.fromkeys(_COLORS, 0.0)
    exact_conflicts = dict.fromkeys(_COLORS, False)
    visible = 0.0
    for crop in crops:
        pixels = _rgba(crop)
        weight = pixels[..., 3].astype(np.float32) / 255
        visible += float(np.sum(weight, dtype=np.float64))
        for key in _COLORS:
            # A tiny coloured detail can be as important as a large one. Near
            # the actual removal colour there is no whole-image area exemption.
            close_to_key = np.max(np.abs(pixels[..., :3].astype(np.float32) - _color(key)), axis=-1) <= 48
            exact_conflicts[key] |= bool(np.any(close_to_key & (pixels[..., 3] > 0)))
            distance, chroma = _hue_distance(pixels[..., :3], key)
            strong = ((distance <= 1 - math.cos(math.pi / 12)) & (chroma >= 64)
                      & (chroma >= .45 * pixels[..., :3].max(axis=-1)))
            risks[key] += float(np.sum(weight * strong, dtype=np.float64))
    significant = max(16, visible * .003)
    for key, risk in risks.items():
        if not exact_conflicts[key] and risk < significant:
            return key
    return min(risks, key=risks.get)


def _border(pixels: np.ndarray) -> np.ndarray:
    return np.concatenate((pixels[0], pixels[-1], pixels[1:-1, 0], pixels[1:-1, -1]))


def estimate_background(pixels: np.ndarray, key: str) -> MatteBackground | None:
    """Observe one trusted matte identity before any local crops are made.

    A transparent or subject-dominated perimeter supplies no such evidence.
    The returned immutable identity may be reused on crops of this same image.
    """
    pixels = _rgba(pixels)
    _color(key)
    border = _border(pixels)
    opaque = border[:, 3] == 255
    # A transparent perimeter is evidence that alpha already encodes the cutout.
    # Retain opaque key-colored subject pixels in that case.
    if np.count_nonzero(opaque) <= len(border) // 2:
        return None
    hue = collision_mask(border[None, ...], key)[0] & opaque
    if np.count_nonzero(hue) <= np.count_nonzero(opaque) // 2:
        return None
    samples = border[hue, :3].astype(np.float32)
    center = np.median(samples, axis=0)
    deviation = np.linalg.norm(samples - center, axis=1)
    # Returned solid backgrounds may contain compression/color-management noise.
    # Adapt only within a bounded local RGB neighborhood of the observed color.
    radius = float(np.clip(8 + 2 * np.quantile(deviation, 0.95), 12, 48))
    return MatteBackground(key, tuple(float(v) for v in center), radius)


def _background(pixels, key, background):
    _color(key)
    if background is _AUTO_BACKGROUND:
        return estimate_background(pixels, key)
    if background is None:
        return None
    if not isinstance(background, MatteBackground) or background.key != key:
        raise ValueError("The matte identity must belong to the same selected key")
    if (len(background.rgb) != 3 or not all(math.isfinite(v) and 0 <= v <= 255 for v in background.rgb)
            or not math.isfinite(background.radius) or not 0 < background.radius <= 255):
        raise ValueError("Invalid observed matte colour or radius")
    return background


def _matte_candidates(pixels: np.ndarray, key: str, *, background=_AUTO_BACKGROUND) -> np.ndarray:
    model = _background(pixels, key, background)
    if model is None:
        return np.zeros(pixels.shape[:2], dtype=bool)
    center = np.asarray(model.rgb, dtype=np.float32)
    rgb = pixels[..., :3].astype(np.float32)
    nearby = np.sum((rgb - center) ** 2, axis=-1) <= model.radius * model.radius
    return nearby & collision_mask(pixels, key) & (pixels[..., 3] == 255)


def erase_matte(pixels: np.ndarray, key: str, *, background=_AUTO_BACKGROUND,
                experimental_unmix: bool = False) -> np.ndarray:
    """Clear the observed matte while preserving non-matte material by default.

    Pass a whole-canvas MatteBackground to avoid inferring from a tight crop.
    Explicit None means no trusted matte: preserve all bytes, without retrying
    inference locally. Existing alpha 0..254 and RGB remain exactly unchanged.
    The explicit experimental_unmix option additionally interprets some opaque
    edge colours as mixtures. This is ambiguous: real opaque paint can have the
    identical colour. It is not a validated repair or a default production step.
    """
    pixels = _rgba(pixels)
    result = pixels.copy()
    model = _background(pixels, key, background)
    if model is None:
        return result
    removed = _matte_candidates(pixels, key, background=model)
    result[removed] = 0
    if not experimental_unmix or not np.any(removed):
        return result
    distance, chroma = _hue_distance(pixels[..., :3], key)
    key_tinted = (distance <= 1 - math.cos(math.pi / 4)) & (chroma >= 6)
    opaque = pixels[..., 3] == 255
    uncertain = _expand(removed, 4) & opaque & ~removed & key_tinted
    yy, xx = np.nonzero(uncertain)
    if not len(xx):
        return result
    solid = opaque & ~removed & ~key_tinted
    if not np.any(solid):
        return result
    center = np.asarray(model.rgb, dtype=np.float32)
    observed = pixels[yy, xx, :3].astype(np.float32)
    mixed = observed - center
    best = np.full(len(xx), np.inf, dtype=np.float32)
    coverage = np.ones(len(xx), dtype=np.float32)
    colours = observed.copy()
    height, width = pixels.shape[:2]
    # A bounded local search keeps disconnected fine details, without eroding
    # an entire contour or altering same-hue material deep inside the subject.
    offsets = [(dy, dx) for step in range(1, 6)
               for dy, dx in ((-step, 0), (step, 0), (0, -step), (0, step),
                              (-step, -step), (-step, step), (step, -step), (step, step))]
    for dy, dx in offsets:
        sy, sx = yy + dy, xx + dx
        inside = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
        indices = np.flatnonzero(inside)
        indices = indices[solid[sy[indices], sx[indices]]]
        if not len(indices):
            continue
        anchor = pixels[sy[indices], sx[indices], :3].astype(np.float32)
        direction = anchor - center
        denominator = np.sum(direction * direction, axis=1)
        alpha = np.sum(mixed[indices] * direction, axis=1) / np.maximum(denominator, 1)
        residual = np.sum((mixed[indices] - alpha[:, None] * direction) ** 2, axis=1)
        error_limit = np.maximum(3, .035 * np.sqrt(denominator)) ** 2
        score = residual + .05 * (dy * dy + dx * dx)
        accept = ((alpha > .01) & (alpha < .995) & (residual <= error_limit)
                  & (score < best[indices]))
        use = indices[accept]
        if not len(use):
            continue
        best[use] = score[accept]
        coverage[use] = alpha[accept]
        colours[use] = np.clip(center + mixed[use] / alpha[accept, None], 0, 255)
    changed = np.isfinite(best)
    result[yy[changed], xx[changed], :3] = np.rint(colours[changed]).astype(np.uint8)
    result[yy[changed], xx[changed], 3] = np.rint(coverage[changed] * 255).astype(np.uint8)
    return result


def _regions(mask: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    """Eight-connected row intervals; avoid Python work for every image pixel."""
    height, width = mask.shape
    parents = []
    runs = []
    previous = []

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def unite(first, second):
        first, second = find(first), find(second)
        if first != second:
            parents[max(first, second)] = min(first, second)

    for y, row in enumerate(mask):
        changes = np.flatnonzero(np.diff(np.pad(row.astype(np.int8), (1, 1))))
        current = []
        cursor = 0
        for start, stop in changes.reshape(-1, 2):
            start, stop = int(start), int(stop)
            identity = len(parents)
            parents.append(identity)
            current.append((start, stop, identity))
            runs.append((y, start, stop, identity))
            while cursor < len(previous) and previous[cursor][1] < start:
                cursor += 1
            neighbor = cursor
            while neighbor < len(previous) and previous[neighbor][0] <= stop:
                unite(identity, previous[neighbor][2])
                neighbor += 1
        previous = current

    labels = np.zeros(mask.shape, dtype=np.int32)
    regions = {}
    for y, start, stop, identity in runs:
        root = find(identity) + 1
        labels[y, start:stop] = root
        region = regions.setdefault(root, {"id": root, "box": [start, y, stop, y + 1]})
        box = region["box"]
        box[0], box[1] = min(box[0], start), min(box[1], y)
        box[2], box[3] = max(box[2], stop), max(box[3], y + 1)
    return labels, list(regions.values())


def edge_background_mask(pixels: np.ndarray, key: str, *, background=_AUTO_BACKGROUND) -> np.ndarray:
    """For rekeying, identify only the observed matte connected to the edge.

    A closed subject patch with the same color is left untouched. Use this
    function when replacing a reference crop's background with another key.
    """
    pixels = _rgba(pixels)
    candidates = _matte_candidates(pixels, key, background=background)
    labels, regions = _regions(candidates)
    height, width = candidates.shape
    edge_ids = [region["id"] for region in regions
                if region["box"][0] == 0 or region["box"][1] == 0
                or region["box"][2] == width or region["box"][3] == height]
    return np.isin(labels, edge_ids) & candidates


def _expand(mask: np.ndarray, radius: int) -> np.ndarray:
    """Exact square support expansion with two cumulative-sum windows."""
    span = radius * 2 + 1
    padded = np.pad(mask, ((0, 0), (radius, radius)))
    sums = np.pad(np.cumsum(padded, axis=1, dtype=np.int32), ((0, 0), (1, 0)))
    horizontal = sums[:, span:] - sums[:, :-span] > 0
    padded = np.pad(horizontal, ((radius, radius), (0, 0)))
    sums = np.pad(np.cumsum(padded, axis=0, dtype=np.int32), ((1, 0), (0, 0)))
    return sums[span:] - sums[:-span] > 0


def select_subject(pixels: np.ndarray, source_aspect: float,
                   reference: np.ndarray | None = None) -> tuple[np.ndarray, list[str]]:
    """Retain connected subjects plus disconnected reference-supported details.

    Alpha > 0 participates, so a low-alpha glow or one-pixel punctuation mark
    is not excluded by an arbitrary visibility cutoff. A same-slot generated
    reference is resized to returned dimensions; its expanded occupancy gives
    positive evidence for components. If none matches, preserve the input and
    warn rather than silently emptying the slot. Without a reference only tiny
    disconnected boundary debris is removed; interior uncertainty is retained.
    source_aspect is validated for caller compatibility, never used to favor a
    large central component over distant lettering.
    """
    pixels = _rgba(pixels)
    if not math.isfinite(source_aspect) or source_aspect <= 0:
        raise ValueError("source_aspect must be positive and finite")
    result = pixels.copy()
    foreground = pixels[..., 3] > 0
    if not np.any(foreground):
        return result, ["empty_subject"]
    labels, regions = _regions(foreground)
    height, width = foreground.shape
    warnings = []
    rejected = []
    used_reference = False
    if reference is not None:
        reference = _rgba(reference)
        alpha_image = Image.fromarray(reference[..., 3])
        resized = np.asarray(alpha_image.resize((width, height), Image.Resampling.NEAREST))
        if np.any(resized):
            support = _expand(resized > 0, max(2, math.ceil(min(width, height) / 28)))
            matched = set(np.unique(labels[support & foreground])) - {0}
            if matched:
                used_reference = True
                sy, sx = np.nonzero(support)
                envelope = (int(sx.min()), int(sy.min()), int(sx.max()) + 1, int(sy.max()) + 1)
                for region in regions:
                    if region["id"] in matched:
                        continue
                    left, top, right, bottom = region["box"]
                    external = (right <= envelope[0] or bottom <= envelope[1]
                                or left >= envelope[2] or top >= envelope[3])
                    if external:
                        rejected.append(region["id"])
                    else:
                        warnings.append("unconfirmed_fragment_retained")
                if rejected:
                    warnings.append("outside_reference_removed")
            else:
                warnings.append("reference_support_mismatch")
        else:
            warnings.append("empty_reference_support")
    if not used_reference:
        interior = [region for region in regions
                    if region["box"][0] > 0 and region["box"][1] > 0
                    and region["box"][2] < width and region["box"][3] < height]
        border_unit = max(1, math.ceil(min(width, height) / 32))
        if interior:
            for region in regions:
                left, top, right, bottom = region["box"]
                at_edge = left == 0 or top == 0 or right == width or bottom == height
                if at_edge and max(right - left, bottom - top) <= border_unit:
                    rejected.append(region["id"])
            if rejected:
                warnings.append("outside_fragment_removed")
        if len(regions) - len(rejected) > 1:
            warnings.append("disconnected_details_retained")
    if rejected:
        result[np.isin(labels, rejected)] = 0
    if (np.any(result[0, :, 3]) or np.any(result[-1, :, 3])
            or np.any(result[:, 0, 3]) or np.any(result[:, -1, 3])):
        warnings.append("subject_touches_boundary")
    return result, list(dict.fromkeys(warnings))
