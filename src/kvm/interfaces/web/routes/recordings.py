"""Authenticated download of completed or interrupted recordings."""
import asyncio
import re

from starlette.responses import JSONResponse, Response

from kvm.automation.recording.archive import export_archive


async def recording_download(request):
    services = request.app.state.services
    if not services.auth.authenticated(request):
        return JSONResponse({'error':'Login required'},status_code=401)
    identity = request.path_params['identity']
    if not re.fullmatch('[a-f0-9]{32}',identity):
        return JSONResponse({'error':'Not found'},status_code=404)
    directory = services.controller.recordings_dir/identity
    try:
        data = await asyncio.to_thread(export_archive, directory)
    except FileNotFoundError:
        return JSONResponse({'error':'Not found'},status_code=404)
    except ValueError as exc:
        return JSONResponse({'error':str(exc)},status_code=409)
    return Response(data,media_type='application/zip',headers={
        'Content-Disposition':f'attachment; filename="kvm-recording-{identity}.zip"',
        'Cache-Control':'no-store'})


