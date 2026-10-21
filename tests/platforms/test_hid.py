import unittest

from kvm_rock5b_plus.hid import char_report, key_report, mouse_report, send_press
from kvm_rock5b_plus.backend import RockInput


class RecordingHID:
    def __init__(self, fail_first=False):
        self.reports = []
        self.fail_first = fail_first

    def write(self, report):
        self.reports.append(report)
        if self.fail_first and len(self.reports) == 1:
            raise OSError('USB disconnected')


class ReportTests(unittest.TestCase):
    def test_ctrl_alt_delete_hid_usage(self):
        self.assertEqual(key_report(['ctrl', 'alt', 'delete']), b'\x05\x00\x4c\x00\x00\x00\x00\x00')

    def test_uppercase_uses_shift(self):
        self.assertEqual(char_report('A'), b'\x02\x00\x04\x00\x00\x00\x00\x00')

    def test_shifted_punctuation(self):
        self.assertEqual(char_report('?'), b'\x02\x00\x38\x00\x00\x00\x00\x00')

    def test_unsupported_text_is_rejected_before_any_input(self):
        keyboard, mouse = RecordingHID(), RecordingHID()
        device = RockInput(keyboard, mouse)
        with self.assertRaisesRegex(ValueError, 'ASCII'):
            device.type_text('hello中文')
        self.assertEqual(keyboard.reports, [])
        self.assertEqual(mouse.reports, [])

    def test_more_than_six_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, 'six'):
            key_report(list('abcdefg'))

    def test_mouse_bottom_right_and_scroll_down(self):
        self.assertEqual(mouse_report(1, 1, 1, -1), b'\x01\xff\x7f\xff\x7f\xff')

    def test_nan_coordinate_rejected(self):
        with self.assertRaisesRegex(ValueError, 'finite'):
            mouse_report(float('nan'), 0)

    def test_key_release_attempted_even_after_disconnect(self):
        device = RecordingHID(fail_first=True)
        with self.assertRaisesRegex(OSError, 'disconnected'):
            send_press(device, b'pressed', b'released', 0)
        self.assertEqual(device.reports[-1], b'released')

    def test_key_release_after_success(self):
        device = RecordingHID()
        send_press(device, b'pressed', b'released', 0)
        self.assertEqual(device.reports, [b'pressed', b'released'])


if __name__ == '__main__':
    unittest.main()
