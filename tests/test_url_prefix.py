import asyncio
import json
import unittest
from unittest.mock import patch
from app.config import ConfigurationError
from app.url_prefix import normalize_prefix, PrefixMiddleware
from app.sparkline import SVGCache
from app.main import app

async def request(application, path, query=b'', headers=()):
    messages=[]
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    async def send(message):messages.append(message)
    await application({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET',
        'scheme':'http','path':path,'raw_path':path.encode(),'root_path':'','query_string':query,
        'headers':list(headers),'server':('test',80),'client':('test',1)},receive,send)
    start=next(m for m in messages if m['type']=='http.response.start')
    return start['status'],dict(start['headers']),b''.join(m.get('body',b'') for m in messages)

class PrefixTests(unittest.TestCase):
    def test_normalization(self):
        for value, expected in [('', ''), ('/', ''), ('asstat2/','/asstat2'), ('///asstat2///','/asstat2'),('/one/two/','/one/two')]:
            self.assertEqual(normalize_prefix(value),expected)
        for value in ('https://example.net','/a?x=1','/a#x','/../a','/a/..','/a//b','/a%2fb','/a\\b'):
            with self.assertRaises(ConfigurationError):normalize_prefix(value)

    def test_both_modes_pages_static_api_svg_and_redirects(self):
        for prefix in ('','/asstat2'):
            application=PrefixMiddleware(app,prefix=prefix)
            for route in ('/','/view-asn','/link-usage','/ipv'):
                status,headers,body=asyncio.run(request(application,prefix+route))
                self.assertEqual(status,200)
                text=body.decode()
                self.assertIn('basePath:'+json.dumps(prefix),text)
                self.assertIn('href="'+prefix+'/view-asn"',text)
                self.assertIn('href="'+prefix+'/static/favicon.svg"',text)
                self.assertNotIn(prefix+prefix+'/static' if prefix else '//static',text)
            app.openapi_schema = None
            for path in ('/static/common.css','/static/app.js','/static/vendor/echarts/echarts.min.js','/static/favicon.svg','/openapi.json'):
                self.assertEqual(asyncio.run(request(application,prefix+path))[0],200)
            if prefix:
                _,_,schema=asyncio.run(request(application,prefix+'/openapi.json'))
                self.assertIn({'url':prefix},json.loads(schema)['servers'])
            self.assertEqual(asyncio.run(request(application,prefix+'/docs'))[0],404)
            with patch('app.main.parse_knownlinks',return_value=[]):
                self.assertEqual(asyncio.run(request(application,prefix+'/api/links'))[0],200)
            cache=SVGCache()
            with patch('app.main.svg_cache',cache),patch('app.main.load_svg',return_value=b'<svg xmlns="http://www.w3.org/2000/svg"/>') as load:
                params=b'asn=64496&start=120&end=86520&tz=UTC&v=5'
                status,headers,body=asyncio.run(request(application,prefix+'/api/asn/sparkline.svg',params))
                self.assertEqual(status,200);self.assertEqual(headers[b'cache-control'],b'public, max-age=1200')
                status,again,_=asyncio.run(request(application,prefix+'/api/asn/sparkline.svg',params))
                self.assertEqual(status,200);self.assertEqual(again[b'etag'],headers[b'etag']);self.assertEqual(load.call_count,1)
                self.assertEqual(asyncio.run(request(application,prefix+'/api/asn/sparkline.svg',params,[(b'if-none-match',headers[b'etag'])]))[0],304)
            bundle=json.dumps({'svg':'<svg/>','svg_url':'/api/link-usage/sparkline.svg?link_id=example&start=120&end=86520&tz=UTC&v=2','legend':{}}).encode()
            with patch('app.main.usage_bundle',return_value=(bundle,'"fixture"')):
                status,_,body=asyncio.run(request(application,prefix+'/api/link-usage/link',b'link_id=example'))
                self.assertEqual(status,200);self.assertTrue(json.loads(body)['svg_url'].startswith(prefix+'/api/'))
                self.assertEqual(asyncio.run(request(application,prefix+'/api/link-usage/sparkline.svg',b'link_id=example'))[0],200)
            if prefix:
                status,headers,_=asyncio.run(request(application,prefix,b'asn=64496&x=1'))
                self.assertEqual(status,307);self.assertEqual(headers[b'location'],b'/asstat2/?asn=64496&x=1')
                self.assertEqual(asyncio.run(request(application,'/'))[0],404)
