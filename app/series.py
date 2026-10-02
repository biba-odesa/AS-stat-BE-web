from app.diagnostics import vm_request
import json
import socket
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, localcontext
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

from app.volumes import VMError, decimal_text


def build_series_query(asn, ip_version):
    family = 'ip_version=~"4|6"' if ip_version == 'both' else f'ip_version="{ip_version}"'
    return (f'sum_over_time(asstat_traffic_bytes{{asn="{asn}",{family},'
            'direction=~"in|out"}[1ms]) * 8 / 60')


@vm_request("range")
def query_series_vm(url, query, start, end):
    parameters = urlencode({'query': query, 'start': start, 'end': end - 60, 'step': '60', 'timeout': '5s', 'nocache': '1'})
    # Disable environment proxies for the local VictoriaMetrics endpoint.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url + '/api/v1/query_range?' + parameters, timeout=7) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise VMError('VictoriaMetrics response is too large')
        payload = json.loads(raw)
    except (socket.timeout, TimeoutError) as exc:
        raise VMError('VictoriaMetrics request timed out', 504) from exc
    except HTTPError as exc:
        raise VMError(f'VictoriaMetrics returned HTTP {exc.code}') from exc
    except URLError as exc:
        status = 504 if isinstance(exc.reason, (socket.timeout, TimeoutError)) else 502
        raise VMError('Unable to receive VictoriaMetrics response', status) from exc
    except (OSError, ValueError) as exc:
        raise VMError('Invalid VictoriaMetrics response or connection error') from exc
    if (not isinstance(payload, dict) or payload.get('status') != 'success'
            or not isinstance(payload.get('data'), dict)
            or payload['data'].get('resultType') != 'matrix'
            or not isinstance(payload['data'].get('result'), list)
            or payload.get('isPartial') or payload.get('warnings')):
        raise VMError('VictoriaMetrics returned an error, partial or invalid response')
    return payload['data']['result']



def make_series_response(asn, ip_version, start, end, links, matrix):
    timestamps = list(range(start, end, 60))
    rows = {link.link_id: {**asdict(link), 'in': [None] * len(timestamps),
                           'out': [None] * len(timestamps)} for link in links}
    seen = set()
    try:
        with localcontext() as context:
            context.prec = 100
            for sample in matrix:
                labels = sample['metric']
                link_id, direction, family = labels['link_id'], labels['direction'], labels['ip_version']
                if (not isinstance(link_id, str) or not link_id or direction not in ('in', 'out')
                        or family not in ('4', '6') or (ip_version != 'both' and family != ip_version)):
                    raise ValueError('invalid labels')
                row = rows.setdefault(link_id, {'link_id': link_id, 'name': link_id,
                                               'color': '#808080', 'in': [None] * len(timestamps),
                                               'out': [None] * len(timestamps)})
                for timestamp, raw_value in sample['values']:
                    time_value = Decimal(str(timestamp))
                    if not time_value.is_finite() or time_value != time_value.to_integral_value():
                        raise ValueError('invalid timestamp')
                    timestamp = int(time_value)
                    if not start <= timestamp < end or timestamp % 60:
                        raise ValueError('timestamp outside minute grid')
                    key = (link_id, direction, family, timestamp)
                    if key in seen:
                        raise ValueError('duplicate family sample')
                    seen.add(key)
                    value = Decimal(raw_value)
                    if not value.is_finite() or value < 0:
                        raise ValueError('invalid speed')
                    offset = (timestamp - start) // 60
                    previous = row[direction][offset]
                    row[direction][offset] = value if previous is None else previous + value
            for row in rows.values():
                for direction in ('in', 'out'):
                    row[direction] = [None if value is None else decimal_text(value)
                                      for value in row[direction]]
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
        raise VMError('VictoriaMetrics returned invalid minute series') from exc
    return {'asn': asn, 'ip_version': ip_version, 'start': start, 'end': end,
            'step': 60, 'unit': 'bit/s', 'timestamps': timestamps,
            'links': [rows[key] for key in sorted(rows)]}
