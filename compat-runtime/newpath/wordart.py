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

from .matte import _regions

MODE = 'standalone-native-wordart/2'
_PAGE_SIZE = (1920, 1080)
_PLACEMENT_PAGE_LIMIT = 4
_WEAK_COMPONENT_MAX_ALPHA = 3
_WEAK_COMPONENT_MAX_PIXELS = 8
_WEAK_COMPONENT_TOTAL_PIXELS = 64
_WEAK_ALPHA_MASS_DENOMINATOR = 4096
_PRIMARY_AUTO = object()


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


def select_primary_wordart(arts, *, primary_id=_PRIMARY_AUTO):
    """Return deep copies of the largest written artwork and the remaining art.

    Prefer target area, then source y/x and identity for stable ties. Remaining
    objects retain their original order and stay in the ordinary board workflow.
    An explicit None keeps all art together; an explicit ID selects that member.
    """
    copied = copy.deepcopy(list(arts))
    if primary_id is None:
        return None, copied
    if primary_id is not _PRIMARY_AUTO and (not isinstance(primary_id, str) or not primary_id.strip()):
        raise ValueError('An explicit primary artwork ID must name eligible written artwork.')
    candidates = []
    for index, member in enumerate(copied):
        lettering = member.get('lettering')
        if member.get('embedded_image') is True or not isinstance(lettering, str) or not lettering.strip():
            continue
        identity = member.get('id')
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('Written artwork requires a nonempty string identity')
        source = _box(member.get('box'), 'Written artwork source box')
        target = _box(member.get('target', member.get('box')), 'Written artwork target')
        candidates.append(((-target[2]*target[3], source[1], source[0], identity), index))
    if primary_id is not _PRIMARY_AUTO:
        selected = next((index for _, index in candidates if copied[index]['id'] == primary_id), None)
        if selected is None:
            raise ValueError('The primary artwork ID does not name eligible written artwork.')
        return copied[selected], copied[:selected] + copied[selected+1:]
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
        # Strong or connected edge ink remains a clipping risk. Higher-alpha
        # overspray needs the existing complete weak-group mass recovery.
        if not np.any(alpha > 1):
            raise ValueError('Standalone wordart touches a canvas edge or has an opaque background')
        edge_max = max(int(edge.max()) for edge in edges.values())
        if edge_max > 1:
            core = alpha > 1
            labels, regions = _regions(core)
            weak_groups, retained_mass = _weak_alpha_groups(alpha, labels, regions)
            weak_mass = sum(group['alpha_mass'] for group in weak_groups)
            if (edge_max > _WEAK_COMPONENT_MAX_ALPHA or not weak_mass or not retained_mass
                    or weak_mass * _WEAK_ALPHA_MASS_DENOMINATOR > retained_mass):
                raise ValueError('Standalone wordart touches a canvas edge or has an opaque background')
            retained = core & ~np.isin(labels, [group['label'] for group in weak_groups])
            yy, xx = np.nonzero(retained)
            ink = [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]
            if not (0 < ink[0] < ink[2] < alpha.shape[1]
                    and 0 < ink[1] < ink[3] < alpha.shape[0]):
                raise ValueError('Standalone wordart touches a canvas edge or has an opaque background')
            warnings.append({
                'reason': 'native_wordart_bounded_weak_edge_overspray',
                'edge_max_alpha': edge_max,
                'edge_nonzero_pixels': {name: int(np.count_nonzero(edge))
                                        for name, edge in edges.items()},
                'edge_validation': {
                    'policy': 'bounded-weak-wordart-edge-alpha-mass/1',
                    'retained_ink_bbox': ink,
                    'weak_component_count': len(weak_groups),
                    'weak_pixel_count': sum(group['pixels'] for group in weak_groups),
                    'weak_alpha_mass': weak_mass,
                    'retained_alpha_mass': retained_mass,
                    'max_weak_alpha': _WEAK_COMPONENT_MAX_ALPHA,
                    'mass_ratio_denominator': _WEAK_ALPHA_MASS_DENOMINATOR,
                },
                'all_rgba_pixels_preserved': True,
                'message': 'Complete disconnected weak edge groups meet the existing opacity-mass '
                           'geometry limit and the retained ink is fully inset. All provider RGBA '
                           'pixels are preserved; visual quality remains unverified.',
            })
        else:
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


def _weak_alpha_groups(alpha, labels, regions):
    """Classify complete groups using the existing weak-opacity limit."""
    candidates = []
    retained_mass = 0
    for region in regions:
        left, top, right, bottom = region['box']
        samples = alpha[top:bottom, left:right][labels[top:bottom, left:right] == region['id']]
        mass = int(samples.sum(dtype=np.uint64))
        maximum = int(samples.max())
        if maximum <= _WEAK_COMPONENT_MAX_ALPHA:
            candidates.append({
                'label': region['id'],
                'box': region['box'],
                'pixels': int(len(samples)),
                'max_alpha': maximum,
                'alpha_mass': mass,
            })
        else:
            retained_mass += mass
    return candidates, retained_mass


def _alpha_mass_bounds(alpha, core, labels, regions, original):
    """Keep whole stronger groups while bounding disconnected weak opacity."""
    candidates, retained_mass = _weak_alpha_groups(alpha, labels, regions)
    weak_mass = sum(region['alpha_mass'] for region in candidates)
    if not weak_mass or weak_mass * _WEAK_ALPHA_MASS_DENOMINATOR > retained_mass:
        return original, None
    geometry = core & ~np.isin(labels, [region['label'] for region in candidates])
    yy, xx = np.nonzero(geometry)
    if not len(xx):
        return original, None
    bounds = [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]
    # Preserve one-pixel antialias extents and the established fitted position.
    if all(abs(actual - previous) <= 1 for actual, previous in zip(bounds, original)):
        return original, None
    return bounds, {
        'policy': 'bounded-weak-wordart-alpha-mass/1',
        'original_ink_bbox': original,
        'recovered_ink_bbox': bounds,
        'component_count': len(candidates),
        'pixel_count': sum(region['pixels'] for region in candidates),
        'alpha_mass': weak_mass,
        'retained_alpha_mass': retained_mass,
        'mass_ratio_denominator': _WEAK_ALPHA_MASS_DENOMINATOR,
        'preserved_extent_difference_px': 1,
        'max_alpha': _WEAK_COMPONENT_MAX_ALPHA,
        'all_rgba_pixels_preserved': True,
    }


def _ink_bounds(alpha):
    """Use bounded weak islands as geometric evidence while retaining RGBA."""
    core = alpha > 1
    yy, xx = np.nonzero(core)
    if not len(xx):
        return None, None
    original = [int(xx.min()), int(yy.min()), int(xx.max())+1, int(yy.max())+1]
    # A uniformly faint subject has no stronger ink supporting an exclusion.
    if not np.any(alpha > _WEAK_COMPONENT_MAX_ALPHA):
        return original, None
    labels, regions = _regions(core)
    counts = np.bincount(labels.ravel())
    candidates = []
    for region in regions:
        identity = region['id']
        count = int(counts[identity])
        if count > _WEAK_COMPONENT_MAX_PIXELS:
            continue
        left, top, right, bottom = region['box']
        values = alpha[top:bottom,left:right][labels[top:bottom,left:right] == identity]
        maximum = int(values.max())
        if maximum <= _WEAK_COMPONENT_MAX_ALPHA:
            candidates.append({'label':identity, 'box':region['box'],
                               'pixels':count, 'max_alpha':maximum})
    total = sum(region['pixels'] for region in candidates)
    if not 0 < total <= _WEAK_COMPONENT_TOTAL_PIXELS:
        return _alpha_mass_bounds(alpha,core,labels,regions,original)
    geometry = core & ~np.isin(labels, [region['label'] for region in candidates])
    yy, xx = np.nonzero(geometry)
    bounds = [int(xx.min()), int(yy.min()), int(xx.max())+1, int(yy.max())+1]
    if bounds == original:
        return _alpha_mass_bounds(alpha,core,labels,regions,original)
    return bounds, {'policy':'bounded-weak-wordart-geometry/1',
                    'original_ink_bbox':original, 'recovered_ink_bbox':bounds,
                    'ignored_for_geometry_only':candidates, 'pixel_count':total,
                    'component_pixel_limit':_WEAK_COMPONENT_MAX_PIXELS,
                    'total_pixel_limit':_WEAK_COMPONENT_TOTAL_PIXELS,
                    'max_alpha':_WEAK_COMPONENT_MAX_ALPHA,
                    'all_rgba_pixels_preserved':True}


def _fit_ink(ink, target, crop_box):
    tx, ty, tw, th = target
    left, top, right, bottom = crop_box
    iw, ih = ink[2] - ink[0], ink[3] - ink[1]
    scale = min(tw / iw, th / ih)
    ink_target = [tx + (tw - iw * scale) / 2, ty + (th - ih * scale) / 2,
                  iw * scale, ih * scale]
    picture_target = [ink_target[0] + (left - ink[0]) * scale,
                      ink_target[1] + (top - ink[1]) * scale,
                      (right - left) * scale, (bottom - top) * scale]
    return scale, ink_target, picture_target


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
    ink, geometry_recovery = _ink_bounds(pixels[..., 3])
    if ink is not None:
        basis = 'alpha_greater_than_one'
        if geometry_recovery:
            basis = 'alpha_greater_than_one_with_bounded_weak_islands'
    else:
        # A technically valid PNG can contain only alpha=1. Do not divide by an
        # absent core or delete it: fit its complete preserved footprint instead.
        ink = [left, top, right, bottom]
        basis = 'all_nonzero_alpha_fallback'
        warnings.append({'id': identity, 'reason': 'native_wordart_alpha1_only',
                         'message': 'No ink above alpha=1; complete nonzero footprint used for geometry. Visual quality remains unverified.'})
    scale,ink_target,picture_target=_fit_ink(ink,(tx,ty,tw,th),(left,top,right,bottom))
    # This is an engineering bound in slide pixels, not a halo/quality cutoff.
    # Check before publishing any extracted asset; the provider PNG is intact.
    try:
        _check_placement_budget(picture_target)
    except ValueError:
        if not geometry_recovery or geometry_recovery['policy']!='bounded-weak-wordart-alpha-mass/1':
            raise
        warnings.append({'id':identity,'reason':'native_wordart_alpha_mass_budget_fallback',
                         'candidate_ink_bbox':ink,'candidate_picture_target':picture_target,
                         'retained_ink_bbox':geometry_recovery['original_ink_bbox'],
                         'all_rgba_pixels_preserved':True,
                         'message':'The opacity-mass geometry candidate exceeds the placement safety budget; '
                                   'the established ink geometry is retained.'})
        ink=geometry_recovery['original_ink_bbox']
        geometry_recovery=None
        basis='alpha_greater_than_one'
        scale,ink_target,picture_target=_fit_ink(ink,(tx,ty,tw,th),(left,top,right,bottom))
        _check_placement_budget(picture_target)
    if geometry_recovery:
        warnings.append({'id':identity, 'reason':'native_wordart_weak_geometry_fragments',
                         **geometry_recovery,
                         'message':'Bounded weak disconnected islands affect the retained picture frame only; '
                                   'stronger ink defines placement. Every original RGBA pixel remains unchanged.'})
    asset['target'] = picture_target
    asset['stretch'] = [scale, scale]
    asset['placement'] = {'policy': 'preserve-native-alpha-uniform-contain/2',
                          'fit': 'contain', 'ink_bbox': ink, 'ink_bbox_basis': basis,
                          'alpha_threshold_for_geometry_only': 1,
                          'visible_ink_target': ink_target, 'uniform_scale': scale,
                          'all_rgba_pixels_preserved': True}
    if geometry_recovery:
        asset['placement']['geometry_recovery'] = geometry_recovery
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
