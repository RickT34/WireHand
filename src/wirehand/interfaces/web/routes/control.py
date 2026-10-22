"""WebSocket command dispatch with guaranteed control-session cleanup."""
import asyncio
from contextlib import suppress
import logging

from anyio import CancelScope
from starlette.websockets import WebSocketDisconnect

from wirehand.automation.scripts.library import NameConflict


async def control_socket(ws):
    services = ws.app.state.services
    if not services.auth.authenticated(ws) or not services.auth.same_origin(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    owner = object()
    try:
        while services.auth.authenticated(ws):
            message = await asyncio.wait_for(ws.receive_json(), services.control_timeout)
            if not isinstance(message, dict):
                raise ValueError('Invalid message')
            error = ''
            async with services.control_mutex:
                try:
                    kind = message.get('type')
                    if kind == 'ping':
                        pass
                    elif kind == 'take':
                        await services.runner.stop()
                        await asyncio.to_thread(services.controller.claim, owner)
                    elif kind == 'give':
                        await asyncio.to_thread(services.controller.give_back, owner)
                    elif kind == 'record_start':
                        await asyncio.to_thread(services.controller.start_recording, owner, message.get('name'))
                    elif kind == 'record_stop':
                        await asyncio.to_thread(services.controller.stop_recording, owner)
                    else:
                        await asyncio.to_thread(services.controller.action, owner, message)
                except NameConflict as exc:
                    error = str(exc)
                except (OSError, ValueError, RuntimeError) as exc:
                    error = str(exc)
                    if services.controller.owner is owner:
                        await asyncio.to_thread(services.controller.disconnect, owner)
            await ws.send_json({'ok': not error, 'error': error,
                                'controlling': services.controller.owner is owner,
                                'control': services.controller.status(),
                                'run': services.runner.status(),
                                'recording': services.controller.recording_status()})
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, RuntimeError):
        pass
    finally:
        # Starlette may cancel this handler as the socket closes. Complete
        # device/lease cleanup even inside a cancelled AnyIO scope.
        with CancelScope(shield=True):
            async with services.control_mutex:
                errors = await asyncio.to_thread(services.controller.disconnect, owner)
                if errors:
                    logging.getLogger('uvicorn.error').warning('Input release: %s', '; '.join(errors))
        with suppress(RuntimeError, WebSocketDisconnect):
            await ws.close()


