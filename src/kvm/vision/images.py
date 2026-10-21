"""Decode screenshots and compare pixels without capture or input side effects."""
import io

import cv2
import numpy as np
from PIL import Image


def decode(data):
    with Image.open(io.BytesIO(data)) as image:
        if min(image.size) < 4:
            raise ValueError('Screenshot must be at least 4x4 pixels')
        if image.width*image.height > 16_000_000:
            raise ValueError('Screenshot exceeds 16 megapixels')
        return image.convert('RGB')

def changed_fraction(before, after):
    a = cv2.GaussianBlur(np.asarray(before, dtype=np.float32), (3,3), 0)
    b = cv2.GaussianBlur(np.asarray(after, dtype=np.float32), (3,3), 0)
    return float(np.mean(np.max(np.abs(a-b), axis=2) > 24))
