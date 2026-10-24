"""Explicit current-picture preparation and deterministic coordinate assistance."""
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .ruler import render_coordinate_guide
from .storage import sha256


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


def normalize_layer(path, role, transform, output):
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
