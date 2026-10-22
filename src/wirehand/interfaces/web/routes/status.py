"""Status endpoint aggregating device, video, control and recording state."""
import asyncio

from starlette.responses import JSONResponse


async def status(request):
    services = request.app.state.services
    if not services.auth.authenticated(request):
        return JSONResponse({'error': 'Login required'}, status_code=401)
    try:
        input_status = await asyncio.to_thread(services.backend.input_state)
    except (OSError, RuntimeError) as exc:
        input_status = {'connected': False, 'detail': str(exc)}
    return JSONResponse({'platform': services.backend.name, 'video': {**services.hub.status(),
                        'transport': 'h264-ws' if services.h264 else 'jpeg',
                        'transports': ['h264-ws','webrtc','jpeg'] if services.h264 else ['jpeg']},
                        'input': input_status, 'control': services.controller.status(), 'recording': services.controller.recording_status(),
                        'run': services.runner.status()},
                        headers={'Cache-Control': 'no-store'})


