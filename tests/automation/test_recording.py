import ast
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from kvm.automation import Computer, Recorder, Recording, AutomationInterrupted
from kvm.vision import TargetNotFound, AmbiguousTarget
from kvm.core.ownership import InputGate
from kvm.vision.matching import locate
from .helpers import ScreenBackend,screen


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.backend=ScreenBackend()
        self.recorder=Recorder(self.backend,self.root/'demo')
        self.inner=self.enterContext(self.backend.open_input())
        self.session=self.recorder.wrap(self.inner)
        self.point=(100/479,90/319)

    def record_click(self):
        self.session.pointer(*self.point,1)
        self.session.pointer(*self.point,0)
        self.recorder.finish()
        return Recording.load(self.root/'demo')

    def test_click_generates_target_and_editable_script(self):
        flow=self.record_click()
        self.assertEqual(flow.steps[0]['action'],'click')
        self.assertEqual(flow.steps[0]['args']['button'],'left')
        script=(self.root/'demo/replay.py').read_text()
        ast.parse(script)
        self.assertIn('computer.click(target=flow.target(',script)
        self.assertNotIn('kvm.interfaces',script)
        self.assertNotIn('kvm.platforms',script)
        self.assertTrue(flow.target(flow.steps[0]['args']['target']).image.exists())

    def test_replay_locates_moved_target_without_using_recorded_coordinates(self):
        flow=self.record_click()
        moved=ScreenBackend(screen((240,170)))
        computer=Computer(moved,self.root/'runtime',timeout=.5,poll_interval=.001)
        flow.replay(computer)
        self.assertEqual(len(moved.events),1)
        kind,x,y,button,wheel=moved.events[0]
        self.assertEqual(kind,'click')
        self.assertAlmostEqual(x,290/479,delta=.005)
        self.assertAlmostEqual(y,190/319,delta=.005)
        self.assertEqual(button,'left')

    def test_generated_python_runs_from_another_directory(self):
        self.record_click()
        replay=ScreenBackend(screen((240,170)))
        computer=Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001)
        with patch('kvm.connect',return_value=computer):
            runpy.run_path(str(self.root/'demo/replay.py'),run_name='__main__')
        self.assertEqual(replay.events[0][0],'click')
        self.assertAlmostEqual(replay.events[0][1],290/479,delta=.005)

    def test_automatic_wait_handles_a_target_that_appears_later(self):
        flow=self.record_click()
        replay=ScreenBackend(screen((240,170)))
        replay.frames=[screen((10,260))]*2
        computer=Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001)
        flow.replay(computer)
        self.assertGreaterEqual(replay.captures,5)
        self.assertEqual(len(replay.events),1)

    def test_duplicate_targets_are_not_guessed(self):
        flow=self.record_click()
        replay=ScreenBackend(screen(duplicate=True))
        computer=Computer(replay,self.root/'runtime',timeout=.04,poll_interval=.001)
        with self.assertRaises(AmbiguousTarget):flow.replay(computer)
        self.assertEqual(replay.events,[])

    def test_double_click_is_compiled_into_one_action(self):
        self.session.click(*self.point,'left')
        self.session.click(*self.point,'left')
        self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual(len(flow.steps),1)
        self.assertEqual(flow.steps[0]['args']['clicks'],2)

    def test_drag_preserves_the_final_release_position(self):
        self.session.pointer(*self.point,1)
        self.session.pointer(140/479,110/319,1)
        self.session.pointer(180/479,130/319,0)
        self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual(flow.steps[0]['action'],'drag')
        self.assertEqual(flow.steps[0]['args']['path'][-1]['dx'],80.)
        replay=ScreenBackend(screen((240,170)))
        flow.replay(Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001))
        self.assertEqual(replay.events[-1][0],'pointer')
        self.assertEqual(replay.events[-1][3],0)
        self.assertAlmostEqual(replay.events[-1][1],370/479,delta=.005)

    def test_modifier_click_preserves_control_key(self):
        self.session.keys(['ctrl'])
        self.session.click(*self.point,'left')
        self.session.keys([])
        self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual([s['action'] for s in flow.steps],['click'])
        self.assertEqual(flow.steps[0]['args']['modifiers'],['ctrl'])
        replay=ScreenBackend()
        flow.replay(Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001))
        self.assertEqual(replay.events[0],('keys',['ctrl']))
        self.assertEqual(replay.events[-1],('keys',[]))

    def test_typing_is_grouped_and_releases_keys(self):
        self.session.keys(['h']);self.session.keys([])
        self.session.keys(['i']);self.session.keys([])
        self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual([s['action'] for s in flow.steps],['key_sequence'])
        replay=ScreenBackend()
        flow.replay(Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001))
        self.assertEqual([e for e in replay.events if e[1]],[('keys',['h']),('keys',['i'])])
        self.assertEqual(replay.events[-1],('keys',[]))

    def test_interrupted_recording_cannot_silently_replay(self):
        self.session.click(*self.point,'left')
        self.recorder.finish(interrupted=True)
        with self.assertRaisesRegex(ValueError,'incomplete'):
            Recording.load(self.root/'demo')

    def test_invalid_later_step_is_rejected_before_replaying_any_action(self):
        flow=self.record_click()
        data=flow.data
        data['steps'].append({'action':'click','args':{'target':next(iter(data['targets'])),'arbitrary':'bad'}})
        (self.root/'demo/recording.json').write_text(json.dumps(data))
        replay=ScreenBackend()
        with self.assertRaisesRegex(ValueError,'parameters'):
            Computer(replay,self.root/'runtime').replay(self.root/'demo')
        self.assertEqual(replay.events,[])

    def test_asset_escape_is_rejected(self):
        flow=self.record_click()
        next(iter(flow.data['targets'].values()))['image']='../outside.png'
        (self.root/'demo/recording.json').write_text(json.dumps(flow.data))
        with self.assertRaisesRegex(ValueError,'inside'):
            Recording.load(self.root/'demo')

    def test_human_intervention_stops_existing_automation(self):
        flow=self.record_click()
        replay=ScreenBackend()
        computer=Computer(replay,self.root/'runtime')
        with InputGate(self.root/'runtime').human():pass
        with self.assertRaises(AutomationInterrupted):flow.replay(computer)
        self.assertEqual(replay.events,[])

    def test_cancelled_drag_releases_pointer(self):
        flow=self.record_click()
        replay=ScreenBackend()
        computer=Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001)
        target=flow.target(flow.steps[0]['args']['target'])
        from contextlib import contextmanager
        from tests.fakes import FakeInput
        class CancellingInput(FakeInput):
            def pointer(inner,x,y,buttons=0,wheel=0):
                super().pointer(x,y,buttons,wheel)
                if buttons:computer.cancel()
        @contextmanager
        def connection():
            yield CancellingInput(replay)
        replay.open_input=connection
        with self.assertRaises(AutomationInterrupted):
            computer.drag(target=target,path=[{'dx':20.,'dy':0.,'delay':.001}])
        self.assertEqual(replay.events[-1][3],0)

    def test_python_context_can_record_existing_programmatic_operations(self):
        source=ScreenBackend()
        computer=Computer(source,self.root/'sdk-runtime',timeout=.5,poll_interval=.001)
        with computer.record(self.root/'sdk-flow'):
            computer.click(100,90)
            computer.write('hello')
        flow=Recording.load(self.root/'sdk-flow')
        self.assertEqual([s['action'] for s in flow.steps],['click','write'])
        moved=ScreenBackend(screen((240,170)))
        flow.replay(Computer(moved,self.root/'sdk-replay',timeout=.5,poll_interval=.001))
        self.assertAlmostEqual(moved.events[0][1],290/479,delta=.005)
        self.assertEqual(moved.events[1],('type_text','hello'))

    def test_failed_input_is_not_automatically_retried(self):
        from contextlib import contextmanager
        flow=self.record_click()
        replay=ScreenBackend()
        class FailingInput:
            def click(inner,*args):
                replay.events.append(('attempt',*args))
                raise OSError('device disconnected after input')
        @contextmanager
        def connection():
            yield FailingInput()
        replay.open_input=connection
        with self.assertRaisesRegex(OSError,'disconnected'):
            flow.replay(Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001))
        self.assertEqual(len(replay.events),1)

    def test_hover_opened_menu_is_preserved_and_replayed(self):
        from contextlib import contextmanager
        from PIL import ImageDraw,ImageFont
        from tests.fakes import FakeInput
        class MenuBackend(ScreenBackend):
            def __init__(inner,origin):
                super().__init__();inner.origin=origin;inner.opened_menu=False;inner.finished=False
            def screenshot(inner,max_width=3840):
                inner.image=screen(inner.origin)
                if inner.opened_menu:
                    x,y=inner.origin;draw=ImageDraw.Draw(inner.image)
                    draw.rectangle((x,y+52,x+110,y+96),fill='#aa5533',outline='white',width=2)
                    draw.text((x+18,y+62),'SAVE',font=ImageFont.load_default(size=21),fill='white')
                return super().screenshot(max_width)
            @contextmanager
            def open_input(inner):
                class MenuInput(FakeInput):
                    def pointer(device,x,y,buttons=0,wheel=0):
                        super().pointer(x,y,buttons,wheel)
                        ox,oy=inner.origin
                        if ox<x*479<ox+110 and oy<y*319<oy+44:inner.opened_menu=True
                    def click(device,x,y,button='none',wheel=0):
                        super().click(x,y,button,wheel)
                        ox,oy=inner.origin
                        if inner.opened_menu and ox<x*479<ox+110 and oy+52<y*319<oy+96:inner.finished=True
                yield MenuInput(inner)
        source=MenuBackend((50,70))
        recorder=Recorder(source,self.root/'menu')
        with source.open_input() as device:
            session=recorder.wrap(device)
            session.pointer(100/479,90/319)
            session.click(100/479,145/319,'left')
        recorder.finish()
        self.assertTrue(source.finished)
        flow=Recording.load(self.root/'menu')
        self.assertEqual([s['action'] for s in flow.steps],['move_path','click'])
        moved=MenuBackend((240,70))
        flow.replay(Computer(moved,self.root/'menu-replay',timeout=.5,poll_interval=.001))
        self.assertTrue(moved.finished)

    def test_modifier_key_tap_is_not_discarded(self):
        self.session.keys(['meta']);self.session.keys([])
        self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual(flow.steps[0]['action'],'key_sequence')
        self.assertEqual(flow.steps[0]['args']['events'][0]['keys'],['meta'])

    def test_long_press_duration_is_preserved(self):
        clock=[0.]
        recorder=Recorder(self.backend,self.root/'long-press',clock=lambda:clock[0])
        session=recorder.wrap(self.inner)
        session.pointer(*self.point,1)
        clock[0]=1.2
        session.pointer(*self.point,0)
        recorder.finish()
        flow=Recording.load(self.root/'long-press')
        self.assertEqual(flow.steps[0]['args']['hold_ms'],1200)

    def test_wheel_burst_is_one_visual_scroll_action(self):
        for _ in range(3):self.session.pointer(*self.point,0,-1)
        self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual([s['action'] for s in flow.steps],['scroll'])
        self.assertEqual(flow.steps[0]['args']['clicks'],-3)
        replay=ScreenBackend(screen((240,170)))
        flow.replay(Computer(replay,self.root/'runtime',timeout=.5,poll_interval=.001))
        self.assertEqual(replay.events[0][-1],-3)

    def test_mouse_release_cleanup_retains_a_long_press(self):
        clock=[0.]
        recorder=Recorder(self.backend,self.root/'released-hold',clock=lambda:clock[0])
        session=recorder.wrap(self.inner)
        session.pointer(*self.point,1)
        clock[0]=.8
        session.release_all()
        recorder.finish()
        flow=Recording.load(self.root/'released-hold')
        self.assertEqual(flow.steps[0]['args']['hold_ms'],800)
