"""Live acceptance for this project's desktop; --recreate also checks persistence."""
import argparse
import asyncio
import base64
import io
import json
from pathlib import Path
import subprocess
import uuid

from dotenv import dotenv_values
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from websockets.asyncio.client import connect


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'deploy/docker-desktop/.env'
COMPOSE = ['docker', 'compose', '--env-file', str(CONFIG), '-f',
           str(ROOT / 'deploy/docker-desktop/compose.yaml')]


def container_python(source):
    subprocess.run([*COMPOSE, 'exec', '-T', '--user', 'agent', 'desktop', 'python', '-'],
                   input=source, text=True, check=True, timeout=45)


async def interfaces(origin, token):
    params = StdioServerParameters(command=str(ROOT / 'desktopctl'), args=['mcp'])
    async with stdio_client(params) as (reader, writer), httpx.AsyncClient(base_url=origin, trust_env=False) as http:
        async with ClientSession(reader, writer) as mcp:
            await mcp.initialize()
            result = await mcp.call_tool('status', {})
            assert not result.isError, result
            state = json.loads(result.content[0].text)
            assert state['platform'] == 'docker-desktop' and state['input']['connected'], state
            result = await mcp.call_tool('screenshot', {'max_width': 640})
            assert not result.isError, result
            with Image.open(io.BytesIO(base64.b64decode(result.content[0].data))) as frame:
                assert frame.width == 640
            assert (await http.get('/api/status')).status_code == 401
            response = await http.post('/login', data={'token': token}, headers={'Origin': origin})
            assert response.status_code == 303, response.status_code
            status = (await http.get('/api/status')).json()
            assert status['video']['transport'] == 'jpeg', status
            cookie = '; '.join(f'{name}={value}' for name, value in http.cookies.items())
            headers = {'Cookie': cookie}
            socket_origin = origin.replace('http://', 'ws://', 1)
            async with connect(f'{socket_origin}/ws/video', origin=origin,
                               additional_headers=headers, proxy=None) as video:
                for _ in range(3):
                    await video.send('next')
                    frame = await asyncio.wait_for(video.recv(), 5)
                    assert isinstance(frame, bytes), frame
                    with Image.open(io.BytesIO(frame)) as image:
                        image.load()
                        assert image.width <= 1280
            async with connect(f'{socket_origin}/ws/control', origin=origin,
                               additional_headers=headers, proxy=None) as control:
                async def action(message):
                    await control.send(json.dumps(message))
                    return json.loads(await asyncio.wait_for(control.recv(), 5))

                result = await action({'type': 'key', 'code': 'Escape', 'down': True})
                assert not result['ok'] and not result['controlling'], result
                result = await action({'type': 'take'})
                assert result['ok'] and result['controlling'], result
                denied = await mcp.call_tool('press_key', {'keys': ['esc']})
                assert denied.isError and 'manual control' in denied.content[0].text, denied
                result = await action({'type': 'record_start'})
                assert result['ok'] and result['recording']['active'], result
                for down in (True, False):
                    result = await action({'type': 'key', 'code': 'Escape', 'down': down})
                    assert result['ok'], result
                result = await action({'type': 'record_stop'})
                assert result['ok'] and not result['recording']['active'], result
                recording_id = result['recording']['last']['id']
                download = await http.get(f'/api/recordings/{recording_id}/download')
                assert download.status_code == 200 and download.content.startswith(b'PK'), download.status_code
                result = await action({'type': 'give'})
                assert result['ok'] and not result['controlling'], result
                result = await mcp.call_tool('press_key', {'keys': ['esc']})
                assert not result.isError, result
            print('PASS: real MCP screenshot/input, HTTP login, JPEG video, human exclusion, recording download')
            return recording_id


def persistence(recording_id, recreate):
    identity = uuid.uuid4().hex
    paths = [f'{base}/.kvm-persistence-{identity}' for base in
             ('/home/agent', '/workspace', '/var/lib/kvm-mcp')]
    container_python(f'''from pathlib import Path
for name in {paths!r}:
    Path(name).write_text({identity!r})
''')
    if recreate:
        subprocess.run([*COMPOSE, 'up', '-d', '--force-recreate', '--wait', 'desktop'],
                       check=True, timeout=180)
    container_python(f'''from pathlib import Path
import shutil
for name in {paths!r}:
    assert Path(name).read_text() == {identity!r}, name
recording = Path('/var/lib/kvm-mcp/recordings') / {recording_id!r}
assert recording.is_dir() and any(recording.iterdir()), recording
for name in {paths!r}:
    Path(name).unlink()
shutil.rmtree(recording)
''')
    print('PASS: home/workspace/state and recorded data' + (' survive container replacement' if recreate else ' are writable'))


async def main(recreate):
    values = dotenv_values(CONFIG)
    token = values['KVM_TOKEN']
    host = values.get('KVM_DESKTOP_BIND', '127.0.0.1')
    if host == '0.0.0.0':
        host = '127.0.0.1'
    origin = f'http://{host}:{values.get("KVM_DESKTOP_PORT", "18810")}'
    source = (ROOT / 'tests/platforms/test_docker_desktop.py').read_text()
    container_python("import os\nos.environ['KVM_DESKTOP_LIVE_TEST'] = '1'\n" + source)
    recording_id = await interfaces(origin, token)
    persistence(recording_id, recreate)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recreate', action='store_true', help='Replace this desktop container to verify data survival')
    args = parser.parse_args()
    asyncio.run(main(args.recreate))
