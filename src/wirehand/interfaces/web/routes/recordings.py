"""Script library HTTP adapters: authentication, conflicts and execution dispatch."""
import asyncio
import json
from urllib.parse import quote

from starlette.responses import JSONResponse, Response

from wirehand.automation.scripts.library import MAX_ARCHIVE, NameConflict, validate_name


def authorize(request):
    auth = request.app.state.services.auth
    if not auth.authenticated(request):
        return JSONResponse({'error': '请先登录'}, status_code=401)
    if request.method != 'GET' and not auth.same_origin(request):
        return JSONResponse({'error': 'Origin rejected'}, status_code=403)


def failure(exc):
    if isinstance(exc, NameConflict):
        return JSONResponse({'error': str(exc), 'existing': exc.existing}, status_code=409)
    code = 404 if isinstance(exc, FileNotFoundError) else 409 if isinstance(exc, RuntimeError) else 400
    return JSONResponse({'error': str(exc)}, status_code=code)


def ensure_editable(services, identity):
    recorder = services.controller.recorder
    if recorder and recorder.directory.name == identity:
        raise RuntimeError('请先停止录制')
    if services.runner.active and services.runner.state['script_id'] == identity:
        raise RuntimeError('请先停止此脚本再修改或替换')


async def body(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise ValueError('请求内容过大')
        data.extend(chunk)
    return data


async def json_body(request):
    data = json.loads(await body(request, 4096))
    if not isinstance(data, dict):
        raise ValueError('Expected an object')
    return data


async def scripts(request):
    if denied := authorize(request):
        return denied
    services = request.app.state.services
    library = services.controller.library
    if request.method == 'GET':
        return JSONResponse({'scripts': await asyncio.to_thread(library.list)}, headers={'Cache-Control': 'no-store'})
    try:
        raw = await body(request, MAX_ARCHIVE)
        async with services.control_mutex:
            filename = request.query_params.get('filename', '')
            name = validate_name(filename[:-4])
            # Import can replace by name only after the client confirms the
            # exact current revision; protect running/recording bundles too.
            for item in await asyncio.to_thread(library.list):
                if item['name'].casefold() == name.casefold():
                    ensure_editable(services, item['id'])
            item = await asyncio.to_thread(library.import_zip, raw, filename,
                                          replace_id=request.query_params.get('replace'),
                                          revision=request.query_params.get('revision'))
        return JSONResponse(item, status_code=201)
    except (OSError, ValueError, RuntimeError) as exc:
        return failure(exc)


async def script_item(request):
    if denied := authorize(request):
        return denied
    services = request.app.state.services
    identity = request.path_params['identity']
    try:
        async with services.control_mutex:
            ensure_editable(services, identity)
            if request.method == 'DELETE':
                await asyncio.to_thread(services.controller.library.delete, identity)
                return JSONResponse({'deleted': identity})
            data = await json_body(request)
            item = await asyncio.to_thread(services.controller.library.rename, identity, data.get('name'))
            return JSONResponse(item)
    except (OSError, ValueError, RuntimeError) as exc:
        return failure(exc)


async def recording_download(request):
    if denied := authorize(request):
        return denied
    try:
        services = request.app.state.services
        async with services.control_mutex:
            name, data = await asyncio.to_thread(services.controller.library.export, request.path_params['identity'])
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': f"attachment; filename=script.zip; filename*=UTF-8''{quote(name + '.zip')}",
            'Cache-Control': 'no-store'})
    except (OSError, ValueError, RuntimeError) as exc:
        return failure(exc)


async def run_status(request):
    if denied := authorize(request):
        return denied
    return JSONResponse(request.app.state.services.runner.status(), headers={'Cache-Control': 'no-store'})


async def run_script(request):
    if denied := authorize(request):
        return denied
    services = request.app.state.services
    try:
        data = await json_body(request)
        async with services.control_mutex:
            identity = request.path_params['identity']
            library = services.controller.library
            info = await asyncio.to_thread(library.info, identity)
            state = services.runner.start(library.path(identity), info, data.get('repeat', 1))
        return JSONResponse(state, status_code=202)
    except (OSError, ValueError, RuntimeError) as exc:
        return failure(exc)


async def stop_script(request):
    if denied := authorize(request):
        return denied
    services = request.app.state.services
    async with services.control_mutex:
        return JSONResponse(await services.runner.stop())
