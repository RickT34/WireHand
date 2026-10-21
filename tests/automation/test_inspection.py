import io
from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from kvm.core.ownership import InputGate
from kvm.vision.models import Candidate
from kvm.automation.inspection import StaleSnapshot, Inspector
from tests.fakes import FakeBackend


class ScreenBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.image = Image.new('RGB',(800,600),'#252b36')
        ImageDraw.Draw(self.image).rectangle((100,100,180,140),fill='#dddddd')
        self.captures = 0

    def screenshot(self, max_width=1280):
        self.captures += 1
        out = io.BytesIO()
        self.image.save(out,'PNG')
        return out.getvalue()


class FixedDetector:
    def __init__(self, boxes=None):
        self.boxes = boxes or [(100,100,181,141)]

    def detect(self, image):
        return [Candidate(box) for box in self.boxes]


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.gate = InputGate(Path(self.directory.name))
        self.backend = ScreenBackend()
        self.vision = Inspector(self.backend,self.gate,FixedDetector())

    def observation(self):
        result = self.vision.inspect()
        return result.metadata['snapshot_id'],result.metadata['targets'][0]['id']

    def test_id_click_uses_source_pixels_not_crop_or_label_coordinates(self):
        snapshot,target = self.observation()
        result = self.vision.click(snapshot,target)
        self.assertEqual(result['point'],[140.,120.])
        self.assertEqual(self.backend.events,[('click',140/799,120/599,'left',0)])
        self.assertEqual(self.backend.closed,1)
        self.assertEqual(self.backend.captures,2, 'Click must capture again before sending input')
        with self.assertRaises(StaleSnapshot):
            self.vision.click(snapshot,target)
        self.assertEqual(len(self.backend.events),1)

    def test_paginated_images_share_one_capture_and_keep_all_targets(self):
        boxes=[(x,y,x+20,y+20) for y in (80,160,240,320,400) for x in range(20,700,60)]
        self.vision = Inspector(self.backend,self.gate,FixedDetector(boxes))
        first = self.vision.inspect(page_size=1)
        seen = set(t['id'] for t in first.metadata['targets'])
        self.backend.image.paste('red',(0,0,800,600))
        for page in range(1,first.metadata['pages']):
            response = self.vision.inspect(first.metadata['snapshot_id'],page,page_size=1)
            self.assertEqual(len(response.images),2)
            self.assertLessEqual(len(response.metadata['targets']),18)
            seen.update(t['id'] for t in response.metadata['targets'])
        self.assertEqual(len(seen),len(boxes))
        self.assertEqual(self.backend.captures,1)

    def test_local_target_change_rejected_without_input(self):
        snapshot,target = self.observation()
        self.backend.image.paste('#252b36',(100,100,181,141))
        with self.assertRaisesRegex(StaleSnapshot,'changed'):
            self.vision.click(snapshot,target)
        self.assertEqual(self.backend.opened,0)
        self.assertEqual(self.backend.events,[])

    def test_dense_screen_starts_with_regions_and_keeps_all_detail_accessible(self):
        boxes=[(x,y,x+12,y+12) for y in range(20,580,25) for x in range(20,780,60)]
        service = Inspector(self.backend,self.gate,FixedDetector(boxes))
        overview = service.inspect()
        self.assertEqual(overview.metadata['presentation'],'regions')
        self.assertEqual(len(overview.images),1)
        self.assertEqual(len(overview.metadata['regions']),9)
        self.assertEqual(sum(r['count'] for r in overview.metadata['regions']),len(boxes))
        self.assertEqual(overview.metadata['targets'],[])
        snapshot = overview.metadata['snapshot_id']
        first = service.region(snapshot,'FULL')
        seen = {t['id'] for t in first.metadata['targets']}
        for page in range(1,first.metadata['pages']):
            result = service.region(snapshot,'FULL',page=page)
            seen.update(t['id'] for t in result.metadata['targets'])
        self.assertEqual(len(seen),len(boxes))
        self.assertEqual(self.backend.captures,1)

    def test_unrelated_tiny_clock_update_does_not_reject_target(self):
        snapshot,target = self.observation()
        self.backend.image.paste('white',(760,5,765,10))
        self.vision.click(snapshot,target)
        self.assertEqual(self.backend.opened,1)

    def test_resolution_change_rejected_without_input(self):
        snapshot,target = self.observation()
        self.backend.image = self.backend.image.resize((1024,768))
        with self.assertRaisesRegex(StaleSnapshot,'dimensions'):
            self.vision.click(snapshot,target)
        self.assertEqual(self.backend.opened,0)

    def test_other_process_input_revision_invalidates_snapshot(self):
        snapshot,target = self.observation()
        other = InputGate(Path(self.directory.name))
        with other.automated():
            pass
        with self.assertRaisesRegex(StaleSnapshot,'ownership changed'):
            self.vision.click(snapshot,target)
        self.assertEqual(self.backend.opened,0)

    def test_input_during_capture_rejects_the_observation(self):
        capture = self.backend.screenshot
        def interrupted_capture(max_width):
            data = capture(max_width)
            with InputGate(Path(self.directory.name)).automated():
                pass
            return data
        self.backend.screenshot = interrupted_capture
        with self.assertRaisesRegex(StaleSnapshot,'during capture'):
            self.vision.inspect()
        self.assertEqual(self.backend.opened,0)

    def test_failed_click_cannot_be_retried_with_the_same_id(self):
        class FailingInput:
            def click(inner, *args):
                self.backend.events.append(('attempt',*args))
                raise OSError('USB disconnected during click')

        @contextmanager
        def connection():
            self.backend.opened += 1
            try:
                yield FailingInput()
            finally:
                self.backend.closed += 1

        self.backend.open_input = connection
        snapshot,target = self.observation()
        with self.assertRaisesRegex(OSError,'disconnected'):
            self.vision.click(snapshot,target)
        with self.assertRaises(StaleSnapshot):
            self.vision.click(snapshot,target)
        self.assertEqual(len(self.backend.events),1)
        self.assertEqual(self.backend.closed,1)

    def test_human_takeover_then_return_invalidates_snapshot(self):
        snapshot,target = self.observation()
        with self.gate.human():
            pass
        with self.assertRaisesRegex(StaleSnapshot,'ownership changed'):
            self.vision.click(snapshot,target)
        self.assertEqual(self.backend.opened,0)

    def test_manual_pause_still_blocks_target_click(self):
        snapshot,target = self.observation()
        self.gate.marker.touch()
        with self.assertRaisesRegex(RuntimeError,'manual control'):
            self.vision.click(snapshot,target)
        self.assertEqual(self.backend.captures,1)
        self.assertEqual(self.backend.opened,0)

    def test_grid_can_be_refined_without_recapturing(self):
        snapshot,_ = self.observation()
        grid = self.vision.region(snapshot,'FULL',mode='grid',page_size=4)
        target = grid.metadata['targets'][0]
        fine = self.vision.region(snapshot,target['id'],mode='grid',page_size=4)
        self.assertEqual(self.backend.captures,1)
        for small in fine.metadata['targets']:
            self.assertEqual(small['kind'],'grid')
            a,b = target['box'],small['box']
            self.assertTrue(a[0]<=b[0]<b[2]<=a[2] and a[1]<=b[1]<b[3]<=a[3])
        again = self.vision.region(snapshot,target['id'],mode='grid',page_size=4)
        self.assertEqual(fine.metadata['targets'],again.metadata['targets'])

    def test_unknown_and_expired_ids_do_not_open_input(self):
        with self.assertRaises(StaleSnapshot):
            self.vision.click('not-a-snapshot','T001')
        service = Inspector(self.backend,self.gate,FixedDetector(),ttl=0)
        result = service.inspect()
        with self.assertRaisesRegex(StaleSnapshot,'expired'):
            service.click(result.metadata['snapshot_id'],'T001')
        self.assertEqual(self.backend.opened,0)

    def test_snapshot_cache_is_bounded(self):
        service = Inspector(self.backend,self.gate,FixedDetector(),max_snapshots=1)
        first = service.inspect()
        second = service.inspect()
        with self.assertRaisesRegex(StaleSnapshot,'evicted'):
            service.inspect(first.metadata['snapshot_id'])
        self.assertEqual(service.inspect(second.metadata['snapshot_id']).metadata['snapshot_id'],second.metadata['snapshot_id'])

    def test_invalid_page_does_not_capture_and_invalid_button_does_not_mutate(self):
        with self.assertRaisesRegex(ValueError,'page 0'):
            self.vision.inspect(page=1)
        self.assertEqual(self.backend.captures,0)
        snapshot,target = self.observation()
        before = self.gate.revision
        with self.assertRaises(ValueError):
            self.vision.click(snapshot,target,button='none')
        self.assertEqual(self.gate.revision,before)
        self.assertEqual(self.backend.opened,0)
