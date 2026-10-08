"""Preserved-prefix routing and one server-supplied frontend URL base."""
import json
import re
from pathlib import Path
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Router
from app.config import ConfigurationError, Settings


def normalize_prefix(value):
    if not isinstance(value, str):
        raise ConfigurationError('Server URL prefix must be a path')
    value = value.strip().strip('/')
    if not value:
        return ''
    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', value):
        raise ConfigurationError('Server URL prefix must contain safe path segments only')
    return '/' + value


class PrefixMiddleware:
    def __init__(self, app, prefix=None):
        self.app = app
        self.prefix = Settings.from_env().url_prefix if prefix is None else normalize_prefix(prefix)
        self.router = Router(routes=[Mount(self.prefix, app=app)]) if self.prefix else app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket') or not self.prefix:
            return await self.app(scope, receive, send)
        path = scope['path']
        if path == self.prefix and scope['type'] == 'http':
            query = scope.get('query_string', b'').decode('ascii')
            return await RedirectResponse(self.prefix + '/' + ('?' + query if query else ''), status_code=307)(scope,receive,send)
        if not path.startswith(self.prefix + '/'):
            return await Response(status_code=404)(scope,receive,send)
        return await self.router(scope, receive, send)


def page_response(filename, request):
    prefix = request.scope.get('root_path', '')
    text = (Path(__file__).parent / 'static' / filename).read_text(encoding='utf-8')
    text = re.sub(r'(href|src)="/(?!/)', lambda match:match.group(1)+'="'+prefix+'/', text)
    base = json.dumps(prefix)
    from app.ipv import client_timeouts_ms
    settings = Settings.from_env()
    ipv_timeouts = json.dumps(client_timeouts_ms(settings), separators=(',',':'))
    script = '<script>window.ASStat={ipvTimeoutMs:'+ipv_timeouts+',rankingTimeoutMs:'+str(settings.ranking_client_timeout_ms)+',basePath:'+base+',url:function(path){return this.basePath+path;}};</script>'
    text = text.replace('</head>', script + '</head>')
    return HTMLResponse(text, headers={'Cache-Control':'no-cache'})
