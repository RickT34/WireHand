"""Console pages and public static assets."""
from pathlib import Path

from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from ..auth import LOGIN

STATIC = Path(__file__).resolve().parents[1] / 'static'


async def index(request):
    services = request.app.state.services
    if not services.auth.authenticated(request):
        return HTMLResponse(LOGIN.replace('{error}', ''), headers={'Cache-Control': 'no-store'})
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})


async def static_file(request):
    name = request.path_params['name']
    if name not in ('style.css', 'app.js', 'h264.js', 'scripts.js'):
        return JSONResponse({'error': 'Not found'}, status_code=404)
    return FileResponse(STATIC / name, headers={'Cache-Control': 'no-cache'})



async def docs_redirect(request):
    return RedirectResponse('/docs/wirehand/factory.html')


async def documentation(request):
    services = request.app.state.services
    if not services.auth.authenticated(request):
        return RedirectResponse('/')
    name = request.path_params['path']
    if not name:
        return RedirectResponse('/docs/wirehand/factory.html')
    try:
        directory = await services.documentation.directory()
    except (OSError, RuntimeError, TimeoutError) as exc:
        return JSONResponse({'error': str(exc)}, status_code=503)
    path = (directory / name).resolve()
    if not path.is_relative_to(directory) or not path.is_file() or path.suffix not in ('.html', '.js', '.css', '.svg', '.json', '.woff2'):
        return JSONResponse({'error': 'Not found'}, status_code=404)
    return FileResponse(path, headers={'Cache-Control': 'no-cache'})
