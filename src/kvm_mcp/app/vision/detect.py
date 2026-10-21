"""Lightweight local visual proposals. These are candidates, not semantic controls."""
import cv2
import numpy as np
from PIL import Image

from .model import Candidate


def overlap(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    area = max(0, right-left) * max(0, bottom-top)
    aa, ab = (a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1])
    return area / max(1, min(aa, ab)), area / max(1, aa+ab-area)


class ContourDetector:
    """Group strokes into words/icons, remove duplicate and nested proposals.

    No OCR/model download or display-server API is required. Detection runs on
    original pixels; downscaling is reserved for the overview rendering.
    """

    def detect(self, image: Image.Image) -> list[Candidate]:
        gray = cv2.cvtColor(np.asarray(image.convert('RGB')), cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 60, 170)
        # Join adjacent glyph strokes while preserving gaps between controls.
        joined = cv2.dilate(edges, np.ones((3, 5), dtype=np.uint8))
        contours, _ = cv2.findContours(joined, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        # Local contrast recovers text on gradients where its edges attach to a
        # large background contour (for example desktop menu-bar wallpaper).
        kernel = np.ones((17, 17), dtype=np.uint8)
        contrast = cv2.max(cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel),
                           cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel))
        mask = cv2.dilate(np.uint8(contrast > 45)*255, np.ones((2, 5), dtype=np.uint8))
        extra, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        base_count = len(contours)
        contours = list(contours) + list(extra)
        width, height = image.size
        boxes = []
        for index, contour in enumerate(contours):
            x, y, w, h = cv2.boundingRect(contour)
            if not (8 <= w <= min(480, width*.7) and 8 <= h <= min(110, height*.4)):
                continue
            if w / h > 24 or h / w > 4 or w*h < 90:
                continue
            box = (x, y, x+w, y+h)
            if index >= base_count and any(overlap(box, old)[0]>.5 for old in boxes):
                continue  # Contrast pass fills holes rather than duplicating controls.
            boxes.append(box)
        # Prefer one enclosing compact control over its inner strokes/text.
        kept = []
        for box in sorted(boxes, key=lambda b: -(b[2]-b[0])*(b[3]-b[1])):
            duplicate = False
            for outer in kept:
                contained, iou = overlap(box, outer)
                ow, oh = outer[2]-outer[0], outer[3]-outer[1]
                cx,cy = (box[0]+box[2])/2,(box[1]+box[3])/2
                ox,oy = (outer[0]+outer[2])/2,(outer[1]+outer[3])/2
                same_center = abs(cx-ox)<6 and abs(cy-oy)<6
                compact_icon = max(ow,oh)<=72 and .65<ow/oh<1.5
                if iou > .65 or (contained > .92 and (same_center or compact_icon) and ow <= 320 and oh <= 80):
                    duplicate = True
                    break
            if not duplicate:
                kept.append(box)
        # A frame containing several spatially separate proposals is a group,
        # not an additional click point between its children.
        kept = [box for box in kept if sum(
            overlap(box, child)[0]>.95 and
            (child[2]-child[0])*(child[3]-child[1]) < (box[2]-box[0])*(box[3]-box[1])*.65
            for child in kept if child != box) < 2]
        # Merge baseline-aligned word/number fragments (e.g. "1920 × 1080").
        # Square icons are kept separate so adjacent toolbar buttons remain selectable.
        merged = []
        for box in sorted(kept,key=lambda b:(b[0],b[1])):
            match = None
            for index, previous in enumerate(merged):
                ph,bh = previous[3]-previous[1],box[3]-box[1]
                pr,br = (previous[2]-previous[0])/ph,(box[2]-box[0])/bh
                aligned = min(previous[3],box[3])-max(previous[1],box[1]) >= .7*max(ph,bh)
                text_like = (pr>1.5 and (br>1.5 or br<.8)) or (pr<.8 and br>1.5)
                if (aligned and text_like and 0 <= box[0]-previous[2] <= max(7,min(ph,bh)*.7)
                        and box[2]-previous[0] <= 480):
                    match = index
                    break
            if match is None:
                merged.append(box)
            else:
                old = merged[match]
                merged[match] = (old[0],min(old[1],box[1]),box[2],max(old[3],box[3]))
        return [Candidate(box) for box in sorted(merged, key=lambda b: (b[1]//12, b[0], b[1]))]
