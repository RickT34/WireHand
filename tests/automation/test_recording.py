import ast
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from wirehand.automation import Computer, Recorder, Recording, AutomationInterrupted
from wirehand.vision import TargetNotFound, AmbiguousTarget
from wirehand.core.ownership import InputGate
from wirehand.vision.matching import locate
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
        self.assertNotIn('wirehand.interfaces',script)
        self.assertNotIn('wirehand.platforms',script)
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
        with patch('wirehand.connect',return_value=computer):
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

    def test_hover_path_opens_menu_without_creating_a_target(self):
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
        self.assertEqual(source.captures,1)
        self.assertNotIn('target',flow.steps[0]['args'])
        self.assertEqual(len(flow.data['targets']),1)
        moved=MenuBackend((50,70))
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

    def test_wheel_burst_is_one_scroll_action_without_a_visual_target(self):
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

    def test_keyboard_recording_and_replay_never_request_a_screenshot(self):
        with patch.object(self.backend,'screenshot',side_effect=AssertionError('Keyboard must not capture')):
            self.session.keys(['a']);self.session.keys([])
            self.session.press_key(['enter'])
            self.session.type_text('hello')
            result=self.recorder.finish()
            flow=Recording.load(self.root/'demo')
            self.assertEqual(result['status'],'complete')
            self.assertEqual(flow.data['targets'],{})
            self.assertFalse(list((self.root/'demo/assets').iterdir()))
            self.backend.events.clear()
            flow.replay(Computer(self.backend,self.root/'runtime'))
        self.assertEqual([s['action'] for s in flow.steps],['key_sequence','hotkey','write'])
        self.assertIn(('press_key',['enter'],50),self.backend.events)
        self.assertIn(('type_text','hello'),self.backend.events)
        self.assertNotIn('flow.target(', (self.root/'demo/replay.py').read_text())

    def test_hover_and_scroll_preserve_positions_without_capturing_any_frame(self):
        with patch.object(self.backend,'screenshot',side_effect=AssertionError('Non-click must not capture')):
            self.session.pointer(.2,.3)
            self.session.pointer(.4,.5)
            self.session.pointer(.6,.7,0,-2)
            self.recorder.finish()
            flow=Recording.load(self.root/'demo')
            self.backend.events.clear()
            flow.replay(Computer(self.backend,self.root/'runtime'))
        self.assertEqual(flow.data['targets'],{})
        self.assertEqual(self.backend.events,[('pointer',.2,.3,0,0),('pointer',.4,.5,0,0),('pointer',.6,.7,0,-2)])

    def test_only_press_captures_and_later_inputs_do_not_depend_on_click_image(self):
        self.session.click(*self.point,'left')
        self.assertEqual(self.backend.captures,1)
        with patch.object(self.backend,'screenshot',side_effect=AssertionError('No new click')):
            self.session.type_text('hello')
            self.session.press_key(['enter'])
            self.session.pointer(.8,.8,0,-1)
            self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual(len(flow.data['targets']),1)
        self.assertEqual([s['action'] for s in flow.steps if 'target' in s['args']],['click'])
        # A keyboard operation must run even when the screen has changed since the click.
        from contextlib import contextmanager
        from tests.fakes import FakeInput
        from PIL import Image
        replay=ScreenBackend()
        @contextmanager
        def connection():
            class Input(FakeInput):
                def click(inner,*args):
                    super().click(*args)
                    replay.image=Image.new('RGB',(480,320),'black')
            yield Input(replay)
        replay.open_input=connection
        flow.replay(Computer(replay,self.root/'runtime',timeout=.1,poll_interval=.001))
        self.assertIn(('type_text','hello'),replay.events)
        self.assertIn(('press_key',['enter'],50),replay.events)

    def test_drag_uses_one_frame_captured_before_press_changes_the_screen(self):
        from contextlib import contextmanager
        from PIL import Image
        from tests.fakes import FakeInput
        original=self.backend.image.copy()
        @contextmanager
        def connection():
            class Input(FakeInput):
                def pointer(inner,x,y,buttons=0,wheel=0):
                    super().pointer(x,y,buttons,wheel)
                    if buttons:self.backend.image=Image.new('RGB',(480,320),'black')
            yield Input(self.backend)
        with connection() as inner:
            session=self.recorder.wrap(inner)
            session.pointer(*self.point,1)
            session.pointer(140/479,110/319,1)
            session.pointer(180/479,130/319,0)
        self.recorder.finish()
        flow=Recording.load(self.root/'demo')
        self.assertEqual(self.backend.captures,1)
        self.assertEqual(flow.steps[0]['action'],'drag')
        target=flow.target(flow.steps[0]['args']['target'])
        self.assertAlmostEqual(locate(original,target).point[0],100,delta=3)

    def test_blank_click_is_saved_as_local_patch_without_whole_screen_fallback(self):
        from PIL import Image
        self.backend.image=Image.new('RGB',(480,320),'black')
        flow=self.record_click()
        self.assertEqual(flow.data['status'],'complete')
        target=next(iter(flow.data['targets'].values()))
        self.assertEqual(target['strategy'],'patch')
        with Image.open(self.root/'demo'/target['image']) as patch_image:
            self.assertLess(patch_image.width,120)
            self.assertLess(patch_image.height,120)
        self.backend.events.clear()
        with self.assertRaises(TargetNotFound):
            flow.replay(Computer(self.backend,self.root/'runtime',timeout=.01,poll_interval=.001))
        self.assertEqual(self.backend.events,[])

    def test_keyboard_step_gaps_survive_manifest_and_generated_python(self):
        clock=[0.]
        recorder=Recorder(self.backend,self.root/'timed',clock=lambda:clock[0])
        session=recorder.wrap(self.inner)
        clock[0]=1.;session.press_key(['enter'])
        clock[0]=2.5;session.type_text('hello')
        recorder.finish()
        flow=Recording.load(self.root/'timed')
        self.assertEqual([step['delay'] for step in flow.steps],[1.,1.5])
        class ReplayTrace:
            def __init__(inner):inner.events=[]
            def __enter__(inner):return inner
            def __exit__(inner,*args):pass
            def sleep(inner,seconds):inner.events.append(('sleep',seconds))
            def hotkey(inner,*keys,**args):inner.events.append(('hotkey',keys))
            def write(inner,text):inner.events.append(('write',text))
        trace=ReplayTrace()
        with patch('wirehand.connect',return_value=trace):
            runpy.run_path(str(self.root/'timed/replay.py'),run_name='__main__')
        self.assertEqual(trace.events,[('sleep',1.),('hotkey',('enter',)),('sleep',1.5),('write','hello')])

    def test_invalid_unanchored_pointer_data_is_rejected_before_input(self):
        self.session.pointer(.2,.3);self.recorder.finish()
        data=Recording.load(self.root/'demo').data
        data['steps'].append({'action':'scroll','args':{'position':[float('nan'),.5],'clicks':1}})
        (self.root/'demo/recording.json').write_text(json.dumps(data))
        self.backend.events.clear()
        with self.assertRaisesRegex(ValueError,'position'):
            Computer(self.backend,self.root/'runtime').replay(self.root/'demo')
        self.assertEqual(self.backend.events,[])
