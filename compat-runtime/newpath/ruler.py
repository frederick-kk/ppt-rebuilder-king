"""A coordinate aid only; the unmarked source remains the visual authority."""
from PIL import Image, ImageDraw, ImageFont


GUIDE_POLICY = "grid25-major50-edge5-four-edge-label50/1"


def _coordinate_font():
    # Use installed fonts, then Pillow's bundled default. No font download or
    # installation is needed; pixel registration never depends on font metrics.
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, 14)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=14)
    except TypeError:  # Older Pillow has an unscaled built-in bitmap font.
        return ImageFont.load_default()


def _label(draw, text, position, canvas, font, *, right=False, bottom=False,
           centered=False, reserved=()):
    left, top, end_x, end_y = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    width, height = end_x - left, end_y - top
    margin = 14  # Keep glyphs clear of the complete 12px perimeter tick band.
    if width + 2 * margin > canvas[0] or height + 2 * margin > canvas[1]:
        return  # A tiny helper canvas cannot contain a complete digit label.
    x, y = position
    if right:
        x = canvas[0] - x - width
    elif centered:
        x -= width // 2
    if bottom:
        y = canvas[1] - y - height
    # Clamp the entire outlined glyph, including font bearings, inside the
    # original canvas. This does not move ticks or change coordinate origin.
    x = min(max(margin, x), canvas[0] - width - margin)
    y = min(max(margin, y), canvas[1] - height - margin)
    bounds = (x, y, x + width, y + height)
    if any(x < b[2] and b[0] < bounds[2] and y < b[3] and b[1] < bounds[3]
           for b in reserved):
        return  # Keep horizontal labels when a side label meets a corner.
    draw.text((x - left, y - top), text, font=font, fill=(255, 255, 255, 240),
              stroke_width=1, stroke_fill=(0, 30, 70, 240))
    return bounds


def render_coordinate_guide(image: Image.Image) -> Image.Image:
    """Return a same-size RGB guide without modifying the supplied PIL image.

    This experiment uses a 25/50px interior grid. Perimeter ticks retain
    5px subdivisions; longer marks distinguish 10/25/50px anchors. Labels are
    outlined glyphs on four edges, with no opaque panel covering source pixels.
    This aid introduces no coordinate rounding, snapping or accuracy claim.
    """
    if not isinstance(image, Image.Image):
        raise TypeError("A PIL image is required for a coordinate guide.")
    base = image.convert("RGB")
    width, height = base.size
    overlay = Image.new("RGBA", base.size)
    draw = ImageDraw.Draw(overlay)
    for x in range(0, width, 25):
        draw.line((x, 0, x, height - 1), fill=(35, 70, 110, 75 if x % 50 == 0 else 30))
    for y in range(0, height, 25):
        draw.line((0, y, width - 1, y), fill=(35, 70, 110, 75 if y % 50 == 0 else 30))
    tick_colour = (10, 40, 80, 200)
    for coordinate in range(0, max(width, height), 5):
        length = 11 if coordinate % 50 == 0 else 7 if coordinate % 25 == 0 else 4 if coordinate % 10 == 0 else 2
        if coordinate < width:
            depth = min(length, height - 1)
            draw.line((coordinate, 0, coordinate, depth), fill=tick_colour)
            draw.line((coordinate, height - 1, coordinate, height - 1 - depth), fill=tick_colour)
        if coordinate < height:
            depth = min(length, width - 1)
            draw.line((0, coordinate, depth, coordinate), fill=tick_colour)
            draw.line((width - 1, coordinate, width - 1 - depth, coordinate), fill=tick_colour)
    font = _coordinate_font()
    horizontal_labels = []
    for x in range(0, width, 50):
        for bottom in (False, True):
            bounds = _label(draw, str(x), (x, 14), base.size, font,
                            bottom=bottom, centered=True, reserved=horizontal_labels)
            if bounds is not None:
                horizontal_labels.append(bounds)
    # The horizontal labels already show the origin at both left corners.
    for y in range(50, height, 50):
        _label(draw, str(y), (14, y + 3), base.size, font, reserved=horizontal_labels)
        _label(draw, str(y), (14, y + 3), base.size, font, right=True, reserved=horizontal_labels)
    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
