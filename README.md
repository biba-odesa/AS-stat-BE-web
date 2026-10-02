# AS-stat-BE-web

A web interface for [AS-stat-BE](https://github.com/biba-odesa/AS-stat-BE).
The application reads traffic data through the VictoriaMetrics HTTP API.
It uses Python with FastAPI/Uvicorn, JavaScript with locally bundled Apache
ECharts 5.6.0, and server-generated SVG images. The interface has a dark theme.

Pages:

- **Top ASN** (`/`): ranked traffic totals and static per-ASN SVG graphs.
- **View ASN** (`/view-asn`): interactive input/output traffic by link, filters,
  ASN metadata and external resource links.
- **Link Usage** (`/link-usage`): per-link SVG graphs with fixed input/output
  top-10 ASN sets and direction-specific Others.

[Original AS-Stats](https://github.com/manuelkasper/AS-Stats) is the source of
the project idea. Its code is not described as copied or derived without
evidence of an actual transfer.

[as-stats-gui](https://github.com/nidebr/as-stats-gui) was consulted as a source
of **visual inspiration** during interface design. This attribution does not
assert a fork or code derivation. No copied application code from that project
was identified in the publication review. Apache ECharts is bundled under its
own Apache-2.0 license; its original LICENSE, NOTICE and distribution banner
are retained in `app/static/vendor/echarts/`. The new application code is licensed under **BSD-2-Clause**; see the root
`LICENSE`. Third-party materials retain their own licenses and notices.

## Installation on Debian 13

VictoriaMetrics and the AS-stat-BE collector must already be deployed by the
operator. These instructions install only the web application. Run the first
block as root on a clean Debian 13 host:

```sh
apt-get update
apt-get install -y sudo python3 python3-venv git ca-certificates curl
useradd --system --user-group --home-dir /nonexistent --no-create-home \
  --shell /usr/sbin/nologin asstat-web
git clone https://github.com/biba-odesa/AS-stat-BE-web.git /opt/as-stat-be-web
install -d -o asstat-web -g asstat-web -m 0750 \
  /opt/as-stat-be-web/.venv /opt/as-stat-be-web/data
```

Keep source files owned by the deploying administrator. Only the virtual
environment and application data directory need to be writable by the service
account. Create the environment and install Python dependencies without
`sudo pip` or system-wide Python package installation:

```sh
cd /opt/as-stat-be-web
sudo -u asstat-web python3 -m venv .venv
sudo -u asstat-web .venv/bin/python -m pip install -r requirements.txt
sudo install -o root -g asstat-web -m 0640 \
  as-stat-web.conf.example as-stat-web.conf
sudo install -d -o root -g asstat-web -m 0750 /etc/as-stat-be-web
sudo install -o root -g asstat-web -m 0640 \
  deploy/knownlinks.example /etc/as-stat-be-web/knownlinks
sudoedit /opt/as-stat-be-web/as-stat-web.conf
```

The installed knownlinks file is a **fictional documentation fixture**. Before
production use, replace it with an operator-approved read-only copy of the
collector mapping, or point the configuration at an existing readable mapping.
Do not change ownership of collector files. The service needs read permission
on the file and traversal permission on its parent directories; an operator
can grant access through a dedicated group or narrowly scoped ACL. A copied
mapping must be updated by the operator when the original changes.

## Configuration

`as-stat-web.conf` is the only primary configuration file; `.env` is not loaded.
Copy and edit the supplied INI example. Standard `configparser` is used with
`interpolation=None`, so percent signs in URLs/passwords remain literal.
Explicit process environment settings override INI values, which override
built-in defaults. Relative paths resolve against the project root, regardless
of the launch directory.

```ini
[server]
host = 127.0.0.1
port = 8000

[victoriametrics]
url = http://127.0.0.1:8428

[links]
knownlinks = /etc/as-stat-be-web/knownlinks

[proxy]
# Optional external HTTP proxy; no credentials in the example.
# HTTP_PROXY = http://proxy.example.net:3128
# HTTPS_PROXY = http://proxy.example.net:3128
# NO_PROXY = localhost,127.0.0.1,::1

[asn_metadata]
http_timeout_seconds = 10

[svg]
cache_ttl_seconds = 1200

[web_cache]
path = data/web-cache
ttl_seconds = 1200
max_size_bytes = 134217728
```

The current application deliberately validates the VictoriaMetrics address as
`http://127.0.0.1:8428`: deploy it on the same host. This setting does not support
a remote VM server. VictoriaMetrics calls always bypass HTTP proxies, even if
proxy environment variables are present. Finite timeouts apply to all queries.

Environment overrides: `UVICORN_HOST`, `UVICORN_PORT`, `ASSTAT_VM_URL`,
`ASSTAT_KNOWNLINKS`, `ASN_METADATA_HTTP_TIMEOUT_SECONDS`,
`ASSTAT_SVG_CACHE_TTL_SECONDS`, `ASSTAT_WEB_CACHE_PATH`,
`ASSTAT_WEB_CACHE_TTL_SECONDS`, `ASSTAT_WEB_CACHE_MAX_SIZE_BYTES`, and standard
`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`. A `HTTPS_PROXY` URL beginning with
`http://` is valid for HTTPS CONNECT. RIPEstat uses those proxy settings, or
direct access if they are unset. DNS Cymru queries use the host resolver, not
an HTTP proxy. TLS certificate verification remains enabled. Protect real
configuration files containing credentials; do not commit them.

The process reads knownlinks and writes `data/asn-metadata.json`, its lock and
diagnostic backup files, and `data/web-cache/`. The web-cache path must remain
inside the project. Both `data/` and the configured cache location must be
writable by `asstat-web`. No collector/spool/VM write access is needed.

## Manual launch and page addresses

```sh
cd /opt/as-stat-be-web
sudo -u asstat-web .venv/bin/python -m app
```

This supported entry point reads host/port from configuration. With loopback
host and port 8000, open `http://localhost:8000/`,
`http://localhost:8000/view-asn` or `http://localhost:8000/link-usage` on the host.
To view remotely, use an operator-managed reverse proxy or SSH forwarding:

```sh
ssh -L 8000:localhost:8000 operator@example.net
```

An alternate launch accepts explicit Uvicorn host/port, overriding the INI
listen address for that invocation:

```sh
sudo -u asstat-web .venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8000
```

Use one worker: caches/coalescing and external request limits are per process.
The application has no built-in user authentication. Bind it to loopback or
an operator-approved internal interface; the operator supplies authorization,
whitelist policy and TLS at the proxy. Do not expose VictoriaMetrics directly
to the browser. Ctrl+C stops a manual foreground run.

## systemd deployment

The repository includes `deploy/as-stat-be-web.service`. Its command is the
same `python -m app` entry point. It runs as `asstat-web`, uses the project as
working directory and restarts on failure after five seconds. The unit does
not block network/DNS access or hide configured knownlinks paths. Its umask
restricts runtime files; `NoNewPrivileges=true` does not prevent ordinary DNS,
TCP/TLS, reading configuration or writing owned cache directories.

After verifying a manual run, stop it before enabling the service on the same
port. Install the example only on the host being deployed:

```sh
sudo install -o root -g root -m 0644 deploy/as-stat-be-web.service \
  /etc/systemd/system/as-stat-be-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now as-stat-be-web.service
sudo systemctl status as-stat-be-web.service
sudo journalctl -u as-stat-be-web.service -n 100 --no-pager
sudo journalctl -u as-stat-be-web.service -f
sudo systemctl stop as-stat-be-web.service
sudo systemctl start as-stat-be-web.service
sudo systemctl restart as-stat-be-web.service
```

Change the unit paths and any access restrictions if your deployment differs.
These examples do not install a service automatically.

## knownlinks contract

Six whitespace-separated columns, with shell-style quotes and `#` comments:

```text
192.0.2.10 1 example-transit "Example Transit" 66c2a5 unused
192.0.2.10 2 example-peering "Example Peering" 8da0cb unused
```

The columns are exporter IPv4, nonzero uint32 ifIndex, **stable link_id**,
display name, six-digit RGB color without `#`, and an ignored legacy column.
The documentation address and names are fictional. Identity comes exclusively
from column three; renaming column four preserves historical association.
Column six is never used for sampling or identity. Duplicate link IDs with
matching metadata share one link; conflicting names/colors and duplicate
exporter/interface mappings are explicit errors, never silently discarded.
Configured links without traffic remain visible. Unknown historical IDs retain
their identity internally but are displayed as `Unknown link`.

## Traffic and period semantics

The metric is `asstat_traffic_bytes{link_id,asn,direction,ip_version}`. Directions
are `in`/`out`, IP families are `4`/`6`, and ASN is a decimal uint32 string.
Each value represents **bytes for one complete minute**, with sampling already
applied. Average minute speed is `bytes * 8 / 60` bit/s. Sampling is not reapplied;
`rate()` and `increase()` are not used. Missing points remain null, distinct
from a real zero. Available IPv4/IPv6 values are summed without claiming full
coverage. Input is drawn below zero, output above; source values stay positive.

View ASN defaults to the last **23 hours of complete minutes**. End B is the
start of the current UTC minute, A = B − 23 hours; the interval is [A,B).
Custom intervals are minute-aligned, positive, at most seven days and cannot
include the unfinished current minute. For Top ASN and Link Usage:
B = floor(now_utc / 1200) * 1200; A = B − 86400. Their 24-hour interval ends at
an UTC 20-minute boundary and is labelled “Updated every 20 minutes”.

Volumes use instant `sum_over_time(...[duration])`, aggregated by labels, at
`time=B−0.001`. Top ASN uses `topk(N, sum by(asn)(sum_over_time(...[86400s])))`
and a separate aggregate query for direction totals. Link Usage selects each
direction's top ten for the entire period and aggregates Others outside that
set. It does not fetch all individual ASN minute histories.

Minute graphs use `sum_over_time(asstat_traffic_bytes{...}[1ms]) * 8 / 60` with
`query_range(start=A,end=B−60,step=60s)`. Point t describes [t,t+60).
Graph points are never used to calculate volumes. Nulls are not filled with
zeros or connected across gaps. Direction stacks are independent. Units use
base 1000: B/kB/MB/GB/TB and bit/s/kbit/s/Mbit/s/Gbit/s. Exact bytes appear in
volume tooltips. Totals count traffic across links and are not guaranteed to
be unique network volume; missing data does not prove inactivity or full-day
coverage. Forms use browser-local time, requests use UTC. SVG axes convert UTC
through the supplied IANA timezone, including DST; Europe/Kiev is normalized
to Europe/Kyiv.

## Result, SVG and metadata caches

Successful results and SVG responses have a default **1200-second** TTL.
Top ASN and Link Usage numerical data use memory → file → VictoriaMetrics;
Link Usage stores ranking, minute rows and legend together. Each SHA-256 key
has one atomic JSON file in `data/web-cache/`, shared disk budget 128 MiB.
Top keys include A/B, limit and format version; link keys include link_id,
A/B and format version. Numeric caches do not depend on timezone. Reads do
not extend expiry from successful computation; corrupt/expired entries are
ignored and cleaned on demand. There is no cron or separate cache service.
Errors are not persisted as success. In-process identical computations share
one result; only two links are processed concurrently.

Each numerical memory cache is limited to 16 MiB/128 entries. Rendered ASN and
link bundle caches are separately bounded at 16 MiB/128 entries. ASN SVG is
memory-only and can require a VM query after a process restart. Link SVG and
legend can be rebuilt from the persistent numerical result without VM calls;
rendering does not extend numeric freshness. Restarted processes reuse fresh
result files. Eviction is possible before TTL when a size limit is reached.

Direct SVG routes:

- `/api/asn/sparkline.svg?asn=N&start=A&end=B&tz=ZONE&v=5`
- `/api/link-usage/sparkline.svg?link_id=ID&start=A&end=B&tz=ZONE&v=2`

The UI creates URLs from server-returned A/B and browser timezone. SVG routes
accept any complete 24-hour minute-aligned interval, not only new 20-minute
boundaries. Responses are `image/svg+xml`, inline, with ETag and
`Cache-Control: public, max-age=1200`; conditional requests support 304.
Errors have `Cache-Control: no-store`. SVGs are generated in Python and loaded
through direct image URLs. Lazy queues permit four ASN or two link images;
no per-image browser/ECharts process or on-disk SVG archive is used.

ASN metadata is fetched on user access from [Team Cymru DNS TXT](https://www.team-cymru.com/ip-asn-mapping)
(`AS<number>.asn.cymru.com`), with [RIPEstat](https://stat.ripe.net/docs/data-api/api-endpoints/as-overview)
as fallback. Name/country are independently fresh for **42 days**. RIPEstat
holder supplies a name but no country; an older Cymru country is preserved
without extending its freshness. Stale records appear immediately while a
bounded asynchronous queue updates them. Retry after source errors is one
hour; confirmed absence is cached for 24 hours. Polling reads status only,
for at most 60 seconds. There are at most two external calls in flight and
one new call per second. Atomic `data/asn-metadata.json` storage survives
restarts; corrupt files are preserved for diagnosis. Metadata failures do not
block rankings or graphs. Names are inserted as text, never as HTML.

## Optional nginx example

`deploy/nginx.conf.example` is an independent **http-context** example with
its own cache zone, loopback listener and neutral hostname. It is not an
installed proxy configuration. Operator-managed authentication/whitelist and
TLS must be applied before exposure. The operator must create a writable
cache directory for nginx workers and validate the complete configuration.

Only the two SVG routes are cached for 20 minutes. HTML/JSON are proxied with
cache off. Keys use the complete URI including the query string, so A/B,
identifier, timezone and version remain distinct; `proxy_cache_lock` combines
concurrent cold requests. Upstream no-store and ETag are honored. See the
[nginx proxy module documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).

To verify an operator-installed TLS proxy, copy one actual SVG URL from the
browser network panel. Run the following twice with **the identical URL**, no
If-None-Match or no-cache header, and keep TLS verification enabled:

```sh
SVG_URL='https://example.net/api/asn/sparkline.svg?asn=64496&start=A&end=B&tz=UTC&v=5'
# Replace A/B with actual server-returned timestamps and ASN with one in your data.
curl --noproxy '*' -sS -D - -o /dev/null -w '%{http_code} %{time_total}s\n' "$SVG_URL"
curl --noproxy '*' -sS -D - -o /dev/null -w '%{http_code} %{time_total}s\n' "$SVG_URL"
```

Expect MISS→HIT or HIT→HIT in `X-Cache-Status`. Check Python logs to confirm HIT
never reached the application. Cached `X-ASStat-*` headers describe the original
upstream response, not new VM requests on proxy HIT. Proxy caching requires
separate installation and verification; the example does not establish HIT.

## Checks

Python tests use synthetic fixtures and mocks, without VM traffic or live ASN
lookups. Install `nodejs` separately if running the JavaScript checks:

```sh
sudo apt-get install -y nodejs
cd /opt/as-stat-be-web
.venv/bin/python -B -m unittest discover -s tests -v
for check in tests/check_*.cjs; do node "$check"; done
git diff --check
```

Optional live comparisons require explicit operator parameters, never a
hardcoded real ASN/link. Documentation ASN **64496** is fictional here;
a real check requires an ASN present in the operator's data. Run one small
check, not a mass scan:

```sh
.venv/bin/python -B scripts/check_operator.py --help
.venv/bin/python -B scripts/check_operator.py --web-url http://localhost:8000 \
  --asn 64496 --mode volumes
```

The CLI performs read-only queries and prints counts/comparison status, not
full traffic responses. No live checks run automatically. `X-ASStat-Cache`,
`X-ASStat-VM-Queries`, `X-ASStat-VM-Seconds` and diagnostic logs expose cache
outcomes, safe hashed keys and VM counts/timings. Real configuration, caches,
logs and virtual environments are ignored by Git; do not publish operator data.
