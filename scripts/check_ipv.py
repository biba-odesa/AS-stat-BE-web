"""Opt-in one-hour IPv comparison and persistent-cache restart check; no VM writes."""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import ipv
from app.config import Settings
from app.series import query_series_vm

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cached',nargs=2,type=int,metavar=('START','END'),help=argparse.SUPPRESS)
options=parser.parse_args()
settings=Settings.from_env()
if options.cached:
    a,b=options.cached
    with patch('app.ipv.query_series_vm',side_effect=AssertionError('Unexpected VM call after process restart')), patch('app.ipv.query_vm',side_effect=AssertionError('Unexpected discovery after process restart')):
        image=ipv.get_svg(settings,'compare','1d',a,b,60,'UTC','in')
    print(hashlib.sha256(image).hexdigest())
    raise SystemExit(0)
b=int(time.time())//3600*3600;a=b-3600
result,_=ipv.get_data(settings,'compare','1d',a,b,60)
# Independent verified 1ms rollup, one hour and only four aggregated series.
query='sum by (ip_version, direction) (sum_over_time(asstat_traffic_bytes{ip_version=~"4|6",direction=~"in|out"}[1ms]) * 8 / 60)'
raw=query_series_vm(settings.victoriametrics_url,query,a,b)
expected={(r['metric']['ip_version'],r['metric']['direction'],int(t)):Decimal(v) for r in raw for t,v in r['values']}
actual={(r['id'],d,t):Decimal(v) for r in result['rows'] for d in ('in','out') for t,v in zip(result['timestamps'],r[d]) if v is not None}
assert actual.keys()==expected.keys()
for key,value in actual.items():assert abs(value-expected[key])<=max(Decimal('.000001'),abs(expected[key])*Decimal('1e-12'))
image=ipv.get_svg(settings,'compare','1d',a,b,60,'UTC','in')
checksum=hashlib.sha256(image).hexdigest()
again=subprocess.check_output([sys.executable,'-B',__file__,'--cached',str(a),str(b)],text=True).strip()
assert again==checksum
print(json.dumps({'start':a,'end':b,'period_seconds':3600,'compared_values':len(actual),'minute_source_points':'matched','new_process_vm_queries':0,'persistent_svg':'matched'}))
