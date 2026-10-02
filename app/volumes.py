from app.diagnostics import vm_request
import json
import re
import socket
import time
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, localcontext
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener


class ParameterError(ValueError):
    pass


class VMError(RuntimeError):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def validate_parameters(asn, ip_version, start, end, now=None):
    if not re.fullmatch(r'[0-9]{1,10}', asn) or int(asn) > 4294967295:
        raise ParameterError('ASN must be a decimal number from 0 to 4294967295')
    if ip_version not in ('4', '6', 'both'):
        raise ParameterError('IP family must be 4, 6 or both')
    current_minute = int(time.time() if now is None else now) // 60 * 60
    if (start is None) != (end is None):
        raise ParameterError('Start and end must be provided together')
    if start is None:
        start, end = current_minute - 23 * 3600, current_minute
    else:
        if not all(re.fullmatch(r'[0-9]{1,12}', value) for value in (start, end)):
            raise ParameterError('Start and end must be whole Unix timestamps in UTC seconds')
        start, end = int(start), int(end)
    if start % 60 or end % 60:
        raise ParameterError('Start and end must be aligned to full minutes')
    if start >= end or end - start > 7 * 86400:
        raise ParameterError('Start must precede end and the period must not exceed 7 days')
    if end > current_minute:
        raise ParameterError('End must not be later than the start of the current minute')
    return str(int(asn)), ip_version, start, end


def build_query(asn, ip_version, start, end):
    family = 'ip_version=~"4|6"' if ip_version == 'both' else f'ip_version="{ip_version}"'
    query = (f'sum by (link_id, direction) (sum_over_time('
             f'asstat_traffic_bytes{{asn="{asn}",{family},direction=~"in|out"}}[{end-start}s]))')
    return query, f'{end-1}.999'


@vm_request("instant")
def query_vm(url, query, instant):
    parameters = urlencode({'query': query, 'time': instant, 'timeout': '5s', 'nocache': '1'})
    # Disable environment proxies for the local VictoriaMetrics endpoint.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url + '/api/v1/query?' + parameters, timeout=7) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
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
            or payload['data'].get('resultType') != 'vector'
            or not isinstance(payload['data'].get('result'), list)
            or payload.get('isPartial') or payload.get('warnings')):
        raise VMError('VictoriaMetrics returned an error, partial or invalid response')
    return payload['data']['result']


def decimal_text(value):
    return format(value, 'f')


def make_response(asn, ip_version, start, end, links, vector):
    rows = {link.link_id: {**asdict(link), 'in': None, 'out': None} for link in links}
    totals = {'in': None, 'out': None}
    try:
        with localcontext() as context:
            context.prec = 100
            for sample in vector:
                metric = sample['metric']
                link_id, direction = metric['link_id'], metric['direction']
                if not isinstance(link_id, str) or not link_id or direction not in totals:
                    raise ValueError('invalid labels')
                value = Decimal(sample['value'][1])
                if not value.is_finite() or value < 0:
                    raise ValueError('invalid bytes')
                row = rows.setdefault(link_id, {'link_id': link_id, 'name': link_id,
                                               'color': '#808080', 'in': None, 'out': None})
                if row[direction] is not None:
                    raise ValueError('duplicate aggregated series')
                row[direction] = decimal_text(value)
                totals[direction] = value if totals[direction] is None else totals[direction] + value
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
        raise VMError('VictoriaMetrics returned invalid values or labels') from exc
    return {'asn': asn, 'ip_version': ip_version, 'start': start, 'end': end,
            'links': [rows[key] for key in sorted(rows)],
            'totals': {key: None if value is None else decimal_text(value)
                       for key, value in totals.items()}}
