import json
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from app.config import Settings
from app.ipv import client_timeouts_ms
from app.series import query_series_vm


class IPvTimeoutTests(unittest.TestCase):
    def test_frontend_budget_covers_all_sequential_blocks(self):
        settings=Settings('http://localhost:8428',Path('fixture'))
        self.assertEqual(client_timeouts_ms(settings),{'1d':860000,'1w':1180000,'1m':860000,'1y':2428000})
        self.assertEqual(client_timeouts_ms(replace(settings,ipv_vm_timeout=40))['1w'],1530000)
        self.assertEqual(settings.metadata_http_timeout,10)
        self.assertEqual(settings.ranking_client_timeout_ms,74000)

    def test_range_timeout_is_optional_and_proxy_free(self):
        with patch('app.series.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps({'status':'success','data':{'resultType':'matrix','result':[]}}).encode()
            query_series_vm('http://localhost:8428','q',60,180)
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],7)
            for step in (60,300,1800,7200):
                query_series_vm('http://localhost:8428','q',0,step*3,step=step,evaluation_end=step*2,timeout_seconds=30)
                call=opener.return_value.open.call_args
                params=parse_qs(urlsplit(call.args[0]).query)
                self.assertEqual(params['timeout'],['30s'])
                self.assertEqual(params['step'],[str(step)])
                self.assertEqual(call.kwargs['timeout'],32)
                self.assertEqual(opener.call_args.args[0].proxies,{})
