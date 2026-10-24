"""Resolve complete regular-board cells using observed transparent gutters.

No identity is guessed. Searches stay between neighboring planned cell centers,
and no alpha values change. An absent separating band is explicit ambiguity.
"""
from __future__ import annotations
import math
import numpy as np
from PIL import Image
from .matte import _regions

_NARROW_UNCONFIRMED_COMPONENT_PX = 8
_NARROW_UNCONFIRMED_TOTAL_PX = 64


def _scaled_slot(slot, reference_size, actual_size):
    x,y,w,h = slot
    rw,rh = reference_size
    aw,ah = actual_size
    return [max(0,math.floor(x*aw/rw)),max(0,math.floor(y*ah/rh)),
            min(aw,math.ceil((x+w)*aw/rw)),min(ah,math.ceil((y+h)*ah/rh))]


def _reference_supports_group_gap(projection,reference,lo,hi,planned,gap,band,radius,unconfirmed):
    if reference is None:return False
    start,stop=band
    gutter0=max(0,math.floor(planned-gap/2))
    gutter1=min(len(projection),math.ceil(planned+gap/2))
    if not (start<=gutter0 and stop>=gutter1):return False
    if (np.any(reference[gutter0:gutter1]) or not np.any(reference[lo:gutter0])
            or not np.any(reference[gutter1:hi])):return False
    if unconfirmed is not None and np.any(unconfirmed[lo:hi]):return False
    padded=np.pad(reference.astype(np.int32),(radius,radius))
    sums=np.pad(np.cumsum(padded),(1,0))
    support=sums[2*radius+1:]-sums[:-(2*radius+1)]>0
    return bool(np.all(~projection[lo:hi]|support[lo:hi]))


def _separators(projection, count, cell_size, margin, gap, scale,
                reference=None,radius=0,supported=None,unconfirmed=None,narrow_accept=None):
    cuts, failed, ambiguous, changed = [], [], [], False
    for index in range(count-1):
        left_center = (margin + cell_size/2 + index*(cell_size+gap))*scale
        right_center = left_center + (cell_size+gap)*scale
        planned = (margin+(index+1)*cell_size+index*gap+gap/2)*scale
        lo = max(0,math.ceil(left_center))
        hi = min(len(projection),math.floor(right_center)+1)
        empty = ~projection[lo:hi]
        changes = np.flatnonzero(np.diff(np.pad(empty.astype(np.int8),(1,1))))
        minimum = max(2,math.ceil(gap*scale/4))
        choices = [];bands={};narrow=[]
        for start,stop in changes.reshape(-1,2):
            start,stop = lo+int(start),lo+int(stop)
            if stop-start < minimum:
                if 2<=stop-start<=5 and lo<start<stop<hi:narrow.append((start,stop))
                continue
            cut = min(max(round(planned),start+1),stop-1)
            choices.append((abs(cut-planned),-(stop-start),cut))
            bands[cut]=(start,stop)
        if choices:
            cut = min(choices)[-1]
            # Distinct blank bands have visible islands between them. Without
            # identity evidence the island could belong to either neighbor;
            # choosing the closest band would silently move a detached stroke.
            if len(choices)>1:
                if _reference_supports_group_gap(projection,reference,lo,hi,planned,
                                                 gap*scale,bands[cut],radius,unconfirmed):
                    if supported is not None:supported.append(index)
                else:ambiguous.append(index)
        else:
            cut = round(planned)
            accepted=False
            if narrow_accept is not None and len(narrow)==1:
                start,stop=narrow[0]
                candidate=min(max(cut,start+1),stop-1)
                accepted=narrow_accept(index,lo,hi,start,stop,candidate,minimum)
                if accepted:cut=candidate
            if not accepted:failed.append(index)
        cuts.append(cut)
        changed = changed or abs(cut-planned) > 1
    return cuts,failed,ambiguous,changed


def resolve_cells(board: dict, alpha: np.ndarray, *, check_canvas_edges: bool = True,
                  reference_alpha: np.ndarray | None = None,
                  native_geometry: bool = False) -> dict:
    """Return cells, status, warnings and unsafe member IDs before cutting.

    ``alpha`` is H x W; alpha > 0 includes disconnected and translucent marks.
    Cells use half-open [x0,y0,x1,y1]. Callers must reject unsafe members.
    With no ``grid``, return historical scaled slots with no new judgments.
    Extractors may defer the canvas-edge check until reference-supported
    subject selection; they must then reject retained subjects at that edge.
    """
    pixels = np.asarray(alpha)
    if pixels.ndim != 2 or not pixels.shape[0] or not pixels.shape[1]:
        raise ValueError("alpha must be a nonempty H x W array")
    height,width = pixels.shape
    reference_size = tuple(board["size"])
    if len(reference_size)!=2 or any(not math.isfinite(v) or v<=0 for v in reference_size):
        raise ValueError("Board dimensions must be positive and finite")
    members = board["members"]
    cells = {member["id"]:_scaled_slot(member["slot"],reference_size,(width,height))
             for member in members}
    result = {"status":"legacy", "cells":cells, "warnings":[], "unsafe_members":[]}
    grid = board.get("grid")
    if not grid:
        return result
    rows,columns = grid["rows"],grid["columns"]
    margin,gap = grid["margin"],grid["gap"]
    cell_width,cell_height = grid["cell_size"]
    if (type(rows) is not int or type(columns) is not int or rows<1 or columns<1
            or min(margin,gap,cell_width,cell_height)<=0):
        raise ValueError("Grid geometry must have positive cell counts, margins and sizes")
    occupied = pixels > 0
    reference=None
    if reference_alpha is not None:
        given=np.asarray(reference_alpha)
        if given.ndim!=2 or not all(given.shape):raise ValueError('reference alpha must be nonempty H x W')
        reference=np.asarray(Image.fromarray((given>0).astype(np.uint8)).resize(
            (width,height),Image.Resampling.NEAREST))>0
    # Native provider overspray at alpha=1 must not join otherwise independent
    # subjects. This mask is geometry only: extraction still uses original RGBA.
    # Missing reference or an alpha1-only member keeps the historical strict
    # interpretation; an unsupported faint subject is never silently erased.
    local_rows=False
    if native_geometry and reference is not None and np.any(reference):
        core=pixels>1
        if all(np.any(core[b:d,a:c]) for a,b,c,d in cells.values()):
            occupied=core
            local_rows=True
            result.update(geometry_policy='native-core-local-rows/1',
                          alpha_threshold_for_geometry_only=1,
                          all_rgba_pixels_preserved=True)
    crossed=set();unconfirmed=None
    if reference is not None:
        labels,regions=_regions(occupied)
        owners={}
        for member in members:
            a,b,c,d=_scaled_slot(member['slot'],reference_size,(width,height))
            for label in np.unique(labels[b:d,a:c][reference[b:d,a:c]&occupied[b:d,a:c]]):
                if label:owners.setdefault(int(label),set()).add(member['id'])
        for identities in owners.values():
            if len(identities)>1:crossed.update(identities)
        # A matching projection in another row/column is not identity evidence.
        # Every island involved in resolving ambiguity needs its own 2-D match
        # inside exactly one planned member, including faint/disconnected marks.
        confirmed=[label for label,identities in owners.items() if len(identities)==1]
        unconfirmed=occupied&~np.isin(labels,confirmed)
    radius=max(2,math.ceil(min(cell_width*width/reference_size[0],
                              cell_height*height/reference_size[1])/28))
    supported_x=[];supported_y=[]
    vertical,bad_columns,ambiguous_columns,changed_x = _separators(occupied.any(axis=0),columns,cell_width,
                                               margin,gap,width/reference_size[0],
                                               None if reference is None else reference.any(axis=0),radius,supported_x,
                                               None if unconfirmed is None else unconfirmed.any(axis=0))
    horizontal,bad_rows,ambiguous_rows,changed_y = _separators(occupied.any(axis=1),rows,cell_height,
                                              margin,gap,height/reference_size[1],
                                              None if reference is None else reference.any(axis=1),radius,supported_y,
                                              None if unconfirmed is None else unconfirmed.any(axis=1))
    column_rows={}
    if local_rows:
        xs=[0,*vertical,width]
        supported_y=[];changed_y=False
        region_boxes={region['id']:region['box'] for region in regions}
        region_pixels=np.bincount(labels.ravel())
        for column in range(columns):
            x0,x1=xs[column:column+2]
            supported=[]
            def accept_narrow(index,lo,hi,start,stop,cut,minimum):
                # Only a full empty alpha>1 row band can enter here. Reference
                # ownership must put every known adjacent subject on its own
                # side; no alpha is removed and unknown specks stay recorded.
                adjacent=[]
                for row in (index,index+1):
                    matches=[member['id'] for member in members
                        if round((member['slot'][0]-margin)/(cell_width+gap))==column
                        and round((member['slot'][1]-margin)/(cell_height+gap))==row]
                    if len(matches)!=1:return False
                    adjacent.append(matches[0])
                if any(identity in crossed for identity in adjacent):return False
                groups=[[label for label,identities in owners.items() if identities=={identity}]
                        for identity in adjacent]
                if not all(groups):return False
                before,after=groups
                if (any(region_boxes[label][3]>start for label in before)
                        or any(region_boxes[label][1]<stop for label in after)):
                    return False
                if (not any(region_boxes[label][3]>lo for label in before)
                        or not any(region_boxes[label][1]<hi for label in after)):
                    return False
                observed=[int(label) for label in np.unique(labels[lo:hi,x0:x1]) if label]
                if any(owners.get(label,set())-set(adjacent) for label in observed):return False
                unowned=[label for label in observed if not owners.get(label)]
                sizes=[int(region_pixels[label]) for label in unowned]
                unowned_pixels=sum(sizes)
                unowned_max=max(sizes,default=0)
                # These fixed engineering budgets allow only discrete native
                # overspray, not an unrecognised word or extra subject. Count
                # each complete component even if only part enters the search.
                # Pixels are never deleted, and this is not identity acceptance.
                budget={'unconfirmed_core_pixels':unowned_pixels,
                        'max_unconfirmed_component_core_pixels':unowned_max,
                        'unconfirmed_component_budget_px':_NARROW_UNCONFIRMED_COMPONENT_PX,
                        'unconfirmed_total_budget_px':_NARROW_UNCONFIRMED_TOTAL_PX}
                if (unowned_max>_NARROW_UNCONFIRMED_COMPONENT_PX
                        or unowned_pixels>_NARROW_UNCONFIRMED_TOTAL_PX):
                    result['warnings'].append({'code':'unconfirmed_native_grid_fragments_over_budget',
                        'axis':'y','column':column,'index':index,'members':adjacent,**budget})
                    return False
                note={'policy':'unique-reference-owned-narrow-native-row-gutter/1',
                      'axis':'y','column':column,'index':index,'band':[start,stop],
                      'width_px':stop-start,'normal_minimum_px':minimum,'cut':cut,
                      'members':adjacent,'owner_component_counts':[len(group) for group in groups],
                      'unconfirmed_components_in_search':len(unowned),**budget,
                      'identity_quality':'not_evaluated',
                      'alpha_threshold_for_geometry_only':1,'all_rgba_pixels_preserved':True}
                result.setdefault('narrow_gutter_recoveries',[]).append(note)
                result['warnings'].append({'code':'reference_supported_narrow_grid_gutter',**note})
                return True
            local=_separators(occupied[:,x0:x1].any(axis=1),rows,cell_height,
                              margin,gap,height/reference_size[1],
                              reference[:,x0:x1].any(axis=1),radius,supported,
                              None if unconfirmed is None else unconfirmed[:,x0:x1].any(axis=1),
                              narrow_accept=accept_narrow)
            column_rows[column]=local
            changed_y=changed_y or local[3]
            result.setdefault('column_row_cuts',{})[str(column)]=list(local[0])
            for index in supported:
                note={'axis':'y','column':column,'index':index,'cut':local[0][index]}
                result.setdefault('reference_supported_boundaries',[]).append(note)
                result['warnings'].append({'code':'reference_supported_grid_boundary',**note})
    if supported_x or supported_y:
        result.setdefault('reference_supported_boundaries',[]).extend([{'axis':axis,'index':index,'cut':cuts[index]}
            for axis,indices,cuts in [('x',supported_x,vertical),('y',supported_y,horizontal)] for index in indices])
        result['warnings'].extend({'code':'reference_supported_grid_boundary','axis':axis,'index':index,'cut':cuts[index]}
            for axis,indices,cuts in [('x',supported_x,vertical),('y',supported_y,horizontal)] for index in indices)
    xs,ys = [0,*vertical,width], [0,*horizontal,height]
    unsafe,used = set(),set()
    for member in members:
        identity = member["id"]
        x,y,w,h = member["slot"]
        column = round((x-margin)/(cell_width+gap))
        row = round((y-margin)/(cell_height+gap))
        if not (0<=row<rows and 0<=column<columns) or (row,column) in used:
            raise ValueError("Member slots must uniquely identify a regular grid cell")
        used.add((row,column))
        if local_rows:
            local,bad_rows,ambiguous_rows,_=column_rows[column]
            local_ys=[0,*local,height]
        else:local_ys=ys
        bounds = [xs[column],local_ys[row],xs[column+1],local_ys[row+1]]
        cells[identity] = bounds
        def warn(code,**extra):
            unsafe.add(identity)
            result["warnings"].append({"id":identity,"code":code,**extra})
        if identity in crossed:warn('reference_cross_grid_component')
        if any(column in (boundary,boundary+1) for boundary in bad_columns):
            warn("unseparable_grid_boundary",axis="x")
        if any(row in (boundary,boundary+1) for boundary in bad_rows):
            warn("unseparable_grid_boundary",axis="y")
        if any(column in (boundary,boundary+1) for boundary in ambiguous_columns):
            warn("ambiguous_grid_fragments",axis="x")
        if any(row in (boundary,boundary+1) for boundary in ambiguous_rows):
            warn("ambiguous_grid_fragments",axis="y")
        x0,y0,x1,y1 = bounds
        if x1<=x0 or y1<=y0:
            warn("unresolvable_cell_bounds")
            continue
        content = occupied[y0:y1,x0:x1]
        if not np.any(content):
            warn("empty_grid_cell")
            continue
        if check_canvas_edges and ((x0==0 and np.any(content[:,0])) or (x1==width and np.any(content[:,-1]))
                or (y0==0 and np.any(content[0,:])) or (y1==height and np.any(content[-1,:]))):
            warn("subject_clipped_at_canvas_edge")
    result["unsafe_members"] = sorted(unsafe)
    result["status"] = "ambiguous" if unsafe else "adjusted" if changed_x or changed_y else "aligned"
    return result
