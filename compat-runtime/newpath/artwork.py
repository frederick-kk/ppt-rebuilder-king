"""Build pixel-budgeted reference boards and extract each returned visual.

Board geometry and alpha interpretation are separate strategies. Legacy boards
consult source colours for matte risk; native boards use provider RGBA directly.
Every drawn reference pixel comes from the newly generated full foreground.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import numpy as np
from PIL import Image
from .boardplan import plan_boards
from .cellmap import resolve_cells
from .matte import choose_key, collision_mask, edge_background_mask, erase_matte, estimate_background, select_subject, _regions
from .native import MODE, validate_native_output
from .wordart import _check_placement_budget

_KEY_RGB = {'magenta':(255,0,255),'green':(0,255,0),'cyan':(0,255,255),'yellow':(255,255,0)}
_REFERENCE_POLICY = 'whole-foreground-alpha-with-collision-notes/3'


def _open_png(path):
    path=Path(path)
    try:
        with Image.open(path) as image:
            if image.format!='PNG' or min(image.size)<1:
                raise ValueError(f'Expected a nonempty PNG: {path}')
            image.load()
            return image.convert('RGBA')
    except (OSError,SyntaxError,Image.DecompressionBombError) as error:
        raise ValueError(f'Cannot decode PNG: {path}') from error


def _inspect(path,key=None):
    image=_open_png(path)
    raw=np.array(image)
    visible=int(np.count_nonzero(raw[...,3]))
    if not visible:
        raise ValueError(f'Image is completely transparent: {path}')
    info={'path':str(Path(path).resolve()),'format':'PNG','size':list(image.size),
          'visible_pixels':visible,'warnings':[]}
    clean=raw
    if key is not None:
        clean=erase_matte(raw,key)
        if not np.any(clean[...,3]):
            raise ValueError(f'Image contains only the selected opaque background: {path}')
        removed=int(np.count_nonzero((raw[...,3]>0)&(clean[...,3]==0)))
        fraction=removed/raw.shape[0]/raw.shape[1]
        info.update(key=key,key_pixels=removed,key_fraction=fraction,
                    key_like_fraction=float(np.count_nonzero(collision_mask(raw,key)) / raw.shape[0] / raw.shape[1]))
        if fraction<.02:
            info['warnings'].append('key_coverage_below_2_percent')
    return raw,clean,info


def validate_output(path,key=None):
    """Validate a technical image result without grading its visual fidelity."""
    return _inspect(path,key)[2]


def prepare_boards(source,foreground,arts,out,*,background_mode=None):
    if background_mode is not None and background_mode != MODE:
        raise ValueError('Unsupported material background mode')
    if not arts:
        return []
    original=_open_png(source)
    generated=_open_png(foreground)
    if original.size!=(1920,1080) or generated.size!=original.size:
        raise ValueError('Source and foreground must use the normalized 1920x1080 frame')
    if len({art['id'] for art in arts})!=len(arts):
        raise ValueError('Artwork ids must be unique')
    native=background_mode==MODE
    full_pixels=np.array(generated)
    if native:
        validate_native_output(foreground,role='foreground')
        clean_pixels=full_pixels
        rekey_pixels=full_pixels
    else:
        # Observe the matte on the complete legacy foreground, before local
        # windows can hide its perimeter or turn a sealed hole into false art.
        matte=estimate_background(full_pixels,'magenta')
        clean_pixels=erase_matte(full_pixels,'magenta',background=matte)
        # Sealed key-coloured material is indistinguishable from a transparent
        # hole when the original member contains magenta. Retain generated
        # bytes as a reference aid with an unresolved warning; never copy source
        # art or claim that the ambiguous colour has been recovered correctly.
        removed=(full_pixels[...,3]==255)&(clean_pixels[...,3]==0)
        sealed=removed&~edge_background_mask(full_pixels,'magenta',background=matte)
        rekey_pixels=clean_pixels.copy()
        rekey_pixels[sealed]=full_pixels[sealed]
    clean_foreground=Image.fromarray(clean_pixels)
    rekey_foreground=Image.fromarray(rekey_pixels)
    plans=plan_boards(arts,original.size)
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    results=[]
    for plan in plans:
        size=tuple(plan['size'])
        placements=plan['placements']
        key=None if native else choose_key([np.asarray(original.crop(p['crop'])) for p in placements])
        members=[];drawings=[];identity=[];reference_warnings=[]
        for placement in placements:
            art=placement['art'];slot=list(placement['slot'])
            inset=int(placement['inset_px'])
            inset_xy=list(placement.get('inset_xy',[inset,inset]))
            source_crop=None if native else np.asarray(original.crop(placement['crop']))
            conflict=False if native else bool(np.any(collision_mask(source_crop,'magenta')))
            cropped=(rekey_foreground if conflict and key!='magenta' else clean_foreground).crop(placement['crop'])
            if conflict:
                reference_warnings.append({'id':art['id'],'code':'source_matte_collision_unresolved'})
            if not np.any(np.asarray(cropped)[...,3]):
                reference_warnings.append({'id':art['id'],'code':'empty_member_reference'})
            ref=cropped
            scale=min((slot[2]-2*inset_xy[0])/ref.width,(slot[3]-2*inset_xy[1])/ref.height)
            rendered=[max(1,int(ref.width*scale)),max(1,int(ref.height*scale))]
            member={'id':art['id'],'slot':slot,'box':list(art['box']),
                    'target':list(art.get('target',art['box'])),
                    'description':art.get('description',''),'presentation':art.get('presentation','cutout'),
                    'inset_px':inset,'rendered_size':rendered,
                    'pixel_budget':placement.get('pixel_budget',{})}
            if 'inset_xy' in placement:member['inset_xy']=inset_xy
            for optional in ('lettering','z'):
                if optional in art:member[optional]=art[optional]
            members.append(member)
            identity.append({'id':art['id'],'slot':slot,'crop':placement['crop'],'inset_px':inset,
                             'inset_xy':inset_xy,
                             'description':member['description'],'lettering':member.get('lettering'),
                             'pixels_sha256':hashlib.sha256(cropped.tobytes()).hexdigest()})
            drawings.append((ref,slot,rendered))
        content={'policy':plan['policy'],'reference_policy':_REFERENCE_POLICY,'size':size,'key':key,
                 'grid':plan.get('grid'),'members':identity,'reference_warnings':reference_warnings}
        if native:
            content.update(background_mode=MODE,reference_policy=MODE)
        signature=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        ident='board-'+signature[:20];path=out/(ident+'.png')
        if not path.exists():
            canvas=Image.new('RGBA',size,(0,0,0,0) if native else (*_KEY_RGB[key],255))
            for ref,slot,rendered in drawings:
                x,y,w,h=slot
                resized=ref.resize(tuple(rendered),Image.Resampling.LANCZOS)
                position=(x+(w-rendered[0])//2,y+(h-rendered[1])//2)
                if native:canvas.paste(resized,position)
                else:canvas.alpha_composite(resized,position)
            canvas.save(path,format='PNG')
        board={'id':ident,'image':str(path),'size':list(size),'capacity':len(members),
                        'key':key,'members':members,'signature':signature,'planning_policy':plan['policy'],
                        'pixel_budget':plan.get('pixel_budget',{}),'reference_warnings':reference_warnings}
        if native:board['background_mode']=MODE
        if 'grid' in plan:board['grid']=plan['grid']
        results.append(board)
    return results


def _bounds(slot,reference_size,actual_size):
    x,y,w,h=slot;rw,rh=reference_size;aw,ah=actual_size
    return (max(0,math.floor(x*aw/rw)),max(0,math.floor(y*ah/rh)),
            min(aw,math.ceil((x+w)*aw/rw)),min(ah,math.ceil((y+h)*ah/rh)))


def _continues_outside(mask,whole,bounds):
    x0,y0,x1,y1=bounds;alpha=mask[...,3]>0;all_alpha=whole[...,3]>0
    return bool((x0>0 and np.any(alpha[:,0]&all_alpha[y0:y1,x0-1]))
                or (y0>0 and np.any(alpha[0]&all_alpha[y0-1,x0:x1]))
                or (x1<whole.shape[1] and np.any(alpha[:,-1]&all_alpha[y0:y1,x1]))
                or (y1<whole.shape[0] and np.any(alpha[-1]&all_alpha[y1,x0:x1])))


def _only_corner_residue_removed(slot,selected,bounds,canvas_size,key,background):
    """Recognize only unsupported isolated opaque key-colour canvas corners.

    This limits acceptance after existing selection; it does not delete pixels
    or relax the matte model for subject edges or other fragments.
    """
    if background is None:
        return False
    removed=(slot[...,3]>0)&(selected[...,3]==0)
    # Existing selection may also reject unrelated interior fragments. Only
    # components touching the physical canvas can establish canvas clipping.
    x0,y0,x1,y1=bounds;cw,ch=canvas_size
    edge=np.zeros(removed.shape,dtype=bool)
    if x0==0:edge[:,0]=True
    if x1==cw:edge[:,-1]=True
    if y0==0:edge[0,:]=True
    if y1==ch:edge[-1,:]=True
    removed &= edge
    yy,xx=np.nonzero(removed)
    if not len(xx):
        return False
    if not np.all(((x0+xx==0)|(x0+xx==cw-1))&((y0+yy==0)|(y0+yy==ch-1))):
        return False
    for y,x in zip(yy,xx):
        neighbors=slot[max(0,y-1):y+2,max(0,x-1):x+2,3]>0
        if np.count_nonzero(neighbors)!=1:
            return False
    samples=slot[removed][None,...]
    center=np.asarray(background.rgb,dtype=np.float32)
    return bool(np.all(samples[...,3]==255) and np.all(collision_mask(samples,key))
                and np.all(np.sum((samples[...,:3].astype(np.float32)-center)**2,axis=-1)<=48**2))


def _retain_interior_lettering(slot,selected,key):
    """An incomplete generated reference cannot veto interior written strokes.

    Cell identity has already passed the gutter guards. Retain whole interior
    components with non-key material, including tiny and translucent strokes;
    do not borrow source pixels or exempt any actual cell/canvas-edge content.
    """
    labels,regions=_regions(slot[...,3]>0)
    height,width=slot.shape[:2]
    restore=[]
    for region in regions:
        left,top,right,bottom=region['box']
        if not (left>0 and top>0 and right<width and bottom<height):continue
        mask=labels==region['id']
        if np.any(selected[...,3][mask]):continue
        if np.any(~collision_mask(slot[mask][None,...],key)):
            restore.append(region['id'])
    if not restore:return selected,0
    mask=np.isin(labels,restore)
    result=selected.copy();result[mask]=slot[mask]
    return result,int(np.count_nonzero(mask))


def extract_assets(board,actual_image,destination):
    """Use full scaled slots and reference occupancy; preserve target geometry."""
    if board.get('background_mode') is not None and board['background_mode'] != MODE:
        raise ValueError('Unsupported material background mode')
    native=board.get('background_mode')==MODE
    if native:
        inspection=validate_native_output(actual_image,role='board')
        raw=pixels=np.asarray(_open_png(actual_image))
    else:
        raw,pixels,inspection=_inspect(actual_image,board['key'])
    actual_h,actual_w=pixels.shape[:2]
    background=None if native else estimate_background(raw,board['key'])
    reference=None
    if board.get('image') and Path(board['image']).is_file():
        reference=np.asarray(_open_png(board['image']))
        if not native:reference=erase_matte(reference,board['key'])
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
    assets=[];warnings=[{'board':board['id'],'code':c} for c in inspection['warnings']]
    # Keep identity/gutter guards here. A detached matte residue at a canvas
    # corner is not evidence that the reference-supported subject is clipped.
    mapping=resolve_cells(board,pixels[...,3],check_canvas_edges=native,
                          reference_alpha=None if reference is None else reference[...,3],
                          native_geometry=native)
    warnings.extend({'board':board['id'],**note} for note in mapping['warnings'])
    unsafe=set(mapping['unsafe_members'])
    for member in board['members']:
        def warn(code):warnings.append({'board':board['id'],'id':member['id'],'code':code})
        if member['id'] in unsafe:
            warn('ambiguous_member_omitted')
            continue
        bounds=tuple(mapping['cells'][member['id']])
        x0,y0,x1,y1=bounds
        if x1<=x0 or y1<=y0:
            warn('unreliable_slot_bounds');continue
        slot=pixels[y0:y1,x0:x1].copy()
        if not np.any(slot[...,3]):
            warn('empty_slot');continue
        ref=None
        if reference is not None:
            if board.get('grid'):
                rw,rh=reference.shape[1],reference.shape[0]
                rx0,ry0=max(0,math.floor(x0*rw/actual_w)),max(0,math.floor(y0*rh/actual_h))
                rx1,ry1=min(rw,math.ceil(x1*rw/actual_w)),min(rh,math.ceil(y1*rh/actual_h))
            else:
                rx0,ry0,rx1,ry1=_bounds(member['slot'],board['size'],(reference.shape[1],reference.shape[0]))
            ref=reference[ry0:ry1,rx0:rx1]
        if native:selected,notes=slot,[]
        else:selected,notes=select_subject(slot,member['box'][2]/member['box'][3],reference=ref)
        for note in notes:warn(note)
        recovered_lettering=0
        if not native and board.get('grid') and member.get('lettering'):
            selected,recovered_lettering=_retain_interior_lettering(slot,selected,board['key'])
            if recovered_lettering:warn('unconfirmed_lettering_fragments_retained')
        if not native and board.get('grid'):
            reference_supported=(ref is not None and np.any(ref[...,3])
                                 and 'reference_support_mismatch' not in notes
                                 and 'empty_reference_support' not in notes)
            # A partial reference match cannot prove arbitrary discarded
            # fragments harmless. Exempt only the observed isolated key-colour
            # corner residues; all other cases retain the raw-alpha guard.
            corner_residue=(reference_supported and _only_corner_residue_removed(
                slot,selected,bounds,(actual_w,actual_h),board['key'],background))
            edge_pixels=selected if corner_residue else slot
            occupied=edge_pixels[...,3]>0
            if ((x0==0 and np.any(occupied[:,0])) or (x1==actual_w and np.any(occupied[:,-1]))
                    or (y0==0 and np.any(occupied[0,:])) or (y1==actual_h and np.any(occupied[-1,:]))):
                note={'id':member['id'],'code':'subject_clipped_at_canvas_edge'}
                mapping['warnings'].append(note)
                mapping['unsafe_members']=sorted(set(mapping['unsafe_members'])|{member['id']})
                mapping['status']='ambiguous'
                warn(note['code']);warn('ambiguous_member_omitted')
                continue
        yy,xx=np.nonzero(selected[...,3])
        if not len(xx):
            warn('empty_selected_slot');continue
        left,top,right,bottom=int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1
        sh,sw=slot.shape[:2]
        if left==0 or top==0 or right==sw or bottom==sh:warn('touch_slot_edge')
        insets=member.get('inset_xy',[member.get('inset_px',0)]*2)
        ix=insets[0]*actual_w/board['size'][0];iy=insets[1]*actual_h/board['size'][1]
        if left<ix or top<iy or right>sw-ix or bottom>sh-iy:warn('safe_area_cross')
        if _continues_outside(selected,pixels,bounds):warn('cross_slot')
        budget=member.get('pixel_budget',{})
        if budget and not budget.get('meets_requirement',True):warn('planned_pixel_budget_shortfall')
        returned_budget={'status':'unknown'}
        if budget.get('required_content_px') and member.get('rendered_size'):
            available=[math.floor(member['rendered_size'][0]*actual_w/board['size'][0]),
                       math.floor(member['rendered_size'][1]*actual_h/board['size'][1])]
            wanted=list(budget['required_content_px'])
            enough=all(got>=need for got,need in zip(available,wanted))
            returned_budget={'status':'geometry_estimate','available_content_px':available,
                             'required_content_px':wanted,'meets_requirement':enough,
                             'assumption':'The generated subject follows its assigned reference placement.'}
            if not enough:warn('returned_pixel_budget_shortfall')
        crop=selected[top:bottom,left:right]
        safe=re.sub(r'[^A-Za-z0-9_-]+','-',str(member['id'])).strip('-')[:60] or 'art'
        suffix=hashlib.sha256(str(member['id']).encode()).hexdigest()[:10]
        path=destination/(safe+'-'+suffix+'.png')
        natural=[crop.shape[1],crop.shape[0]];target=list(member.get('target',member['box']))
        asset={'id':member['id'],'path':str(path),'target':target,'source_size':natural,
               'crop_box':[left,top,right,bottom],'actual_slot':[x0,y0,x1-x0,y1-y0],
               'slot_size':[sw,sh],'stretch':[target[2]/natural[0],target[3]/natural[1]],
               'extraction_policy':MODE if native else 'observed-gutters-v1' if board.get('grid') else 'reference-occupancy-v1',
               'planned_slot':list(member['slot']),'cell_mapping_status':mapping['status'],
               'returned_pixel_budget':returned_budget}
        if 'z' in member:asset['z']=member['z']
        if native:asset['background_mode']=MODE
        if native and mapping.get('geometry_policy')=='native-core-local-rows/1':
            # Fit the visible subject to the same authored target as before,
            # while keeping the complete faint RGBA frame with its true offset.
            cy,cx=np.nonzero(selected[...,3]>1)
            core=[int(cx.min()),int(cy.min()),int(cx.max())+1,int(cy.max())+1]
            sx,sy=target[2]/(core[2]-core[0]),target[3]/(core[3]-core[1])
            picture=[target[0]+(left-core[0])*sx,target[1]+(top-core[1])*sy,
                     natural[0]*sx,natural[1]*sy]
            try:_check_placement_budget(picture)
            except ValueError as error:
                raise ValueError('Native material placement exceeds the four-page safety budget. Preserve the original PNG.') from error
            asset.update(authored_target=list(target),target=picture,stretch=[sx,sy],
                         placement={'policy':'preserve-native-alpha-authored-stretch/1',
                                    'core_bbox':core,'visible_core_target':list(target),
                                    'alpha_threshold_for_geometry_only':1,
                                    'all_rgba_pixels_preserved':True})
        if recovered_lettering:asset['lettering_recovered_pixels']=recovered_lettering
        Image.fromarray(crop).save(path,format='PNG')
        assets.append(asset)
    # Deferred checks on later members may have changed the overall status.
    for asset in assets:asset['cell_mapping_status']=mapping['status']
    return {'assets':assets,'warnings':warnings,'cell_mapping':mapping}
