import unittest

from PIL import Image, ImageDraw

from kvm.vision.detection import ContourDetector
from kvm.vision.models import Candidate, Target
from kvm.vision.rendering import partition


class DetectorLayoutTests(unittest.TestCase):
    def test_blank_frame_has_no_visual_proposals(self):
        self.assertEqual(ContourDetector().detect(Image.new('RGB',(800,600),'#222222')),[])


    def test_separate_buttons_have_click_candidates_inside_each(self):
        image = Image.new('RGB',(640,320),'#222222')
        draw = ImageDraw.Draw(image)
        bounds = [(40,50,180,100),(240,50,380,100),(440,50,580,100)]
        for box,text in zip(bounds,('Open','Save','Cancel')):
            draw.rounded_rectangle(box,radius=8,fill='#444444',outline='white',width=2)
            draw.text((box[0]+20,box[1]+16),text,fill='white')
        candidates = ContourDetector().detect(image)
        for x1,y1,x2,y2 in bounds:
            self.assertTrue(any(x1<c.center[0]<x2 and y1<c.center[1]<y2 for c in candidates),bounds)


    def test_distant_cursor_does_not_stretch_toolbar_crop(self):
        targets = [Target('a',Candidate((100,20,120,40))),Target('b',Candidate((130,20,150,40))),
                   Target('cursor',Candidate((500,500,510,520)))]
        groups = partition(targets)
        self.assertEqual(sorted(len(g) for g in groups),[1,2])
        self.assertEqual({t.id for group in groups for t in group},{'a','b','cursor'})

