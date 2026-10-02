import asyncio
import unittest
from html.parser import HTMLParser
from unittest.mock import patch
from app.main import app


async def page(path):
    messages = []
    async def receive():
        return {'type':'http.request','body':b'','more_body':False}
    async def send(message):
        messages.append(message)
    await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1',
               'method':'GET','scheme':'http','path':path,'raw_path':path.encode(),
               'query_string':b'','root_path':'','headers':[],
               'server':('test',80),'client':('test',1)},receive,send)
    status = next(m['status'] for m in messages if m['type']=='http.response.start')
    body = b''.join(m.get('body',b'') for m in messages).decode()
    return status, body


class PageMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.scripts = []
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        if tag == 'a': self.links.append(dict(attrs))
        if tag == 'script': self.scripts.append(dict(attrs))


class PageTests(unittest.TestCase):
    def test_pages_and_navigation_without_vm_queries(self):
        with patch('app.main.query_vm') as volumes, patch('app.main.query_series_vm') as series:
            for path, title, active in [('/', 'Top ASN', '/'),('/view-asn','View ASN','/view-asn'),('/link-usage','Link Usage','/link-usage')]:
                with self.subTest(path=path):
                    status, text = asyncio.run(page(path))
                    self.assertEqual(status,200)
                    self.assertIn(f'<title>AS-Stats — {title}</title>',text)
                    self.assertIn('/static/common.css',text)
                    markup = PageMarkup(text)
                    self.assertEqual([a['href'] for a in markup.links],['/','/view-asn','/link-usage'])
                    self.assertEqual([a['href'] for a in markup.links if a.get('aria-current')=='page'],[active])
                    if path == '/':
                        self.assertIn('Total includes traffic counted across all links.',text)
                        self.assertEqual([s['src'] for s in markup.scripts],['/static/asn-metadata.js','/static/svg-images.js?v=20261002-2','/static/top-asn.js?v=20261002-2'])
                        self.assertNotIn('/api/',text)
                    elif path == '/link-usage':
                        self.assertIn('usage-links',text)
                        self.assertEqual([s['src'] for s in markup.scripts],['/static/svg-images.js?v=20261002-2','/static/link-usage.js?v=20261002-2'])
                        self.assertNotIn('asn-metadata.js',text)
                    else:
                        self.assertIn('chart-traffic',text)
                        self.assertEqual([s['src'] for s in markup.scripts],['/static/vendor/echarts/echarts.min.js','/static/asn-metadata.js','/static/app.js'])
            volumes.assert_not_called()
            series.assert_not_called()
