"""Choose regular image-board cells from source and destination pixel demand.

Equal cells and real empty gutters are preferred over dense packing: an image
service must preserve the arrangement. This bounded deterministic candidate
search does not prove optimal bin packing. Oversized members remain explicit.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import cached_property
import math

_CANVASES = ((1024, 1024), (1536, 1024))
_MARGIN, _GAP = 24, 24
_POLICY = "equal-cells-pixel-budget/1"
_SPLIT_POLICY = "equal-cells-beneficial-lettering-split/1"
_MIN_SPLIT_GAIN = 1.25
_MAX_SPLIT_CANDIDATES = 12


@dataclass(frozen=True)
class _Request:
    art: dict
    crop: tuple[int, int, int, int]
    required_scale: float

    @property
    def identity(self):
        return self.art["id"]

    @property
    def width(self):
        return self.crop[2] - self.crop[0]

    @property
    def height(self):
        return self.crop[3] - self.crop[1]

    @property
    def wanted(self):
        return (math.ceil(self.width * self.required_scale),
                math.ceil(self.height * self.required_scale))

    @cached_property
    def hardware_content(self):
        # The same margins and safety border apply even to a dedicated board.
        # Sharing must never provide fewer pixels than this unavoidable limit.
        return max((_sampling(self, _cell(size,1,1)) for size in _CANVASES),
                   key=lambda value:value[0])[1]

    @cached_property
    def planning_wanted(self):
        if all(got>=need for got,need in zip(self.hardware_content,self.wanted)):
            return self.wanted
        return self.hardware_content


def _geometry(value, name):
    try:
        if len(value) != 4:
            raise ValueError
        values = tuple(float(number) for number in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must contain four finite coordinates") from error
    x, y, width, height = values
    if (not all(math.isfinite(number) for number in values) or width <= 0 or height <= 0
            or not math.isfinite(x + width) or not math.isfinite(y + height)):
        raise ValueError(f"{name} must have finite coordinates and positive dimensions")
    return values


def _requests(arts, source_size):
    result, identities = [], set()
    for art in arts:
        identity = art.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in identities:
            raise ValueError("Artwork IDs must be unique nonempty strings")
        identities.add(identity)
        x, y, width, height = _geometry(art.get("box"), f"Source box for {identity}")
        if x >= source_size[0] or y >= source_size[1] or x + width <= 0 or y + height <= 0:
            raise ValueError(f"Source box is outside the current source: {identity}")
        context = math.ceil(math.sqrt(min(width, height)))
        crop = (max(0, math.floor(x - context)), max(0, math.floor(y - context)),
                min(source_size[0], math.ceil(x + width + context)),
                min(source_size[1], math.ceil(y + height + context)))
        target = _geometry(art.get("target", art["box"]), f"Target box for {identity}")
        required = max(1.0, target[2] / width, target[3] / height, 96.0 / max(width, height))
        if art.get("lettering"):
            required = max(required, 48.0 / min(width, height))
        if not all(math.isfinite(v) for v in (required * (crop[2]-crop[0]),
                                               required * (crop[3]-crop[1]))):
            raise ValueError(f"Pixel requirement cannot be represented for {identity}")
        result.append(_Request(art, crop, required))
    return sorted(result, key=lambda request: request.identity)


def _cell(size, rows, columns):
    width = (size[0] - 2 * _MARGIN - (columns-1) * _GAP) // columns
    height = (size[1] - 2 * _MARGIN - (rows-1) * _GAP) // rows
    ix, iy = math.ceil(width * .1), math.ceil(height * .1)
    if width <= 2 * ix or height <= 2 * iy:
        return None
    return width, height, ix, iy


def _sampling(item, cell):
    width, height, ix, iy = cell
    scale = min((width-2*ix) / item.width, (height-2*iy) / item.height)
    content = (int(item.width * scale), int(item.height * scale))
    return scale, content, all(got >= need for got, need in zip(content, item.wanted))


def _shareable(item, cell):
    _,content,_ = _sampling(item,cell)
    return all(got>=need for got,need in zip(content,item.planning_wanted))


def _templates(count):
    result = []
    for size in _CANVASES:
        for rows in range(1, count + 1):
            for columns in range(1, math.ceil(count / rows) + 1):
                cell = _cell(size, rows, columns)
                if cell is not None:
                    result.append((size, rows, columns, cell))
    return result


def _priority(item, mode):
    width, height = item.wanted
    if mode == "width":
        return -width, -height, item.identity
    if mode == "height":
        return -height, -width, item.identity
    return -width*height, -max(width,height), item.identity


def _plan(items, templates, mode):
    remaining = sorted(items, key=lambda item: _priority(item, mode))
    boards = []
    while remaining:
        anchor = remaining[0]
        options = []
        for size, rows, columns, cell in templates:
            if not _shareable(anchor, cell):
                continue
            eligible = [item for item in remaining if _shareable(item, cell)]
            group = eligible[:rows*columns]
            key = (-len(group), size[0]*size[1], rows*columns-len(group),
                   -min(_sampling(item,cell)[0] for item in group), rows, columns,
                   tuple(item.identity for item in group))
            options.append((key, (size, rows, columns, cell, group)))
        if options:
            _, chosen = min(options, key=lambda option: option[0])
        else:
            size = max(_CANVASES, key=lambda candidate: _sampling(anchor, _cell(candidate,1,1))[0])
            chosen = (size,1,1,_cell(size,1,1),[anchor])
        boards.append(chosen)
        picked = {item.identity for item in chosen[-1]}
        remaining = [item for item in remaining if item.identity not in picked]
    return boards


def _public(board):
    size, rows, columns, cell, items = board
    width, height, ix, iy = cell
    placements = []
    for index, item in enumerate(sorted(items, key=lambda entry: entry.identity)):
        row, column = divmod(index, columns)
        slot = [_MARGIN+column*(width+_GAP), _MARGIN+row*(height+_GAP), width,height]
        scale, content, enough = _sampling(item, cell)
        placements.append({"art": item.art, "crop": list(item.crop), "slot": slot,
                           "inset_px": min(ix,iy), "inset_xy": [ix,iy],
                           "pixel_budget": {"required_scale": item.required_scale,
                                            "planned_scale": scale,
                                            "required_content_px": list(item.wanted),
                                            "planning_content_px": list(item.planning_wanted),
                                            "hardware_max_content_px": list(item.hardware_content),
                                            "hardware_limited": item.planning_wanted != item.wanted,
                                            "planned_content_px": list(content),
                                            "meets_requirement": enough}})
    return {"size": list(size), "placements": placements, "policy": _POLICY,
            "grid": {"rows": rows, "columns": columns, "margin": _MARGIN,
                     "gap": _GAP, "cell_size": [width,height], "policy": _POLICY},
            "pixel_budget": {"all_members_meet_requirement": all(
                placement["pixel_budget"]["meets_requirement"] for placement in placements)}}


def _best_plan(items):
    """Keep the existing demand-first planner and its deterministic tie-breaks."""
    templates = _templates(len(items))
    attempts = [_plan(items,templates,mode) for mode in ("area","width","height")]
    return min(attempts, key=lambda boards: (
        len(boards),sum(board[0][0]*board[0][1] for board in boards),
        tuple((board[1],board[2],tuple(sorted(item.identity for item in board[-1]))) for board in boards)))


def _written(item):
    text = item.art.get("lettering")
    return isinstance(text,str) and bool(text.strip())


def _content_map(boards):
    return {item.identity:_sampling(item,board[3])[1]
            for board in boards for item in board[-1]}


def _beneficial_split(boards):
    """Buy at most one extra board for two materially better sampled labels.

    Only dense groups in plans of at most three boards are considered. Search
    at most twelve singleton partitions per group, retaining regular cells and
    the original reachable pixel budgets. Hardware-limited width alone cannot
    justify another call because its dedicated-board sampling does not improve.
    This is a planning benefit, not a promise of image-model visual quality.
    """
    if len(boards) > 3:
        return boards, None
    candidates = []
    for index,board in enumerate(boards):
        group = board[-1]
        written = [item for item in group if _written(item)]
        dense = ((len(group)>=4 and len(written)>=2)
                 or (len(group)>=3 and len(written)==len(group)))
        if not dense:
            continue
        before = _content_map([board])
        ordered = sorted(group,key=lambda item:item.identity)
        for isolated in ordered[:_MAX_SPLIT_CANDIDATES]:
            remaining = [item for item in group if item.identity != isolated.identity]
            replacement = _best_plan([isolated]) + _best_plan(remaining)
            if len(replacement) != 2:
                continue
            after = _content_map(replacement)
            if any(new<old for identity in before
                   for old,new in zip(before[identity],after[identity])):
                continue
            gains = {identity:min(new/old for old,new in zip(before[identity],after[identity]))
                     for identity in before}
            improved = sorted(item.identity for item in written
                              if gains[item.identity]>=_MIN_SPLIT_GAIN)
            if len(improved) < 2:
                continue
            audit = {"policy":_SPLIT_POLICY,
                     "reason":"dense_written_group_with_measurable_sampling_gain",
                     "base_board_count":len(boards),"extra_board_count":1,
                     "minimum_linear_gain":_MIN_SPLIT_GAIN,
                     "original_group_ids":sorted(before),
                     "improved_lettering_ids":improved,
                     "sampling":[{"id":identity,"before_content_px":list(before[identity]),
                                  "after_content_px":list(after[identity]),
                                  "linear_gain":gains[identity]}
                                 for identity in sorted(before)]}
            pixels = sum(b[0][0]*b[0][1] for b in replacement)
            key = (-len(improved),pixels,-sum(min(gains[i],4.0) for i in improved),
                   tuple(sorted(before)),isolated.identity)
            revised = boards[:index]+replacement+boards[index+1:]
            candidates.append((key,revised,audit))
    if not candidates:
        return boards, None
    _,chosen,audit = min(candidates,key=lambda candidate:candidate[0])
    return chosen,audit


def plan_boards(arts: list[dict], source_size=(1920,1080)) -> list[dict]:
    """Plan stable source crops and equal cells with honest integer budgets.

    Three deterministic source-demand orderings are evaluated, preferring
    fewer boards and then fewer canvas pixels. Hardware-limited members may
    share when each retains its full dedicated-board sampling limit; original
    unmet source budgets stay explicit. Reachable budgets are never reduced.
    A dense written group may use one more board when at least two written
    subjects gain 25 percent in both sampling dimensions and no member loses
    pixels. A fourth board is optional, not a total-board cap or concurrency.
    ``inset_xy`` is actual per-axis clearance; ``inset_px`` is legacy metadata.
    """
    try:
        if len(source_size) != 2:
            raise ValueError
        size = tuple(int(value) for value in source_size)
        if any(value <= 0 or value != original for value,original in zip(size,source_size)):
            raise ValueError
    except (TypeError,ValueError,OverflowError) as error:
        raise ValueError("Source size must contain two positive pixel dimensions") from error
    items = _requests(arts,size)
    if not items:
        return []
    chosen,audit = _beneficial_split(_best_plan(items))
    result = []
    for board in sorted(chosen,key=lambda value:min(item.identity for item in value[-1])):
        public = _public(board)
        if audit and any(item.identity in audit['original_group_ids'] for item in board[-1]):
            public['policy'] = _SPLIT_POLICY
            public['grid']['policy'] = _SPLIT_POLICY
            public['pixel_budget']['adaptive_split'] = audit
        result.append(public)
    return result
