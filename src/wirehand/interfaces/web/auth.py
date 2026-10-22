"""Cookie authentication, same-origin checks and login/logout endpoints."""
import asyncio
import hashlib
import hmac
import time
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

LOGIN = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · WireHand 接手</title>
<link rel="stylesheet" href="/style.css"><body class="login-page"><main class="login-card">
<div class="brand-mark">W</div><p class="eyebrow">WIREHAND · 接手</p>
<h1>接上线，就接手。</h1><p class="muted">登录 WireHand，查看设备画面并远程操作。</p>
<form action="/login" method="post"><label for="token">访问密码</label>
<input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
<button class="primary" type="submit">打开控制台 <span>→</span></button></form>
<p class="muted small">{error}</p></main></body></html>'''


class SessionAuth:
    def __init__(self, token):
        if len(token) < 20:
            raise ValueError('KVM_TOKEN must contain at least 20 characters')
        self.token = token

    def signature(self, value):
        return hmac.new(self.token.encode(), value.encode(), hashlib.sha256).hexdigest()


    def authenticated(self, connection):
        cookie = connection.cookies.get('kvm_session', '')
        try:
            timestamp, digest = cookie.split('.', 1)
            age = time.time() - int(timestamp)
            return 0 <= age < 43200 and hmac.compare_digest(digest, self.signature(timestamp))
        except (ValueError, TypeError):
            return False


    def same_origin(self, connection):
        origin = connection.headers.get('origin', '')
        scheme = 'https' if connection.url.scheme in ('https', 'wss') else 'http'
        return origin == f'{scheme}://{connection.headers.get("host", "")}'


    async def login(self, request: Request):
        if not self.same_origin(request):
            return JSONResponse({'error': 'Origin rejected'}, status_code=403)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 2048:
                return JSONResponse({'error': 'Request too large'}, status_code=413)
        candidate = parse_qs(data.decode(errors='replace')).get('token', [''])[0]
        if not hmac.compare_digest(candidate.encode(), self.token.encode()):
            await asyncio.sleep(.3)
            return HTMLResponse(LOGIN.replace('{error}', '密码不正确，请重试。'), status_code=401)
        timestamp = str(int(time.time()))
        response = RedirectResponse('/', status_code=303)
        response.set_cookie('kvm_session', timestamp + '.' + self.signature(timestamp),
                            max_age=43200, httponly=True, samesite='strict',
                            secure=request.url.scheme == 'https')
        return response


    async def logout(self, request):
        if not self.same_origin(request):
            return JSONResponse({'error': 'Origin rejected'}, status_code=403)
        response = RedirectResponse('/', status_code=303)
        response.delete_cookie('kvm_session')
        return response


