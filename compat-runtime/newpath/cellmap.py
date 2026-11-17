"""Resolve complete regular-board cells using observed transparent gutters.

No identity is guessed. Searches stay between neighboring planned cell centers,
and no alpha values change. An absent separating band is explicit ambiguity.
"""
from __future__ import annotations
import math
import numpy as np
from PIL import Image
from .matte import _regions, _expand
from .native import MODE

_NARROW_UNCONFIRMED_COMPONENT_PX = 8
_NARROW_UNCONFIRMED_TOTAL_PX = 64
_WEAK_UNCONFIRMED_MAX_ALPHA = 3
FIXED_GRID_POLICY = 'native-fixed-grid-low-alpha-background/1'


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
                reference=None,radius=0,supported=None,unconfirmed=None,narrow_accept=None,
                owned_accept=None,weak_accept=None):
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
                else:
                    recovered=None if owned_accept is None else owned_accept(
                        index,lo,hi,[(choice[-1],bands[choice[-1]]) for choice in choices])
                    if recovered is None and weak_accept is not None:
                        recovered=weak_accept(index,lo,hi,planned,cut,minimum)
                    if recovered is None:ambiguous.append(index)
                    else:
                        cut=recovered
                        if supported is not None:supported.append(index)
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


def _resolve_cells(board: dict, alpha: np.ndarray, *, check_canvas_edges: bool = True,
                  reference_alpha: np.ndarray | None = None,
                  native_geometry: bool = False, reference_fragment_radius=0,
                   context=None, actual_anchor_pixel_budget=None) -> dict:
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
        if context is not None:
            context.update(labels=labels,regions=regions,owners=owners,reference=reference)
        if reference_fragment_radius:
            attached=_attach_reference_fragments(board,pixels,labels,regions,reference,
                                                  owners,reference_fragment_radius)
            if actual_anchor_pixel_budget is not None:
                actual_attached,actual_report=_attach_actual_anchor_fragments(
                    board,pixels,labels,regions,owners,actual_anchor_pixel_budget)
                attached.extend(actual_attached)
                if actual_report:result['actual_anchor_support']=actual_report
            result['reference_supported_fragments']=attached
            result['reference_fragments_unresolved']=sum(not owners.get(region['id']) for region in regions)
        for identities in owners.values():
            if len(identities)>1:crossed.update(identities)
        # A matching projection in another row/column is not identity evidence.
        # Every island involved in resolving ambiguity needs its own 2-D match
        # inside exactly one planned member, including faint/disconnected marks.
        confirmed=[label for label,identities in owners.items() if len(identities)==1]
        unconfirmed=occupied&~np.isin(labels,confirmed)
    weak_occupied=None
    weak_labels=[]
    weak_max_alpha={}
    eligible_pixels=0
    if local_rows:
        region_pixels=np.bincount(labels.ravel())
        for region in regions:
            label=region['id']
            if owners.get(label) or region_pixels[label]>_NARROW_UNCONFIRMED_COMPONENT_PX:
                continue
            a,b,c,d=region['box']
            values=pixels[b:d,a:c][labels[b:d,a:c]==label]
            if int(values.max())<=_WEAK_UNCONFIRMED_MAX_ALPHA:
                weak_labels.append(label)
                weak_max_alpha[label]=int(values.max())
        eligible_pixels=sum(int(region_pixels[label]) for label in weak_labels)
        if 0<eligible_pixels<=_NARROW_UNCONFIRMED_TOTAL_PX:
            weak_mask=np.isin(labels,weak_labels)
            weak_occupied=occupied&~weak_mask
            weak_unconfirmed=unconfirmed&~weak_mask
    radius=max(2,math.ceil(min(cell_width*width/reference_size[0],
                              cell_height*height/reference_size[1])/28))

    def weak_acceptor(axis,column=None,x_bounds=None):
        if weak_occupied is None:return None
        # Retrying a disputed gutter changes only its geometric evidence.
        # Original occupancy still controls ownership, edges and image output.
        if axis=='x':
            projection=weak_occupied.any(axis=0)
            reference_projection=reference.any(axis=0)
            unconfirmed_projection=weak_unconfirmed.any(axis=0)
            local_labels=labels
            scaled_gap=gap*width/reference_size[0]
        else:
            x0,x1=x_bounds
            projection=weak_occupied[:,x0:x1].any(axis=1)
            reference_projection=reference[:,x0:x1].any(axis=1)
            unconfirmed_projection=weak_unconfirmed[:,x0:x1].any(axis=1)
            local_labels=labels[:,x0:x1]
            scaled_gap=gap*height/reference_size[1]

        def accept(index,lo,hi,planned,cut,minimum):
            empty=~projection[lo:hi]
            changes=np.flatnonzero(np.diff(np.pad(empty.astype(np.int8),(1,1))))
            bands=[(lo+int(start),lo+int(stop)) for start,stop in changes.reshape(-1,2)
                   if stop-start>=minimum]
            if len(bands)!=1:return None
            start,stop=bands[0]
            if not start<=cut<stop:return None
            if not _reference_supports_group_gap(projection,reference_projection,lo,hi,
                    planned,scaled_gap,(start,stop),radius,unconfirmed_projection):
                return None
            search=local_labels[:,lo:hi] if axis=='x' else local_labels[lo:hi]
            involved=np.intersect1d(np.unique(search),weak_labels)
            if not len(involved):return None
            sizes=[int(region_pixels[label]) for label in involved]
            note={'policy':'bounded-weak-native-gutter/1','axis':axis,'index':index,
                  'band':[start,stop],'cut':cut,'unconfirmed_components_in_search':len(sizes),
                  'unconfirmed_core_pixels':sum(sizes),
                  'max_unconfirmed_component_core_pixels':max(sizes),
                  'eligible_board_core_pixels':eligible_pixels,
                  'unconfirmed_component_budget_px':_NARROW_UNCONFIRMED_COMPONENT_PX,
                  'unconfirmed_total_budget_px':_NARROW_UNCONFIRMED_TOTAL_PX,
                  'max_unconfirmed_alpha':max(weak_max_alpha[int(label)] for label in involved),
                  'unconfirmed_alpha_limit':_WEAK_UNCONFIRMED_MAX_ALPHA,
                  'identity_quality':'not_evaluated','all_rgba_pixels_preserved':True}
            if column is not None:note['column']=column
            result.setdefault('weak_speck_gutter_recoveries',[]).append(note)
            result['warnings'].append({'code':'weak_native_grid_speck_tolerance',**note})
            return cut
        return accept

    supported_x=[];supported_y=[]
    vertical,bad_columns,ambiguous_columns,changed_x = _separators(occupied.any(axis=0),columns,cell_width,
                                               margin,gap,width/reference_size[0],
                                               None if reference is None else reference.any(axis=0),radius,supported_x,
                                               None if unconfirmed is None else unconfirmed.any(axis=0),
                                               weak_accept=weak_acceptor('x'))
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
            def accept_owned(index,lo,hi,candidates):
                # A small written group may sit outside the center-to-center
                # search, yet every one of its 2-D components has a known
                # reference owner. Accept only the unique real empty band that
                # keeps the complete adjacent groups on their proper sides.
                adjacent=[]
                for row in (index,index+1):
                    matches=[member['id'] for member in members
                        if round((member['slot'][0]-margin)/(cell_width+gap))==column
                        and round((member['slot'][1]-margin)/(cell_height+gap))==row]
                    if len(matches)!=1:return None
                    adjacent.append(matches[0])
                if any(identity in crossed for identity in adjacent):return None
                groups=[[label for label,identities in owners.items() if identities=={identity}]
                        for identity in adjacent]
                if not all(groups):return None
                before,after=groups
                known=set(before+after)
                span0=min(lo,*(region_boxes[label][1] for label in known))
                span1=max(hi,*(region_boxes[label][3] for label in known))
                observed={int(label) for label in np.unique(labels[span0:span1,x0:x1]) if label}
                # Even a single unknown core pixel is identity evidence we do
                # not possess. Unlike the separate narrow-speck allowance,
                # this path never allocates an unowned island to a member.
                if observed-known:return None
                feasible=[]
                for cut,(start,stop) in candidates:
                    if (all(region_boxes[label][3]<=start for label in before)
                            and all(region_boxes[label][1]>=stop for label in after)):
                        feasible.append((cut,start,stop))
                if len(feasible)!=1:return None
                cut,start,stop=feasible[0]
                note={'policy':'unique-reference-owned-native-row-gutter/1',
                      'axis':'y','column':column,'index':index,'band':[start,stop],
                      'cut':cut,'members':adjacent,
                      'owner_component_counts':[len(group) for group in groups],
                      'unconfirmed_core_pixels':0,'identity_quality':'not_evaluated',
                      'alpha_threshold_for_geometry_only':1,'all_rgba_pixels_preserved':True}
                result.setdefault('owned_gutter_recoveries',[]).append(note)
                result['warnings'].append({'code':'reference_owned_grid_gutter',**note})
                return cut
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
                              narrow_accept=accept_narrow,owned_accept=accept_owned,
                              weak_accept=weak_acceptor('y',column,(x0,x1)))
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


def _attach_reference_fragments(board,pixels,labels,regions,reference,owners,radius):
    """A complete faint fragment must fit exactly one finite reference support."""
    height,width=pixels.shape
    supports={}
    for member in board['members']:
        a,b,c,d=_scaled_slot(member['slot'],board['size'],(width,height))
        seed=np.zeros((height,width),dtype=bool)
        seed[b:d,a:c]=reference[b:d,a:c]
        supports[member['id']]=_expand(seed,radius)
    attached=[]
    for region in regions:
        label=region['id']
        if owners.get(label):continue
        a,b,c,d=region['box']
        mask=labels[b:d,a:c]==label
        maximum=int(pixels[b:d,a:c][mask].max())
        if maximum>8:continue
        matches=[identity for identity,support in supports.items() if np.all(support[b:d,a:c][mask])]
        if len(matches)!=1:continue
        owners[label]={matches[0]}
        attached.append({'owner':matches[0],'box':list(region['box']),
                         'pixels':int(mask.sum()),'max_alpha':maximum})
    return attached


def _attach_actual_anchor_fragments(board,pixels,labels,regions,owners,total_budget):
    """Attach complete faint edges near one already reference-confirmed strong group."""
    height,width=pixels.shape
    grid=board['grid']
    cell_width=grid['cell_size'][0]*width/board['size'][0]
    cell_height=grid['cell_size'][1]*height/board['size'][1]
    radius=max(2,math.ceil(min(cell_width,cell_height)/64))
    component_budget=max(8,math.ceil(cell_width*cell_height/1024))
    supports={}
    for member in board['members']:
        identity=member['id']
        known=[label for label,identities in owners.items() if identities=={identity}]
        seed=np.isin(labels,known)&(pixels>16)
        if not np.any(seed):return [],None
        supports[identity]=_expand(seed,radius)
    # Build every support before assigning any fragment. Attached faint pixels
    # cannot become seeds or extend another fragment's permitted distance.
    proposed=[]
    for region in regions:
        label=region['id']
        if owners.get(label):continue
        a,b,c,d=region['box']
        mask=labels[b:d,a:c]==label
        area=int(mask.sum())
        maximum=int(pixels[b:d,a:c][mask].max())
        if area>component_budget or maximum>16:continue
        matches=[identity for identity,support in supports.items()
                 if np.all(support[b:d,a:c][mask])]
        if len(matches)!=1:continue
        proposed.append({'owner':matches[0],'box':list(region['box']),
                         'pixels':area,'max_alpha':maximum,'label':label,
                         'support':'reference-confirmed-actual-strong-pixels'})
    attached_pixels=sum(fragment['pixels'] for fragment in proposed)
    if not proposed or attached_pixels>total_budget:return [],None
    for fragment in proposed:
        owners[fragment.pop('label')]={fragment['owner']}
    report={'policy':'bounded-actual-anchor-fragments/1',
            'seed_alpha_threshold':16,'fragment_alpha_limit':16,'radius_px':radius,
            'per_component_budget_px':component_budget,
            'remaining_combined_total_budget_px':total_budget,
            'attached_components':len(proposed),'attached_pixels':attached_pixels,
            'fixed_non_cascading_seeds':True,'identity_quality':'not_evaluated',
            'all_rgba_pixels_preserved':True}
    return proposed,report


def _whole_observed_cells(board,geometry,regions,owners,candidate,observed,check_canvas_edges):
    """Accept existing observed cells only when every original group stays whole."""
    if not observed:return None
    cells=observed.get('cells',{})
    if set(cells)!={member['id'] for member in board['members']}:return None
    height,width=geometry.shape
    rectangles=list(cells.values())
    for rectangle in rectangles:
        if len(rectangle)!=4 or any(type(value) is not int for value in rectangle):return None
        a,b,c,d=rectangle
        if not (0<=a<c<=width and 0<=b<d<=height):return None
        if not np.any(geometry[b:d,a:c]>1):return None
    if sum((c-a)*(d-b) for a,b,c,d in rectangles)!=width*height:return None
    for index,(a,b,c,d) in enumerate(rectangles):
        for x0,y0,x1,y1 in rectangles[index+1:]:
            if max(a,x0)<min(c,x1) and max(b,y0)<min(d,y1):return None
    if check_canvas_edges and (np.any(geometry[:,0]>1) or np.any(geometry[:,-1]>1)
                               or np.any(geometry[0,:]>1) or np.any(geometry[-1,:]>1)):
        return None
    for fragment in candidate.get('reference_supported_fragments',[]):
        a,b,c,d=fragment['box']
        x0,y0,x1,y1=cells[fragment['owner']]
        if not (x0<=a and y0<=b and c<=x1 and d<=y1):return None
    for region in regions:
        a,b,c,d=region['box']
        containing=[identity for identity,(x0,y0,x1,y1) in cells.items()
                    if x0<=a and y0<=b and c<=x1 and d<=y1]
        if len(containing)!=1:return None
        if owners.get(region['id']) and owners[region['id']]!={containing[0]}:return None
    return {identity:list(rectangle) for identity,rectangle in cells.items()}


def _recover_reference_fragments(board,pixels,context,check_canvas_edges,actual_anchor_support=False,
                                 observed_mapping=None):
    labels=context['labels'];regions=context['regions'];owners=context['owners']
    reference=context['reference']
    members=board['members']
    if any(len(identities)>1 for identities in owners.values()):return None
    strong=set(int(label) for label in np.unique(labels[reference&(pixels>8)]) if label)
    anchors={next(iter(identities)) for label,identities in owners.items()
             if len(identities)==1 and label in strong}
    if anchors!={member['id'] for member in members}:return None
    height,width=pixels.shape
    grid=board['grid']
    cell_width=grid['cell_size'][0]*width/board['size'][0]
    cell_height=grid['cell_size'][1]*height/board['size'][1]
    component_budget=max(8,math.ceil(cell_width*cell_height/1024))
    total_budget=max(64,math.ceil(width*height/1024))
    areas=np.bincount(labels.ravel())
    ignored=[];maxima={}
    for region in regions:
        label=region['id']
        if owners.get(label):continue
        a,b,c,d=region['box']
        maximum=int(pixels[b:d,a:c][labels[b:d,a:c]==label].max())
        if maximum>3:continue
        if areas[label]>component_budget:return None
        ignored.append(label);maxima[label]=maximum
    ignored_pixels=sum(int(areas[label]) for label in ignored)
    if ignored_pixels>total_budget:return None
    geometry=pixels.copy()
    if ignored:geometry[np.isin(labels,ignored)]=0
    radius=max(2,math.ceil(min(cell_width,cell_height)/28))
    candidate=_resolve_cells(board,geometry,check_canvas_edges=check_canvas_edges,
                             reference_alpha=reference,native_geometry=True,
                              reference_fragment_radius=radius,
                              actual_anchor_pixel_budget=(total_budget-ignored_pixels
                                                          if actual_anchor_support else None))
    if candidate['unsafe_members'] or candidate.get('reference_fragments_unresolved'):return None
    if not ignored and not candidate.get('reference_supported_fragments'):return None
    for fragment in candidate.get('reference_supported_fragments',[]):
        a,b,c,d=fragment['box']
        x0,y0,x1,y1=candidate['cells'][fragment['owner']]
        if not (x0<=a and y0<=b and c<=x1 and d<=y1):return None
    # Keep every original alpha>1 component complete, with its original owner.
    crossing_ignored=[]
    for region in regions:
        a,b,c,d=region['box']
        containing=[identity for identity,(x0,y0,x1,y1) in candidate['cells'].items()
                    if x0<=a and y0<=b and c<=x1 and d<=y1]
        if len(containing)!=1:
            if containing or owners.get(region['id']) or region['id'] not in ignored:return None
            crossing_ignored.append(region)
            continue
        if owners.get(region['id']) and owners[region['id']]!={containing[0]}:return None
    observed_cut_recovery=None
    if crossing_ignored:
        observed_cells=_whole_observed_cells(board,geometry,regions,owners,candidate,
                                              observed_mapping,check_canvas_edges)
        if observed_cells is None:return None
        previous_cells=candidate['cells']
        candidate['cells']=observed_cells
        candidate['status']='adjusted'
        candidate['column_row_cuts']={column:list(cuts) for column,cuts
                                      in observed_mapping.get('column_row_cuts',{}).items()}
        observed_cut_recovery={'policy':'complete-original-groups-existing-observed-cuts/1',
            'previous_cells':previous_cells,'observed_cells':observed_cells,
            'crossing_ignored_components':len(crossing_ignored),
            'crossing_ignored_pixels':sum(int(areas[region['id']]) for region in crossing_ignored),
            'crossing_ignored_max_alpha':max(maxima[region['id']] for region in crossing_ignored),
            'all_original_core_components_unsplit':True,'all_owners_preserved':True,
            'identity_quality':'not_evaluated','all_rgba_pixels_preserved':True}
        candidate['warnings'].append({'code':'native_complete_observed_cut_recovered',**observed_cut_recovery})
    boxes={}
    for identity,(a,b,c,d) in candidate['cells'].items():
        yy,xx=np.nonzero(geometry[b:d,a:c]>1)
        if not len(xx):return None
        boxes[identity]=[a+int(xx.min()),b+int(yy.min()),a+int(xx.max())+1,b+int(yy.max())+1]
    report={'policy':'reference-supported-native-fragments/1',
            'ignored_geometry_components':len(ignored),'ignored_geometry_pixels':ignored_pixels,
            'ignored_alpha_limit':3,'max_ignored_alpha':max(maxima.values(),default=0),
            'max_ignored_component_pixels':max((int(areas[label]) for label in ignored),default=0),
            'per_component_budget_px':component_budget,'total_budget_px':total_budget,
            'budget_basis':'one_1024th_scaled_cell_area_and_one_1024th_returned_canvas_area_with_original_minima',
            'reference_radius_px':radius,'reference_fragment_alpha_limit':8,
            'attached_fragments':candidate.pop('reference_supported_fragments',[]),
            'all_original_core_components_unsplit':True,'all_rgba_pixels_preserved':True,
            'identity_quality':'not_evaluated'}
    candidate.pop('reference_fragments_unresolved',None)
    if 'actual_anchor_support' in candidate:
        report['actual_anchor_support']=candidate.pop('actual_anchor_support')
    if observed_cut_recovery:report['observed_cut_recovery']=observed_cut_recovery
    candidate['reference_fragment_recovery']=report
    candidate['member_geometry_boxes']=boxes
    candidate['warnings'].append({'code':'reference_supported_native_fragments_recovered',**report})
    return candidate


def _restore_partial_members(board, context, strict, candidate):
    """Apply validated recovery only to failed cells with their original bounds."""
    unsafe = set(strict['unsafe_members'])
    safe = [member['id'] for member in board['members'] if member['id'] not in unsafe]
    reserved = [strict['cells'][identity] for identity in safe]
    groups = [(region['box'], context['owners'].get(region['id'], set()))
              for region in context['regions']]
    recovery = candidate.get('reference_fragment_recovery', {})
    groups.extend((fragment['box'], {fragment['owner']})
                  for fragment in recovery.get('attached_fragments', []))
    recovered = []
    geometry_boxes = {}
    for identity in strict['unsafe_members']:
        rectangle = candidate['cells'].get(identity)
        core = candidate.get('member_geometry_boxes', {}).get(identity)
        if rectangle != strict['cells'][identity] or core is None:
            continue
        a, b, c, d = rectangle
        if not (a < c and b < d and a <= core[0] < core[2] <= c
                and b <= core[1] < core[3] <= d):
            continue
        if any(max(a, x0) < min(c, x1) and max(b, y0) < min(d, y1)
               for x0, y0, x1, y1 in reserved):
            continue
        valid = True
        for (x0, y0, x1, y1), owners in groups:
            intersects = max(a, x0) < min(c, x1) and max(b, y0) < min(d, y1)
            if not intersects:
                continue
            if not (a <= x0 and b <= y0 and x1 <= c and y1 <= d):
                valid = False
                break
            if owners and owners != {identity}:
                valid = False
                break
        if not valid:
            continue
        recovered.append(identity)
        geometry_boxes[identity] = list(core)
        reserved.append(rectangle)
    if not recovered:
        return strict
    remaining = [identity for identity in strict['unsafe_members'] if identity not in geometry_boxes]
    report = {
        'policy': 'preserve-accepted-assets-partial-native-recovery/1',
        'recovered_members': recovered,
        'remaining_unsafe_members': remaining,
        'preserved_safe_members': safe,
        'original_cell_bounds_preserved': True,
        'recovered_member_core_components_unsplit': True,
        'all_owners_preserved': True,
        'identity_quality': 'not_evaluated',
        'all_rgba_pixels_preserved': True,
    }
    result = dict(strict)
    result['status'] = 'ambiguous' if remaining else candidate['status']
    result['unsafe_members'] = remaining
    result['member_geometry_boxes'] = {**strict.get('member_geometry_boxes', {}), **geometry_boxes}
    result['preserved_asset_statuses'] = {identity: strict['status'] for identity in safe}
    result['reference_fragment_recovery'] = recovery
    result['partial_member_recovery'] = report
    result['warnings'] = [*strict['warnings'], {'code': 'partial_native_members_recovered', **report}]
    return result


def _fixed_grid_partitions(board, actual_size):
    """Partition complete planned cells and retain unused space separately."""
    width, height = actual_size
    reference_width, reference_height = board['size']
    grid = board['grid']
    rows, columns = grid['rows'], grid['columns']
    margin, gap = grid['margin'], grid['gap']
    cell_width, cell_height = grid['cell_size']
    right = margin + columns * cell_width + (columns - 1) * gap
    bottom = margin + rows * cell_height + (rows - 1) * gap
    if right > reference_width - margin or bottom > reference_height - margin:
        return None
    member_positions = {}
    for member in board['members']:
        x, y, member_width, member_height = member['slot']
        column = round((x - margin) / (cell_width + gap))
        row = round((y - margin) / (cell_height + gap))
        expected = [margin + column * (cell_width + gap),
                    margin + row * (cell_height + gap), cell_width, cell_height]
        if (not (0 <= row < rows and 0 <= column < columns)
                or any(abs(value - wanted) > 1e-6
                       for value, wanted in zip(member['slot'], expected))
                or (row, column) in member_positions):
            return None
        member_positions[row, column] = member['id']
    xs = [round(margin * width / reference_width)]
    xs.extend(round((margin + column * (cell_width + gap) - gap / 2)
                    * width / reference_width) for column in range(1, columns))
    xs.append(round(right * width / reference_width))
    ys = [round(margin * height / reference_height)]
    ys.extend(round((margin + row * (cell_height + gap) - gap / 2)
                    * height / reference_height) for row in range(1, rows))
    ys.append(round(bottom * height / reference_height))
    if (not (0 < xs[0] < xs[-1] < width and 0 < ys[0] < ys[-1] < height)
            or any(a >= b for a, b in zip(xs, xs[1:]))
            or any(a >= b for a, b in zip(ys, ys[1:]))):
        return None
    partitions = []
    cells = {}
    for row in range(rows):
        for column in range(columns):
            box = [xs[column], ys[row], xs[column + 1], ys[row + 1]]
            identity = member_positions.get((row, column))
            if identity is not None:
                cells[identity] = box
            partitions.append({'kind': 'member' if identity is not None else 'unused_cell',
                               'member_id': identity, 'box': box})
    for name, box in [
        ('outside_top', [0, 0, width, ys[0]]),
        ('outside_bottom', [0, ys[-1], width, height]),
        ('outside_left', [0, ys[0], xs[0], ys[-1]]),
        ('outside_right', [xs[-1], ys[0], width, ys[-1]]),
    ]:
        partitions.append({'kind': name, 'member_id': None, 'box': box})
    return cells, partitions, xs, ys


def _recover_fixed_native_grid(board, pixels, reference_alpha, context, strict):
    """Cut only faint grid dividers after whole strong cores match real reference."""
    if (board.get('background_mode') != MODE or reference_alpha is None
            or len(strict['unsafe_members']) != len(board['members'])):
        return None
    multi_owner_components = sum(len(owners) > 1 for owners in context['owners'].values())
    if multi_owner_components:
        return None
    height, width = pixels.shape
    partition = _fixed_grid_partitions(board, (width, height))
    if partition is None:
        return None
    cells, parts, xs, ys = partition
    given = np.asarray(reference_alpha)
    reference_levels = np.where(given > 16, 255, np.where(given > 0, 1, 0)).astype(np.uint8)
    reference = np.asarray(Image.fromarray(reference_levels).resize(
        (width, height), Image.Resampling.NEAREST))
    if not np.any(reference > 16):
        return None
    dividers = []
    for axis, cuts in [('x', xs), ('y', ys)]:
        for cut in cuts:
            if axis == 'x':
                band = pixels[:, cut - 1:cut + 1]
                reference_band = reference[:, cut - 1:cut + 1]
            else:
                band = pixels[cut - 1:cut + 1, :]
                reference_band = reference[cut - 1:cut + 1, :]
            maximum = int(band.max())
            nonzero = int(np.count_nonzero(band))
            if (not nonzero or maximum > _WEAK_UNCONFIRMED_MAX_ALPHA
                    or np.any(reference_band)):
                return None
            dividers.append({'axis': axis, 'cut': cut,
                             'nonzero_alpha_pixels': nonzero,
                             'max_alpha_adjacent_2px': maximum})
    partition_labels = np.full(pixels.shape, -1, dtype=np.int32)
    member_partitions = {}
    background = []
    covered = 0
    for index, part in enumerate(parts):
        x0, y0, x1, y1 = part['box']
        if np.any(partition_labels[y0:y1, x0:x1] != -1):
            return None
        partition_labels[y0:y1, x0:x1] = index
        covered += (x1 - x0) * (y1 - y0)
        identity = part['member_id']
        if identity is not None:
            member_partitions[identity] = index
        else:
            if (np.any(pixels[y0:y1, x0:x1] > 16)
                    or np.any(reference[y0:y1, x0:x1] > 16)):
                return None
            background.append({**part, 'max_alpha': int(pixels[y0:y1, x0:x1].max())})
    if covered != width * height or np.any(partition_labels < 0):
        return None
    strong_context = {}
    _resolve_cells(board, np.where(pixels > 16, pixels, 0), native_geometry=True,
                   reference_alpha=np.where(reference > 16, reference, 0),
                   context=strong_context)
    if not strong_context:
        return None
    strong_labels = strong_context['labels']
    boxes = {}
    for region in strong_context['regions']:
        owners = strong_context['owners'].get(region['id'], set())
        if len(owners) != 1:
            return None
        identity = next(iter(owners))
        if identity not in cells:
            return None
        x0, y0, x1, y1 = cells[identity]
        left, top, right, bottom = region['box']
        if not (x0 <= left < right <= x1 and y0 <= top < bottom <= y1):
            return None
        labels = partition_labels[top:bottom, left:right][
            strong_labels[top:bottom, left:right] == region['id']]
        if not np.all(labels == member_partitions[identity]):
            return None
        previous = boxes.get(identity, region['box'])
        boxes[identity] = [min(previous[0], left), min(previous[1], top),
                           max(previous[2], right), max(previous[3], bottom)]
    if set(boxes) != set(cells):
        return None
    split_components = 0
    for region in context['regions']:
        left, top, right, bottom = region['box']
        touched = partition_labels[top:bottom, left:right][
            context['labels'][top:bottom, left:right] == region['id']]
        if len(np.unique(touched)) > 1:
            split_components += 1
    report = {
        'policy': FIXED_GRID_POLICY,
        'identity_quality': 'not_evaluated',
        'strong_alpha_threshold_for_geometry_only': 16,
        'divider_alpha_limit': _WEAK_UNCONFIRMED_MAX_ALPHA,
        'divider_adjacent_pixels': 2,
        'dividers': dividers,
        'all_strong_components_unique_and_unsplit': True,
        'strong_components': len(strong_context['regions']),
        'original_multi_owner_components': multi_owner_components,
        'background_field_policy': 'nonzero-alpha-at-every-planned-divider/1',
        'split_alpha_above1_components': split_components,
        'original_alpha_above1_components_unsplit': split_components == 0,
        'complete_cell_rgba_preserved': True,
        'background_partitions': background,
        'coverage': {'canvas_pixels': width * height, 'partition_pixels': covered,
                     'each_pixel_exactly_once': True},
    }
    return {
        **strict, 'status': 'adjusted', 'cells': cells, 'unsafe_members': [],
        'geometry_policy': FIXED_GRID_POLICY,
        'alpha_threshold_for_geometry_only': 16,
        'member_geometry_boxes': boxes, 'fixed_grid_recovery': report,
        'warnings': [*strict['warnings'], {'code': 'native_fixed_grid_recovered', **report}],
    }


def resolve_cells(board: dict, alpha: np.ndarray, *, check_canvas_edges: bool = True,
                  reference_alpha: np.ndarray | None = None,
                  native_geometry: bool = False) -> dict:
    """Keep accepted cells while recovering only fully supported native members."""
    context={}
    strict=_resolve_cells(board,alpha,check_canvas_edges=check_canvas_edges,
                           reference_alpha=reference_alpha,native_geometry=native_geometry,
                           context=context)
    if (not native_geometry or strict.get('geometry_policy')!='native-core-local-rows/1'
            or not strict['unsafe_members'] or not context):
        return strict
    recovered=_recover_reference_fragments(board,np.asarray(alpha),context,check_canvas_edges,
                                           observed_mapping=strict)
    if recovered is None:
        recovered=_recover_reference_fragments(board,np.asarray(alpha),context,check_canvas_edges,
                                               actual_anchor_support=True,observed_mapping=strict)
    if recovered is None:
        recovered = _recover_fixed_native_grid(board, np.asarray(alpha), reference_alpha,
                                               context, strict)
        return strict if recovered is None else recovered
    if len(strict['unsafe_members']) == len(board['members']):
        return recovered
    return _restore_partial_members(board, context, strict, recovered)
