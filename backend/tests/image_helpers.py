"""Small real images for upload tests, never magic bytes pretending to be photos."""
import io

from PIL import Image


def jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (30, 60, 90)).save(output, format="JPEG")
    return output.getvalue()
