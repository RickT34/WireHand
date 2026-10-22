"""Inspect a saved device screenshot and export actual WireHand visual marks.

An optional target ID demonstrates click-to-coordinate resolution using an
in-memory input backend. This script never sends OS or hardware input.
"""
import argparse
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw, ImageFont

from wirehand.automation import Computer, Recording
from wirehand.automation.inspection import Inspector
from wirehand.vision.matching import locate
from wirehand.cli.inspect import export_gallery
from wirehand.core.ownership import InputGate


class PreviewBackend:
    """Screenshot and recorded clicks only; this backend has no device connection."""
    name = 'visual-preview'

    def __init__(self, image):
        self.image = image
        self.captures = 0
        self.events = []

    def screenshot(self, max_width=3840):
        self.captures += 1
        image = self.image.copy()
        if image.width > max_width:
            image.thumbnail((max_width, image.height))
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        return buffer.getvalue()

    @contextmanager
    def open_input(self):
        backend = self
        class Input:
            def click(self, x, y, button='left', wheel=0):
                backend.events.append({'point': [x*(backend.image.width-1), y*(backend.image.height-1)],
                                       'button': button, 'wheel': wheel})
        yield Input()


def save_inspection(output, prefix, inspection):
    (output/f'{prefix}.json').write_text(json.dumps(inspection.metadata, ensure_ascii=False, indent=2))
    for region, data in zip(inspection.metadata['image_regions'], inspection.images):
        (output/f'{prefix}-{region}.png').write_bytes(data)


def comparison_sheet(output, result):
    """Compose the unaltered overview and selected marked crop for documentation."""
    crop_path = next(output.glob(f"region*-{result['target_region']}.png"))
    with Image.open(output/'overview-overview.png') as source:
        overview = source.convert('RGB')
    with Image.open(crop_path) as source:
        crop = source.convert('RGB')
    canvas = Image.new('RGB', (1440, 960), '#f1f5f8')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.load_default(size=24)
    normal = ImageFont.load_default(size=19)
    small = ImageFont.load_default(size=16)
    draw.text((28, 20), '1. Choose a region', font=title, fill='#233f51')
    draw.text((490, 20), '2. Choose a marked target', font=title, fill='#233f51')
    overview.thumbnail((420, 430), Image.Resampling.LANCZOS)
    canvas.paste(overview, (28, 68))
    crop.thumbnail((920, 790), Image.Resampling.LANCZOS)
    canvas.paste(crop, (490, 68))
    metrics_top = 68+overview.height+38
    draw.text((28, metrics_top), f"{result['candidates']} candidates", font=normal, fill='#426278')
    draw.text((28, metrics_top+40), f"{result['region_id']} -> {result['region_targets']} candidates", font=title, fill='#254e3c')
    # Show up to three views around the selected crop; the gallery contains all.
    views = result['views']
    selected_index = next(i for i, v in enumerate(views) if v['id'] == result['target_region'])
    start = max(0, selected_index-1)
    cards_top = metrics_top+100
    for i, view in enumerate(views[start:start+3]):
        box = (28+i*140, cards_top, 158+i*140, cards_top+92)
        selected = view['id'] == result['target_region']
        draw.rounded_rectangle(box, radius=10, fill='#dcefe4' if selected else '#e3eaf0',
                               outline='#78ac8d' if selected else '#c1cfd9')
        draw.text((box[0]+14, cards_top+14), view['id'], font=title, fill='#254e3c')
        draw.text((box[0]+14, cards_top+56), f"{view['count']} marks", font=small, fill='#426278')
    draw.text((28, cards_top+140), '3. Click by ID', font=title, fill='#233f51')
    draw.text((28, cards_top+183), f"click_target(..., '{result['target_id']}')", font=normal, fill='#254e3c')
    draw.text((28, 920), 'Saved device screenshot / actual WireHand detections / no physical input sent', font=small, fill='#627889')
    canvas.save(output/'visual-targeting.png')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='A saved device screenshot')
    parser.add_argument('--output', type=Path, required=True, help='An empty output directory')
    parser.add_argument('--region', default='FULL', help='Region to expand, e.g. C09 or FULL')
    parser.add_argument('--target', help='Optional candidate ID to resolve with simulated input')
    args = parser.parse_args()
    output = args.output
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory must be empty')
    with Image.open(args.image) as source:
        screen = source.convert('RGB')
    screen.thumbnail((3840, screen.height), Image.Resampling.LANCZOS)
    output.mkdir(parents=True, exist_ok=True)
    screen.save(output/'screen.png')
    backend = PreviewBackend(screen)
    with tempfile.TemporaryDirectory(prefix='wirehand-visual-demo-') as runtime:
        inspector = Inspector(backend, InputGate(Path(runtime)))
        overview = inspector.inspect()
        save_inspection(output, 'overview', overview)
        snapshot_id = overview.metadata['snapshot_id']
        region = inspector.region(snapshot_id, args.region)
        save_inspection(output, 'region', region)
        targets = list(region.metadata['targets'])
        for page in range(1, region.metadata['pages']):
            more = inspector.region(snapshot_id, args.region, page=page)
            save_inspection(output, f'region-page-{page}', more)
            targets.extend(more.metadata['targets'])
        if backend.captures != 1:
            raise RuntimeError('Paging/refining unexpectedly captured multiple frames')
        result = {'source_image': args.image.name, 'size': list(screen.size),
                  'presentation': overview.metadata['presentation'],
                  'candidates': overview.metadata['total_candidates'], 'region_id': args.region,
                  'region_pages': region.metadata['pages'], 'region_targets': len(targets),
                  'views': [{'id': v['id'], 'count': v['count']} for v in region.metadata['regions']],
                  'physical_input_sent': False, 'inspection_captures': 1}
        if args.target:
            target = next((t for t in targets if t['id'] == args.target), None)
            if target is None:
                parser.error('Target is not in the requested region; inspect its exported images/JSON first')
            clicked = inspector.click(snapshot_id, args.target)
            if len(backend.events) != 1 or backend.captures != 2:
                raise RuntimeError('Expected one simulated click and a fresh validation frame')
            if max(abs(a-b) for a, b in zip(backend.events[0]['point'], target['point'])) > 1e-6:
                raise RuntimeError('Click coordinate differs from the selected candidate')
            result.update(target_id=args.target, target_region=target['region'], click=clicked,
                          events=backend.events, click_validation_captures=1)
            # Build a one-click recording from this real frame using simulated
            # input, so users can inspect the actual generated target/template.
            recording_backend = PreviewBackend(screen)
            with Computer(recording_backend, Path(runtime)/'recording') as computer:
                with computer.record(output/'recording'):
                    computer.click(*target['point'])
            flow = Recording.load(output/'recording')
            identity = flow.steps[0]['args']['target']
            visual_target = flow.target(identity)
            match = locate(screen, visual_target)
            if max(abs(a-b) for a, b in zip(match.point,target['point']))>3:
                raise RuntimeError('Generated visual target does not locate the selected point')
            result['recording'] = {'directory':'recording','target_id':identity,
                                   'image':flow.data['targets'][identity]['image'],
                                   'point':list(visual_target.point),'screen_size':list(visual_target.screen_size),
                                   'matched_point':list(match.point)}
            comparison_sheet(output, result)
        (output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    gallery = export_gallery(output/'screen.png', output/'gallery')
    result['gallery_pages'] = gallery['pages']
    (output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
