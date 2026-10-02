import re
import time
from decimal import Decimal, InvalidOperation, localcontext

from app.periods import ranking_period
from app.volumes import ParameterError, VMError, decimal_text, query_vm


def validate_limit(limit):
    if not re.fullmatch(r'[0-9]{1,3}', limit) or not 1 <= int(limit) <= 300:
        raise ParameterError('Top must be a whole number from 1 to 300')
    return int(limit)


def parse_asn(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}', value) or int(value) > 4294967295:
        raise ValueError('invalid ASN label')
    return str(int(value))


def parse_value(sample):
    value = Decimal(sample['value'][1])
    if not value.is_finite() or value < 0:
        raise ValueError('invalid volume')
    return value


def detail_query(asns):
    pattern = '^(' + '|'.join(re.escape(asn) for asn in asns) + ')$'
    return ('sum by (asn, direction) (sum_over_time('
            f'asstat_traffic_bytes{{asn=~"{pattern}"}}[86400s]))')


def ranking_response(limit, start, end, top, directions):
    try:
        rows = {}
        for sample in top:
            asn = parse_asn(sample['metric']['asn'])
            parse_value(sample)
            if asn in rows or len(rows) >= limit:
                raise ValueError('duplicate or excess ranked ASN')
            rows[asn] = {'asn':asn, 'in':None, 'out':None}
        with localcontext() as context:
            context.prec = 100
            for sample in directions:
                asn = parse_asn(sample['metric']['asn'])
                direction = sample['metric']['direction']
                if asn not in rows or direction not in ('in','out') or rows[asn][direction] is not None:
                    raise ValueError('unexpected or duplicate direction')
                rows[asn][direction] = parse_value(sample)
            for row in rows.values():
                values = [row[d] for d in ('in','out') if row[d] is not None]
                row['total'] = sum(values,Decimal(0)) if values else None
            ordered = sorted(rows.values(), key=lambda row: (row['total'] is None,
                            -(row['total'] or Decimal(0)), int(row['asn'])))
            for rank, row in enumerate(ordered,1):
                row['rank'] = rank
                for field in ('in','out','total'):
                    row[field] = None if row[field] is None else decimal_text(row[field])
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
        raise VMError('Invalid ranking response from VictoriaMetrics') from exc
    return {'limit':limit,'start':start,'end':end,'rows':ordered}


def fetch_top(url, limit, now=None):
    start, end = ranking_period(now)
    instant = f'{end-1}.999'
    top = query_vm(url, f'topk({limit}, sum by (asn) (sum_over_time(asstat_traffic_bytes[86400s])))', instant)
    try:
        asns = [parse_asn(sample['metric']['asn']) for sample in top]
        if len(set(asns)) != len(asns) or len(asns) > limit:
            raise ValueError('duplicate or excess ranked ASN')
    except (KeyError, TypeError, ValueError) as exc:
        raise VMError('Invalid ranking response from VictoriaMetrics') from exc
    directions = query_vm(url, detail_query(asns), instant) if asns else []
    return ranking_response(limit,start,end,top,directions)
