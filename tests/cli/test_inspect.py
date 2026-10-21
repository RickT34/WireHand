import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from kvm.cli.inspect import export_gallery
from kvm.platforms.image import ImageBackend


class GalleryTests(unittest.TestCase):
    def test_gallery_coordinates_use_the_same_scaled_frame_as_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'large.png'
            Image.new('RGB',(4000,1000),'#222222').save(source)
            manifest = export_gallery(source,Path(directory)/'gallery')
            with Image.open(Path(directory)/'gallery/original.png') as frame:
                self.assertEqual(frame.size,tuple(manifest['size']))
                self.assertEqual(frame.size,(3840,960))
            with Image.open(io.BytesIO(ImageBackend(source).screenshot(640))) as frame:
                self.assertEqual(frame.format,'JPEG')
                self.assertEqual(frame.size,(640,160))

