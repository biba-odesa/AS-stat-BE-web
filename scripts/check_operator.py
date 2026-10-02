"""Small opt-in live comparisons with explicit operator data; no writes to VM."""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.asn_metadata import validate_asn
from app.top_asn import fetch_top
from app.volumes import build_query, query_vm, validate_parameters
from app.series import build_series_query, query_series_vm

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--web-url', default='http://localhost:8000')
parser.add_argument('--vm-url', default='http://127.0.0.1:8428')
parser.add_argument('--mode', choices=('volumes', 'series', 'top', 'link', 'svg', 'metadata'), required=True)
parser.add_argument('--asn', help='ASN present in operator data; documentation example: 64496')
parser.add_argument('--link-id', help='Configured link ID for the one-link check')
parser.add_argument('--timezone', default='UTC')
parser.add_argument('--limit', type=int, default=3)
options = parser.parse_args()
if options.asn:
    try: options.asn = validate_asn(options.asn)
    except ValueError as exc: parser.error(str(exc))
if options.mode in ('volumes', 'series', 'svg', 'metadata') and options.asn is None:
    parser.error('--asn is required; choose one present in your data')
if options.mode == 'link' and not options.link_id:
    parser.error('--link-id is required')
if not 1 <= options.limit <= 3:
    parser.error('Live checks allow only --limit 1..3')
opener = build_opener(ProxyHandler({}))


def get(path, parameters):
    with opener.open(options.web_url.rstrip('/') + path + '?' + urlencode(parameters), timeout=60) as response:
        return json.load(response)


if options.mode == 'top':
    result = get('/api/top-asn', {'limit': options.limit})
    reference = fetch_top(options.vm_url, options.limit, now=result['end'])
    assert result == reference
elif options.mode in ('volumes', 'series'):
    # One complete minute is sufficient for a small comparison.
    _, _, start, end = validate_parameters(options.asn, 'both', None, None)
    start = end - 60
    result = get('/api/asn/' + options.mode, {'asn':options.asn, 'ip_version':'both', 'start':start, 'end':end})
    if options.mode == 'volumes':
        query, instant = build_query(options.asn, 'both', start, end)
        vector = query_vm(options.vm_url, query, instant)
        expected = {(row['metric']['link_id'], row['metric']['direction']):Decimal(row['value'][1]) for row in vector}
        actual = {(row['link_id'], direction):Decimal(row[direction]) for row in result['links']
                  for direction in ('in','out') if row[direction] is not None}
    else:
        vector = query_series_vm(options.vm_url, build_series_query(options.asn, 'both'), start, end)
        expected = {}
        for row in vector:
            for stamp, value in row['values']:
                key = (row['metric']['link_id'], row['metric']['direction'], int(stamp))
                expected[key] = expected.get(key, Decimal(0)) + Decimal(value)
        actual = {(row['link_id'], direction, stamp):Decimal(value)
                  for row in result['links'] for direction in ('in','out')
                  for stamp, value in zip(result['timestamps'], row[direction]) if value is not None}
    assert actual == expected
elif options.mode in ('svg', 'link'):
    period = get('/api/link-usage', {})
    parameters = {'start':period['start'], 'end':period['end'], 'tz':options.timezone}
    if options.mode == 'svg':
        path = '/api/asn/sparkline.svg'
        parameters.update(asn=options.asn, v='5')
    else:
        parameters.update(link_id=options.link_id, v='2')
        bundle = get('/api/link-usage/link', parameters)
        assert bundle['start'] == parameters['start'] and bundle['end'] == parameters['end']
        path = '/api/link-usage/sparkline.svg'
    url = options.web_url.rstrip('/') + path + '?' + urlencode(parameters)
    from xml.etree import ElementTree as ET
    previous = None
    reports = []
    for _ in range(2):
        with opener.open(url, timeout=60) as response:
            data = response.read()
            ET.fromstring(data)
            reports.append({'status':response.status, 'cache':response.headers.get('X-ASStat-Cache'),
                            'vm_queries':response.headers.get('X-ASStat-VM-Queries')})
            if previous is not None: assert data == previous
            previous = data
    print(json.dumps({'requests':reports}))
else:
    # Explicit single-ASN external lookup; this never scans a ranking.
    from app.asn_metadata import cymru_lookup, ripe_lookup
    statuses = {'cymru':cymru_lookup(options.asn)[0], 'ripe':ripe_lookup(options.asn)[0]}
    print(json.dumps({'sources':statuses}))
print(json.dumps({'mode':options.mode, 'result':'checks passed'}))
