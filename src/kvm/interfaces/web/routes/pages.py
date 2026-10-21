"""Console pages and public static assets."""
from pathlib import Path

from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from ..auth import LOGIN

STATIC = Path(__file__).resolve().parents[1] / 'static'


async def index(request):
    services = request.app.state.services
    if not services.auth.authenticated(request):
        return HTMLResponse(LOGIN.replace('{error}', ''), headers={'Cache-Control': 'no-store'})
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})


async def static_file(request):
    name = request.path_params['name']
    if name not in ('style.css', 'app.js', 'h264.js'):
        return JSONResponse({'error': 'Not found'}, status_code=404)
    return FileResponse(STATIC / name, headers={'Cache-Control': 'no-cache'})


