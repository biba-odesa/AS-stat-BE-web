import asyncio
import unittest
from app.diagnostics import RequestDiagnosticsMiddleware, cache_event, vm_request


class DiagnosticsTests(unittest.TestCase):
    def test_counts_headers_and_safe_logs(self):
        @vm_request('instant')
        def query():return {'private':'PAYLOAD'}
        async def handler(scope,receive,send):
            cache_event('result','MISS',('http://user:SECRET@host','QUERY'))
            query();query()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        sent=[]
        async def send(message):sent.append(message)
        with self.assertLogs('uvicorn.error',level='INFO') as logs:
            asyncio.run(RequestDiagnosticsMiddleware(handler)({'type':'http','path':'/api/top-asn'},None,send))
        headers=dict(sent[0]['headers'])
        self.assertEqual(headers[b'x-asstat-vm-queries'],b'2')
        self.assertEqual(headers[b'x-asstat-cache'],b'result:MISS')
        for secret in ('SECRET','QUERY','PAYLOAD','user:'):
            self.assertNotIn(secret,' '.join(logs.output))
