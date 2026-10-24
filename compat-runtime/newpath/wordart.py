"""Recreate one primary title as provider-native transparent artwork.

Other artwork retains the existing board workflow. This route never creates
alpha from a colour and never uses an incomplete reference mask to delete ink.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image

MODE = 'standalone-native-wordart/2'
_PAGE_SIZE = (1920, 1080)
_PLACEMENT_PAGE_LIMIT = 4


def _box(value, name):
    try:
        numbers = tuple(float(number) for number in value)
        if len(numbers) != 4 or not all(math.isfinite(number) for number in numbers):
            raise ValueError
        if numbers[2] <= 0 or numbers[3] <= 0 or not math.isfinite(numbers[2]*numbers[3]):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(name + ' requires finite x/y and positive width/height') from error
    return numbers


def _check_placement_budget(target):
    """Bound physical PPT geometry, independently of image quality or alpha.

    A nearly empty provider core can otherwise turn tiny visible ink into a
    picture frame hundreds of pages wide. Refuse such a frame without deleting
    pixels, changing the fit, or requesting a replacement image.
    """
    try:
        x, y, width, height = _box(target, 'Placed wordart picture')
        max_width = _PAGE_SIZE[0] * _PLACEMENT_PAGE_LIMIT
        max_height = _PAGE_SIZE[1] * _PLACEMENT_PAGE_LIMIT
        right, bottom = x+width, y+height
        if (width > max_width or height > max_height
                or not all(math.isfinite(v) for v in (right, bottom))
                or min(x, right) < -max_width or max(x, right) > max_width
                or min(y, bottom) < -max_height or max(y, bottom) > max_height):
            raise ValueError
    except ValueError as error:
        raise ValueError('Standalone wordart placement exceeds the four-page safety budget. '
                         'Preserve the provider PNG; no automatic generation is authorized.') from error


def select_primary_wordart(arts):
    """Return deep copies of the largest written artwork and the remaining art.

    Prefer target area, then source y/x and identity for stable ties. Remaining
    objects retain their original order and stay in the ordinary board workflow.
    """
    copied = copy.deepcopy(list(arts))
    candidates = []
    for index, member in enumerate(copied):
        lettering = member.get('lettering')
        if not isinstance(lettering, str) or not lettering.strip():
            continue
        identity = member.get('id')
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('Written artwork requires a nonempty string identity')
        source = _box(member.get('box'), 'Written artwork source box')
        target = _box(member.get('target', member.get('box')), 'Written artwork target')
        candidates.append(((-target[2]*target[3], source[1], source[0], identity), index))
    if not candidates:
        return None, copied
    _, chosen = min(candidates, key=lambda candidate: candidate[0])
    return copied[chosen], copied[:chosen] + copied[chosen+1:]


def _inspect(path):
    path = Path(path)
    try:
        with Image.open(path) as image:
            if image.format != 'PNG' or min(image.size) < 1:
                raise ValueError('Standalone wordart requires a nonempty PNG')
            image.load()
            if 'A' not in image.getbands() and 'transparency' not in image.info:
                raise ValueError('Standalone wordart PNG has no native transparency')
            pixels = np.asarray(image.convert('RGBA')).copy()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('Cannot decode standalone wordart PNG') from error
    alpha = pixels[..., 3]
    visible = int(np.count_nonzero(alpha))
    if not visible:
        raise ValueError('Standalone wordart PNG is completely transparent')
    transparent = int(np.count_nonzero(alpha == 0))
    if not transparent:
        raise ValueError('Standalone wordart PNG has no fully transparent pixels')
    warnings = []
    edges = {'top': alpha[0], 'bottom': alpha[-1],
             'left': alpha[:, 0], 'right': alpha[:, -1]}
    if any(np.any(edge) for edge in edges.values()):
        # Some native PNGs contain barely visible alpha=1 overspray at a canvas
        # edge despite a complete inset ink core. Preserve every RGBA byte and
        # continue with an explicit warning; this does not certify the subject.
        # A core touching an edge, or no core at all, remains a clipping risk.
        if not np.any(alpha > 1) or any(np.any(edge > 1) for edge in edges.values()):
            raise ValueError('Standalone wordart touches a canvas edge or has an opaque background')
        warnings.append({'reason': 'native_wordart_alpha1_edge_overspray',
                         'edge_max_alpha': 1,
                         'edge_nonzero_pixels': {name: int(np.count_nonzero(edge))
                                                 for name, edge in edges.items()},
                         'all_rgba_pixels_preserved': True,
                         'message': 'Only alpha=1 overspray touches the canvas; the alpha>1 ink core is fully inset. '
                                    'All provider pixels are retained without regeneration; visual quality remains unverified.'})
    height, width = pixels.shape[:2]
    info = {'path': str(path.resolve()), 'format': 'PNG', 'size': [width, height],
            'visible_pixels': visible, 'transparent_pixels': transparent,
            'background_mode': MODE, 'transparent_background': True,
            'warnings': warnings, 'quality': 'not_evaluated'}
    return pixels, info


def validate_wordart_output(path):
    """Validate true PNG alpha and inset ink, not text/style correctness.

    A single subject may have any canvas ratio. Painted inner backgrounds,
    incorrect text and visual fidelity require a separate recorded review.
    """
    return _inspect(path)[1]


def extract_wordart_asset(member, path, destination):
    """Keep every visible provider pixel, including tiny disconnected strokes."""
    pixels, inspection = _inspect(path)
    identity = member.get('id')
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError('Standalone wordart requires a nonempty string identity')
    _box(member.get('box'), 'Written artwork source box')
    target = list(member.get('target', member['box']))
    tx, ty, tw, th = _box(target, 'Written artwork target')
    yy, xx = np.nonzero(pixels[..., 3])
    left, top, right, bottom = int(xx.min()), int(yy.min()), int(xx.max())+1, int(yy.max())+1
    crop = pixels[top:bottom, left:right]
    # This is a tight rectangular crop only. Alpha and all RGB bytes inside
    # that rectangle, including RGB hidden beneath alpha=0, remain unchanged.
    destination = Path(destination).resolve()
    safe = re.sub(r'[^A-Za-z0-9_-]+', '-', identity).strip('-')[:60] or 'art'
    suffix = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:10]
    output = destination/(safe + '-' + suffix + '.png')
    height, width = pixels.shape[:2]
    natural = [crop.shape[1], crop.shape[0]]
    asset = copy.deepcopy(member)
    asset.update(path=str(output), target=target, authored_target=list(target), source_size=natural,
                 crop_box=[left, top, right, bottom], actual_slot=[0, 0, width, height],
                 slot_size=[width, height], stretch=[target[2]/natural[0], target[3]/natural[1]],
                 extraction_policy=MODE, cell_mapping_status='aligned',
                 returned_pixel_budget={'status': 'unknown'})
    warnings = [{**warning, 'id': identity} for warning in inspection['warnings']]
    # Fit the visible ink, rather than the transparent provider canvas. Keeping
    # one scale on both axes preserves brush-letter proportions even when an
    # almost invisible alpha=1 halo is many times taller than the ink. Every
    # pixel inside the full nonzero-alpha crop is retained, including that halo.
    core_y, core_x = np.nonzero(pixels[..., 3] > 1)
    if len(core_x):
        ink = [int(core_x.min()), int(core_y.min()),
               int(core_x.max())+1, int(core_y.max())+1]
        basis = 'alpha_greater_than_one'
    else:
        # A technically valid PNG can contain only alpha=1. Do not divide by an
        # absent core or delete it: fit its complete preserved footprint instead.
        ink = [left, top, right, bottom]
        basis = 'all_nonzero_alpha_fallback'
        warnings.append({'id': identity, 'reason': 'native_wordart_alpha1_only',
                         'message': 'No ink above alpha=1; complete nonzero footprint used for geometry. Visual quality remains unverified.'})
    iw, ih = ink[2]-ink[0], ink[3]-ink[1]
    scale = min(tw/iw, th/ih)
    ink_target = [tx+(tw-iw*scale)/2, ty+(th-ih*scale)/2,
                  iw*scale, ih*scale]
    picture_target = [ink_target[0]+(left-ink[0])*scale,
                      ink_target[1]+(top-ink[1])*scale,
                      natural[0]*scale, natural[1]*scale]
    # This is an engineering bound in slide pixels, not a halo/quality cutoff.
    # Check before publishing any extracted asset; the provider PNG is intact.
    _check_placement_budget(picture_target)
    asset['target'] = picture_target
    asset['stretch'] = [scale, scale]
    asset['placement'] = {'policy': 'preserve-native-alpha-uniform-contain/2',
                          'fit': 'contain', 'ink_bbox': ink, 'ink_bbox_basis': basis,
                          'alpha_threshold_for_geometry_only': 1,
                          'visible_ink_target': ink_target, 'uniform_scale': scale,
                          'all_rgba_pixels_preserved': True}
    if any(not math.isclose(actual, desired, rel_tol=1e-12, abs_tol=1e-9)
           for actual, desired in zip(picture_target, (tx, ty, tw, th))):
        warnings.append({'id': identity, 'reason': 'native_wordart_uniform_contain_fit',
                         'authored_target': list(target), 'picture_target': picture_target,
                         'visible_ink_target': ink_target,
                         'message': 'Native ink proportions preserved inside the authored box; the title may be narrower and its transparent picture frame may extend beyond it. All alpha and RGB remain unchanged.'})
    mapping = {'status': 'aligned', 'cells': {identity: [0, 0, width, height]},
               'warnings': [], 'unsafe_members': [], 'policy': MODE}
    destination.mkdir(parents=True, exist_ok=True)
    Image.fromarray(crop).save(output, format='PNG')
    return {'assets': [asset], 'warnings': warnings, 'cell_mapping': mapping, 'validation': inspection}
