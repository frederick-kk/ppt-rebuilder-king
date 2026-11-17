"""Explicit current-picture preparation and deterministic coordinate assistance."""
import hashlib
import io
import math
from pathlib import Path
import uuid

from PIL import Image, ImageDraw, ImageOps

from .ruler import render_coordinate_guide
from .storage import sha256, relative_file, fingerprint
from .layout import _box, _observed_box
from .native import MODE, validate_native_output


TITLE_REFERENCE_POLICY = 'source-parts-title-reference/1'


def decoded_picture(path):
    with Image.open(path) as image:
        layer = ImageOps.exif_transpose(image).convert('RGBA')
        white = Image.new('RGBA', layer.size, 'white')
        white.alpha_composite(layer)
        return white.convert('RGB')


def pixel_identity(path):
    picture = decoded_picture(path)
    h = hashlib.sha256(str(picture.size).encode('ascii') + picture.tobytes())
    return h.hexdigest()


def prepare_source(path, work):
    source, work = Path(path).resolve(), Path(work).resolve()
    folder = work / 'input'; folder.mkdir(exist_ok=True)
    original = folder / ('original' + (source.suffix.lower() or '.bin'))
    before = sha256(source)
    if original.exists():
        if sha256(original)!=before:raise ValueError('The saved original differs from the initialization source.')
    else:
        temporary=folder/'original.copying'
        temporary.write_bytes(source.read_bytes())
        if sha256(temporary)!=before:raise ValueError('The source changed while being copied.')
        temporary.replace(original)
    if before != sha256(original) or before != sha256(source):
        raise ValueError('The source changed while being copied.')
    picture = decoded_picture(original)
    scale = min(1920 / picture.width, 1080 / picture.height)
    size = [max(1,round(picture.width * scale)), max(1,round(picture.height * scale))]
    offset = [(1920 - size[0]) // 2, (1080 - size[1]) // 2]
    working = Image.new('RGB', (1920, 1080), 'white')
    working.paste(picture.resize(size, Image.Resampling.LANCZOS), tuple(offset))
    working.save(folder / 'working.png')
    render_coordinate_guide(working).save(folder / 'ruler.png')
    return {'input_path':str(source), 'original':original.relative_to(work).as_posix(),
        'byte_sha256':before, 'pixel_sha256':pixel_identity(original),
        'working':'input/working.png', 'working_sha256':sha256(folder/'working.png'),
        'guide':'input/ruler.png', 'guide_sha256':sha256(folder/'ruler.png'),
        'transform':{'scale':scale,'original_size':list(picture.size),'resized':size,
            'offset':offset,'padding':[offset[0],offset[1],1920-offset[0]-size[0],1080-offset[1]-size[1]]}}


def _working_picture(work, source):
    path = relative_file(work, source['working'])
    if sha256(path) != source['working_sha256']:
        raise ValueError('The working source changed before preparing its reading references.')
    with Image.open(path) as opened:
        if opened.format != 'PNG' or opened.size != (1920, 1080):
            raise ValueError('Reading references require the saved 1920x1080 working source.')
        opened.load()
        return opened.copy()


def _save_source_picture(work, name, image):
    path = relative_file(work, 'input/' + name)
    if path.exists():
        with Image.open(path) as opened:
            same = (opened.format == 'PNG' and opened.size == image.size
                    and opened.convert('RGBA').tobytes() == image.convert('RGBA').tobytes())
        if not same:
            raise ValueError('A saved source reading crop has different pixels: ' + name)
    else:
        encoded = io.BytesIO()
        image.save(encoded, format='PNG')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.preparing')
        try:
            temporary.write_bytes(encoded.getvalue())
            if path.exists():
                return _save_source_picture(work, name, image)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'path': path.relative_to(work).as_posix(), 'sha256': sha256(path)}


def prepare_reading_images(work, source):
    """Four overlapping, reversible two-times pixel views of the working source."""
    work = Path(work).resolve()
    picture = _working_picture(work, source)
    boxes = [(0, 0, 1056, 594), (864, 0, 1920, 594),
             (0, 486, 1056, 1080), (864, 486, 1920, 1080)]
    names = ('top-left', 'top-right', 'bottom-left', 'bottom-right')
    result = []
    for name, box in zip(names, boxes):
        crop = picture.crop(box)
        enlarged = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.NEAREST)
        saved = _save_source_picture(work, 'reading-' + name + '.png', enlarged)
        result.append({**saved, 'working_box': list(box), 'zoom': 2,
                       'source_sha256': source['working_sha256']})
    return result


def prepare_wordart_source_crop(work, source, member, *, reference_policy=None):
    """Keep real source pixels around a complete written-artwork source box."""
    if reference_policy not in (None, TITLE_REFERENCE_POLICY):
        raise ValueError('Unsupported title source reference policy')
    work = Path(work).resolve()
    picture = _working_picture(work, source)
    member_box = _box(member.get('box'), 'wordart source box')
    x, y, width, height = member_box
    margin = math.ceil(max(16, .10 * max(width, height)))
    box = [max(0, math.floor(x - margin)), max(0, math.floor(y - margin)),
           min(1920, math.ceil(x + width + margin)), min(1080, math.ceil(y + height + margin))]
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError('The wordart source crop has no pixels inside the working source.')
    crop = picture.crop(box)
    identity = [source['working_sha256'], member_box]
    metadata = {}
    if reference_policy == TITLE_REFERENCE_POLICY:
        if 'source_parts' in member:
            parts = _observed_box({'source_parts': member['source_parts']},
                                  'wordart source')['source_parts']
        else:
            parts = [{'name': 'member-box', 'bounds': [x, y, x + width, y + height]}]
        mask = Image.new('L', crop.size, 0)
        draw = ImageDraw.Draw(mask)
        for part in parts:
            left, top, right, bottom = part['bounds']
            left = max(box[0], math.floor(left))
            top = max(box[1], math.floor(top))
            right = min(box[2], math.ceil(right))
            bottom = min(box[3], math.ceil(bottom))
            if left < right and top < bottom:
                draw.rectangle((left - box[0], top - box[1],
                                right - box[0] - 1, bottom - box[1] - 1), fill=255)
        isolated = Image.new('RGBA', crop.size, (0, 0, 0, 0))
        isolated.paste(crop.convert('RGBA'), (0, 0), mask)
        crop = isolated
        identity.extend([reference_policy, parts])
        metadata = {
            'reference_policy': reference_policy,
            'source_parts': parts,
            'reference_provenance': {
                'kind': 'masked-working-source', 'retained_rgba_unchanged': True,
                'outside_parts_rgba_zero': True, 'generated': False,
            },
        }
    name = 'wordart-source-' + fingerprint(identity)[:20] + '.png'
    saved = _save_source_picture(work, name, crop)
    return {**saved, 'crop_box': box, 'member_box': member_box, 'zoom': 1,
            'source_sha256': source['working_sha256'], **metadata}


def normalize_layer(path, role, transform, output, *, background_mode=None):
    if background_mode is not None and background_mode != MODE:
        raise ValueError('Unsupported material background mode')
    if role == 'foreground' and background_mode == MODE:
        validate_native_output(path, role=role)
        with Image.open(path) as opened:
            canvas = opened.convert('RGBA').resize((1920,1080), Image.Resampling.LANCZOS)
        x,y=transform['offset']; w,h=transform['resized']
        draw=ImageDraw.Draw(canvas)
        if x: draw.rectangle((0,0,x-1,1079),fill=(0,0,0,0))
        if y: draw.rectangle((0,0,1919,y-1),fill=(0,0,0,0))
        if x+w<1920: draw.rectangle((x+w,0,1919,1079),fill=(0,0,0,0))
        if y+h<1080: draw.rectangle((0,y+h,1919,1079),fill=(0,0,0,0))
        Path(output).parent.mkdir(parents=True,exist_ok=True)
        canvas.save(output)
        return
    matte = (255,255,255,255) if role == 'background' else (255,0,255,255)
    with Image.open(path) as opened:
        image = opened.convert('RGBA').resize((1920,1080), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA',(1920,1080),matte)
    canvas.alpha_composite(image)
    x,y=transform['offset']; w,h=transform['resized']
    draw=ImageDraw.Draw(canvas)
    if x: draw.rectangle((0,0,x-1,1079),fill=matte)
    if y: draw.rectangle((0,0,1919,y-1),fill=matte)
    if x+w<1920: draw.rectangle((x+w,0,1919,1079),fill=matte)
    if y+h<1080: draw.rectangle((0,y+h,1919,1079),fill=matte)
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    canvas.save(output)
