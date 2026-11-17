"""Convert a declared, observed solid colour field into derived native alpha."""
from io import BytesIO
import os
from pathlib import Path
import uuid

import numpy as np
from PIL import Image

from .matte import choose_key, erase_matte, estimate_background
from .native import validate_native_output
from .storage import sha256


POLICY = 'keyed-materials/1'
_KEY_RGB = {'magenta': (255, 0, 255), 'green': (0, 255, 0),
            'cyan': (0, 255, 255), 'yellow': (255, 255, 0)}


def rgb_for_key(key):
    if not isinstance(key, str) or key not in _KEY_RGB:
        raise ValueError('Unsupported material key colour')
    return _KEY_RGB[key]


def _open_pixels(path):
    try:
        with Image.open(path) as image:
            if image.format != 'PNG' or min(image.size) < 1:
                raise ValueError('Keyed materials require a nonempty PNG')
            image.load()
            return np.array(image.convert('RGBA'))
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('Cannot decode keyed material PNG') from error


def choose_material_key(reference_path):
    """Use visible reference colours to select the existing least-conflicting key."""
    return choose_key([_open_pixels(reference_path)])


def prepare_material(path, output, key):
    """Preserve actual input bytes and publish a separate validated alpha file.

    Native alpha is retained unless the explicit key field is observed on its
    opaque perimeter. Colour conversion clears only matching opaque pixels. It does not infer
    semantic subject masks, repair missing content, or remove white material.
    """
    rgb_for_key(key)
    source = Path(path).resolve()
    output = Path(output).resolve()
    if source == output or (output.exists() and os.path.samefile(source, output)):
        raise ValueError('A derived material must not replace the actual return')
    pixels = _open_pixels(source)
    source_sha = sha256(source)
    try:
        validation = validate_native_output(source)
    except ValueError:
        validation = None
    removed = 0
    matte = estimate_background(pixels, key)
    if validation is not None and matte is None:
        data = source.read_bytes()
        conversion = 'native-pass-through'
    else:
        if matte is None:
            raise ValueError('The requested solid key colour is not established on the returned perimeter')
        clean = erase_matte(pixels, key, background=matte)
        removed = int(np.count_nonzero((pixels[..., 3] > 0) & (clean[..., 3] == 0)))
        if not removed or not np.any(clean[..., 3]):
            raise ValueError('Key conversion has no nonempty separated subject')
        buffer = BytesIO()
        Image.fromarray(clean).save(buffer, format='PNG')
        data = buffer.getvalue()
        conversion = 'keyed-to-alpha'
    if sha256(source) != source_sha:
        raise ValueError('The actual material return changed during conversion')
    if output.exists():
        if output.read_bytes() != data:
            raise ValueError('An existing derived material is immutable')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    validation = validate_native_output(output)
    return {'policy': POLICY, 'path': str(output), 'sha256': sha256(output),
            'source_path': str(source), 'source_sha256': source_sha,
            'key': key, 'conversion': conversion, 'removed_pixels': removed,
            'matte': None if matte is None else {'rgb': list(matte.rgb), 'radius': matte.radius},
            'validation': validation}
