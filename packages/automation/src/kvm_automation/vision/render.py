"""Spatial pages, context-preserving crops and readable, non-overlapping marks."""
import io
import math
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .model import Box, Target, View

FONT = ImageFont.load_default(size=17)
SMALL_FONT = ImageFont.load_default(size=14)
COLORS = ('#20d4bd', '#ffc653', '#91b4ff', '#f597d0', '#a9dc76', '#ff9f73')


def png(image):
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def padded(box: Box, size, padding=24):
    return (max(0, box[0]-padding), max(0, box[1]-padding),
            min(size[0], box[2]+padding), min(size[1], box[3]+padding))


def extent(targets):
    return (min(t.candidate.box[0] for t in targets), min(t.candidate.box[1] for t in targets),
            max(t.candidate.box[2] for t in targets), max(t.candidate.box[3] for t in targets))


def partition(targets: list[Target], limit=18) -> list[list[Target]]:
    """Split by spatial gaps; also split sparse groups spanning a large screen."""
    if not targets:
        return []
    box = extent(targets)
    width, height = box[2]-box[0], box[3]-box[1]
    # A distant cursor/isolated icon must not force a mostly-empty tall crop.
    gap_axis = None
    gap_cut = None
    largest_gap = 90
    for axis in (0,1):
        ordered = sorted(targets, key=lambda t: t.candidate.box[axis])
        for i in range(1,len(ordered)):
            gap = ordered[i].candidate.box[axis] - max(t.candidate.box[axis+2] for t in ordered[:i])
            if gap > largest_gap:
                largest_gap, gap_axis, gap_cut = gap,axis,i
    if gap_axis is not None:
        ordered = sorted(targets,key=lambda t:t.candidate.box[gap_axis])
        return partition(ordered[:gap_cut],limit)+partition(ordered[gap_cut:],limit)
    if len(targets) <= limit and width <= 850 and height <= 550:
        return [targets]
    if len(targets) == 1:
        return [targets]
    axis = 0 if width/850 >= height/550 else 1
    ordered = sorted(targets, key=lambda t: t.candidate.center[axis])
    # Favor natural gaps around the middle rather than slicing an adjacent pair.
    lo, hi = max(1, len(ordered)//3), min(len(ordered)-1, math.ceil(len(ordered)*2/3))
    cut = max(range(lo, hi+1), key=lambda i:
              ordered[i].candidate.center[axis]-ordered[i-1].candidate.center[axis])
    return partition(ordered[:cut], limit) + partition(ordered[cut:], limit)


def intersects(a, b, pad=0):
    return a[0] < b[2]+pad and a[2]+pad > b[0] and a[1] < b[3]+pad and a[3]+pad > b[1]


def render_view(image: Image.Image, view: View):
    targets = sorted(view.targets,key=lambda t:(t.candidate.center[1]//16,t.candidate.center[0]))
    crop = image.crop(view.box)
    # Never reduce fine controls. Magnify small crops while bounding image size.
    scale = min(2.5, 1100/crop.width, 760/crop.height)
    width, height = max(1, round(crop.width*scale)), max(1, round(crop.height*scale))
    crop = crop.resize((width, height), Image.Resampling.LANCZOS)
    # A dedicated label gutter guarantees a readable fallback for dense targets.
    top = 100  # Two label lanes above the screenshot, including screen-edge controls.
    canvas = Image.new('RGB', (width+125, height+top+30+len(targets)*26), '#101820')
    canvas.paste(crop, (0, top))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 11), f'{view.id} | {len(view.targets)} candidates | {scale:.1f}x', font=FONT, fill='white')
    target_boxes = []
    for target in targets:
        x1,y1,x2,y2 = target.candidate.box
        target_boxes.append(((x1-view.box[0])*scale, (y1-view.box[1])*scale+top,
                             (x2-view.box[0])*scale, (y2-view.box[1])*scale+top))
    labels = []
    crop_edges = cv2.Canny(np.asarray(crop),60,160)
    gutter_bottom = 44
    for i, (target, box) in enumerate(zip(targets, target_boxes)):
        color = COLORS[i % len(COLORS)]
        cx,cy = target.candidate.center
        cx,cy = (cx-view.box[0])*scale, (cy-view.box[1])*scale+top
        label_w, label_h = max(58,math.ceil(draw.textlength(target.id,font=FONT))+8), 23
        options = [(cx-label_w/2,box[1]-label_h-3), (box[2]+4,box[1]),
                   (box[0]-label_w-4,box[1]), (cx-label_w/2,box[3]+3)]
        if cy < top+130:
            options += [(max(0,min(width-label_w,cx-label_w/2)),y) for y in (72,44)]
        label = None
        for x,y in options:
            proposed = (round(x),round(y),round(x)+label_w,round(y)+label_h)
            if proposed[0]<0 or proposed[1]<42 or proposed[2]>width or proposed[3]>canvas.height:
                continue
            if any(intersects(proposed, other, 2) for other in labels+target_boxes):
                continue
            pixels = crop_edges[max(0,proposed[1]-top):max(0,min(height,proposed[3]-top)),proposed[0]:proposed[2]]
            if pixels.size and np.mean(pixels>0)>.025:
                continue  # Also avoid text/icons missed by the detector.
            label = proposed
            break
        if label is None:
            label_y = max(gutter_bottom+3,round(cy-label_h/2))
            label = (width+12,label_y,width+12+label_w,label_y+label_h)
            gutter_bottom = label_y+label_h
        labels.append(label)
        draw.rectangle(box, outline=color, width=2)
        anchor = (min(max(cx,label[0]),label[2]), min(max(cy,label[1]),label[3]))
        draw.line((cx,cy,*anchor),fill=color,width=1)
        draw.ellipse((cx-2,cy-2,cx+2,cy+2),fill=color)
        draw.rounded_rectangle(label,radius=4,fill=color)
        draw.text((label[0]+4,label[1]+2),target.id,font=FONT,fill='#07101a')
    return canvas.crop((0,0,canvas.width,max(height+top+30,gutter_bottom+5)))


def render_overview(image: Image.Image, views: list[View], highlight=None):
    canvas = image.copy()
    canvas.thumbnail((1280, 800), Image.Resampling.LANCZOS)
    scale = canvas.width / image.width
    draw = ImageDraw.Draw(canvas)
    for i, view in enumerate(views):
        box = tuple(round(v*scale) for v in view.box)
        color = '#ff6868' if view.id == highlight else COLORS[i % len(COLORS)]
        draw.rectangle(box,outline=color,width=2)
        text = f'{view.id} ({len(view.targets)})'
        # Put a region label inside its box; individual targets appear only in crops.
        x,y = box[0]+3,box[1]+3
        draw.rectangle((x,y,x+97,y+23),fill='#101820')
        draw.text((x+3,y+3),text,font=SMALL_FONT,fill=color)
    return canvas
