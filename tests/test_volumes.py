import asyncio
import json
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from app.knownlinks import Link
from app.main import app
from app.volumes import (ParameterError, VMError, validate_parameters,
                         build_query, query_vm, make_response)


async def request(params):
    messages = []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        messages.append(message)
    await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
               'method': 'GET', 'scheme': 'http', 'path': '/api/asn/volumes',
               'raw_path': b'/api/asn/volumes', 'query_string': params.encode(),
               'root_path': '', 'headers': [], 'server': ('test', 80),
               'client': ('test', 123)}, receive, send)
    status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
    body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return status, json.loads(body)


class VolumesTests(unittest.TestCase):
    def test_default_and_boundaries(self):
        self.assertEqual(validate_parameters('000', 'both', None, None, now=180061),
                         ('0', 'both', 97260, 180060))
        self.assertEqual(validate_parameters('4294967295', '6', '120', '180', now=180)[0], '4294967295')
        validate_parameters('0', '4', '0', str(7*86400), now=7*86400)

    def test_invalid_parameters_http_422(self):
        cases = ['', 'asn=-1', 'asn=4294967296', 'asn=1.0', 'asn=1e2', 'asn=abc',
                 'asn=1&ip_version=5', 'asn=1&start=120', 'asn=1&end=180',
                 'asn=1&start=121&end=180', 'asn=1&start=180&end=120',
                 'asn=1&start=120&end=120', 'asn=1&start=0&end=604860',
                 'asn=1&start=120&end=999999999960', 'asn=1&start=120.0&end=180']
        for params in cases:
            with self.subTest(params=params):
                status, _ = asyncio.run(request(params))
                self.assertEqual(status, 422)

    def test_query_window_and_instant(self):
        query, instant = build_query('64496', 'both', 120, 240)
        self.assertEqual(query, 'sum by (link_id, direction) (sum_over_time(asstat_traffic_bytes{asn="64496",ip_version=~"4|6",direction=~"in|out"}[120s]))')
        self.assertEqual(instant, '239.999')
        self.assertIn('ip_version="6"', build_query('0', '6', 120, 180)[0])

    def test_missing_zero_history_and_totals(self):
        vector = [{'metric': {'link_id': 'a', 'direction': 'in'}, 'value': [0, '0']},
                  {'metric': {'link_id': 'historic', 'direction': 'out'}, 'value': [0, '9007199254740993']}]
        data = make_response('1', 'both', 120, 180, [Link('a','A','#112233'), Link('idle-example','Idle Example','#445566')], vector)
        rows = {r['link_id']: r for r in data['links']}
        self.assertEqual(rows['a']['in'], '0')
        self.assertIsNone(rows['a']['out'])
        self.assertIsNone(rows['idle-example']['in'])
        self.assertEqual(rows['historic']['color'], '#808080')
        self.assertEqual(data['totals'], {'in': '0', 'out': '9007199254740993'})
        empty = make_response('1','4',120,180,[],[])
        self.assertEqual(empty['totals'], {'in':None,'out':None})

    def test_vm_http_network_timeout_errors(self):
        for error, status in [(HTTPError('url',503,'error',{},None),502),
                              (URLError('offline'),502), (TimeoutError(),504)]:
            with self.subTest(error=error), patch('app.volumes.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(VMError) as caught:
                    query_vm('http://127.0.0.1:8428','query','179.999')
                self.assertEqual(caught.exception.status_code, status)

    def test_vm_response_validation_and_proxy_timeout(self):
        payloads = [b'invalid', b'{"status":"error"}',
                    b'{"status":"success","data":{"resultType":"matrix","result":[]}}']
        for payload in payloads:
            with patch('app.volumes.build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = payload
                with self.assertRaises(VMError): query_vm('http://127.0.0.1:8428','q','179.999')
        with patch('app.volumes.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"status":"success","data":{"resultType":"vector","result":[]}}'
            self.assertEqual(query_vm('http://127.0.0.1:8428','q','179.999'), [])
            self.assertEqual(opener.call_args.args[0].proxies, {})
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],7)

    def test_bad_samples_rejected(self):
        for value in ('NaN', 'Inf', '-1'):
            with self.assertRaises(VMError):
                make_response('1','4',120,180,[],[{'metric':{'link_id':'a','direction':'in'},'value':[0,value]}])

    def test_api_success_and_vm_failure(self):
        with patch('app.main.parse_knownlinks', return_value=[Link('idle-example','Idle Example','#112233')]), patch('app.main.query_vm', return_value=[]) as query:
            status, data = asyncio.run(request('asn=64496&start=120&end=240'))
            self.assertEqual(status, 200)
            self.assertEqual(data['start'],120)
            self.assertEqual(data['end'],240)
            self.assertEqual(data['links'][0]['link_id'],'idle-example')
            self.assertEqual(query.call_args.args[2], '239.999')
        with patch('app.main.parse_knownlinks', return_value=[]), patch('app.main.query_vm', side_effect=VMError('timeout',504)):
            status, data = asyncio.run(request('asn=1&start=120&end=180'))
            self.assertEqual(status,504)
            self.assertIn('period 1d',data['detail'])
