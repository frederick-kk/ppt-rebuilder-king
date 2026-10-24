"""Validate provider-native alpha without interpreting material colours."""
from pathlib import Path

import numpy as np
from PIL import Image

MODE = 'native-transparent-materials/1'


def validate_native_output(path, *, role='foreground'):
    """Require a nonempty transparent PNG; do not grade subject semantics.

    Foreground objects may touch the source frame. Board identity, gutter and
    canvas clipping checks remain the extractor's responsibility, using every
    nonzero alpha value including alpha=1.
    """
    path = Path(path)
    try:
        with Image.open(path) as image:
            if image.format != 'PNG' or min(image.size) < 1:
                raise ValueError('Native materials require a nonempty PNG')
            image.load()
            if 'A' not in image.getbands() and 'transparency' not in image.info:
                raise ValueError('Native material PNG has no native transparency')
            alpha = np.asarray(image.convert('RGBA'))[..., 3]
            size = list(image.size)
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('Cannot decode native material PNG') from error
    visible = int(np.count_nonzero(alpha))
    if not visible:
        raise ValueError('Native material PNG is completely transparent')
    transparent = int(np.count_nonzero(alpha == 0))
    if not transparent:
        raise ValueError('Native material PNG has no fully transparent pixels')
    return {'path': str(path.resolve()), 'format': 'PNG', 'size': size,
            'visible_pixels': visible, 'transparent_pixels': transparent,
            'background_mode': MODE, 'transparent_background': True,
            'role': role, 'warnings': [], 'quality': 'not_evaluated'}
