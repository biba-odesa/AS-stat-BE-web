"""Opt-in read-only, one-ASN API/source comparison; never writes VM data."""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.archive_sources import CONTRACTS, source_url
from app.series import query_series_vm
from app.volumes import validate_parameters, query_vm
from app import ipv

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--asn',required=True,help='ASN actually present in operator data; documentation example: 64496')
parser.add_argument('--base-url',default='http://127.0.0.1:8000',help='Direct Python application URL, including configured prefix')
args=parser.parse_args()
asn,_,_,_=validate_parameters(args.asn,'both',None,None)
settings=Settings.from_env();opener=build_opener(ProxyHandler({}))
for period,(duration,step,port) in CONTRACTS.items():
    with opener.open(args.base_url.rstrip('/')+'/api/asn/series?'+urlencode({'asn':asn,'period':period}),timeout=30) as response:
        data=json.load(response)
    params={'asn':asn,'period':period,'start':str(data['start']),'end':str(data['end'])}
    with opener.open(args.base_url.rstrip('/')+'/api/asn/volumes?'+urlencode(params),timeout=30) as response:
        volumes=json.load(response)
    q=f'sum by (link_id,direction) (sum_over_time(asstat_traffic_bytes{{asn="{asn}",ip_version=~"4|6",direction=~"in|out"}}[{duration}s]))'
    vector=query_vm(source_url(settings,period),q,f"{data['end']-1}.999")
    expected_volumes={(r['metric']['link_id'],r['metric']['direction']):Decimal(r['value'][1]) for r in vector}
    actual_volumes={(r['link_id'],d):Decimal(r[d]) for r in volumes['links'] for d in ('in','out') if r[d] is not None}
    assert expected_volumes==actual_volumes
    observations=[(link,d,t,v) for link in data['links'] for d in ('in','out') for t,v in zip(data['timestamps'],link[d]) if v is not None]
    if not observations:
        print(json.dumps({'period':period,'step':step,'asn_points':0,'status':'No ASN data in this source'}),flush=True)
        continue
    a=observations[0][2];b=a+3*step
    q=f'sum by (link_id,direction) (sum_over_time(asstat_traffic_bytes{{asn="{asn}",direction=~"in|out"}}[1ms]))'
    raw=query_series_vm(source_url(settings,period),q,a,b,step=step,evaluation_end=b-step)
    expected={(r['metric']['link_id'],r['metric']['direction'],int(t)):Decimal(v)*8/step for r in raw for t,v in r['values']}
    actual={(r['link_id'],d,t):Decimal(v) for r in data['links'] for d in ('in','out') for t,v in zip(data['timestamps'],r[d]) if a<=t<b and v is not None}
    assert expected.keys()==actual.keys()
    for key,v in expected.items():assert abs(v-actual[key])<=max(Decimal('.000001'),abs(v)*Decimal('1e-12'))
    # Check a small all-ASN IPv window against direct bytes, not a full year.
    numeric=ipv.fetch_data(settings,'compare',a,b,step,period)
    q='sum by (ip_version,direction) (sum_over_time(asstat_traffic_bytes{ip_version=~"4|6",direction=~"in|out"}[1ms]))'
    raw=query_series_vm(source_url(settings,period),q,a,b,step=step,evaluation_end=b-step)
    expected={(r['metric']['ip_version'],r['metric']['direction'],int(t)):Decimal(v)*8/step for r in raw for t,v in r['values']}
    actual={(r['id'],d,t):Decimal(v) for r in numeric['rows'] for d in ('in','out') for t,v in zip(numeric['timestamps'],r[d]) if v is not None}
    assert expected.keys()==actual.keys()
    for key,v in expected.items():assert abs(v-actual[key])<=max(Decimal('.000001'),abs(v)*Decimal('1e-12'))
    print(json.dumps({'period':period,'step':step,'available_asn_values':len(observations),'compared_ipv_points':len(actual),'volumes':'matched','start':a,'end':b,'status':'matched'}),flush=True)
