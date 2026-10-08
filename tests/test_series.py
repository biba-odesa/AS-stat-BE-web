import asyncio
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from urllib.error import URLError, HTTPError

from app.series import build_series_query, query_series_vm, make_series_response
from app.volumes import VMError
from app.knownlinks import Link
from test_volumes import request


def sample(family, values, direction='in', link_id='a'):
    return {'metric':{'link_id':link_id,'direction':direction,'ip_version':family},'values':values}


class SeriesTests(unittest.TestCase):
    def test_grid_gap_zero_and_family_merge(self):
        matrix = [sample('4', [[120,'0'],[240,'10']]), sample('6', [[240,'2'],[300,'3']])]
        data = make_series_response('64496','both',120,360,[Link('a','A','#112233'),Link('idle-example','Idle Example','#445566')],matrix)
        self.assertEqual(data['timestamps'], [120,180,240,300])
        self.assertEqual(data['links'][0]['in'], ['0',None,'12','3'])
        self.assertEqual(data['links'][0]['out'], [None]*4)
        self.assertEqual(data['links'][1]['in'], [None]*4)

    def test_history_and_empty(self):
        result = make_series_response('1','4',120,180,[],[sample('4',[[120,'1']],link_id='historic')])
        self.assertEqual(result['links'][0]['name'],'historic')
        self.assertEqual(result['links'][0]['color'],'#808080')
        self.assertEqual(make_series_response('1','4',120,180,[],[])['timestamps'],[120])

    def test_query_window_parameters_and_proxy(self):
        query = build_series_query('64496','both')
        self.assertEqual(query,'sum_over_time(asstat_traffic_bytes{asn="64496",ip_version=~"4|6",direction=~"in|out"}[1ms]) * 8 / 60')
        with patch('app.series.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"status":"success","data":{"resultType":"matrix","result":[]}}'
            self.assertEqual(query_series_vm('http://127.0.0.1:8428',query,120,360),[])
            args = parse_qs(urlsplit(opener.return_value.open.call_args.args[0]).query)
            self.assertEqual((args['start'],args['end'],args['step']),(['120'],['300'],['60']))
            self.assertEqual(args['query'],[query])
            self.assertEqual(opener.call_args.args[0].proxies,{})
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],7)

    def test_errors(self):
        for error, status in [(TimeoutError(),504),(URLError('offline'),502),(HTTPError('url',500,'error',{},None),502)]:
            with patch('app.series.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(VMError) as caught: query_series_vm('url','q',120,180)
                self.assertEqual(caught.exception.status_code,status)
        for raw in (b'invalid', b'{"status":"error"}', b'{"status":"success","data":{"resultType":"vector","result":[]}}'):
            with patch('app.series.build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(VMError): query_series_vm('url','q',120,180)

    def test_invalid_samples(self):
        for matrix in ([sample('4',[[121,'1']])], [sample('4',[[120,'NaN']])],
                       [sample('4',[[120,'1'],[120,'2']])], [sample('6',[[120,'1']])]):
            with self.assertRaises(VMError): make_series_response('1','4',120,180,[],matrix)

    def test_endpoint_validation_and_errors(self):
        # Reuse the ASGI helper while changing its route for this endpoint.
        import test_volumes
        original_app = test_volumes.app
        async def series_app(scope, receive, send):
            scope['path'] = '/api/asn/series'
            scope['raw_path'] = b'/api/asn/series'
            await original_app(scope,receive,send)
        with patch('test_volumes.app',series_app):
            self.assertEqual(asyncio.run(request('asn=-1'))[0],422)
            with patch('app.main.parse_knownlinks',return_value=[]), patch('app.main.query_series_vm',return_value=[]):
                status, data = asyncio.run(request('asn=1&start=120&end=180'))
                self.assertEqual(status,200)
                self.assertEqual(data['timestamps'],[120])
            with patch('app.main.parse_knownlinks',return_value=[]), patch('app.main.query_series_vm',side_effect=VMError('timeout',504)):
                self.assertEqual(asyncio.run(request('asn=1&start=120&end=180'))[0],504)
