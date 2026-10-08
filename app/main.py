import logging
import hashlib
import json
from dataclasses import asdict
from contextlib import asynccontextmanager
from pydantic import BaseModel, ConfigDict, StrictStr
from app.asn_metadata import MetadataService, validate_asn
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.series import build_series_query, query_series_vm, make_series_response

from app.sparkline import cache as svg_cache, validate_sparkline, load_svg, SVG_VERSION, validate_timezone
from app.top_asn import validate_limit, fetch_top
from app.web_cache import top_cache
from app.periods import ranking_period
from app import link_usage, ipv
from app.archive_sources import view_parameters, source_url
from app.diagnostics import RequestDiagnosticsMiddleware
from app.config import Settings, ConfigurationError
from app.url_prefix import PrefixMiddleware, page_response
from app.knownlinks import KnownlinksError, parse_knownlinks
from app.volumes import ParameterError, VMError, validate_parameters, build_query, query_vm, make_response

metadata_service = MetadataService()

@asynccontextmanager
async def lifespan(app):
    try:
        Settings.from_env()
    except ConfigurationError as exc:
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        raise SystemExit(2) from None
    metadata_service.start()
    yield
    metadata_service.close()

app = FastAPI(title="AS-Stats links", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(RequestDiagnosticsMiddleware)
app.add_middleware(PrefixMiddleware)
logger = logging.getLogger(__name__)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/api/links")
def get_links():
    settings = Settings.from_env()
    try:
        return [asdict(link) for link in parse_knownlinks(settings.knownlinks_path)]
    except KnownlinksError as exc:
        logger.exception("Invalid knownlinks")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Cannot read knownlinks")
        raise HTTPException(status_code=503, detail="Cannot read knownlinks") from exc


@app.get("/", include_in_schema=False)
def index(request: Request):
    return page_response("index.html",request)


@app.get("/view-asn", include_in_schema=False)
def view_asn(request: Request):
    return page_response("view-asn.html",request)


@app.get("/api/asn/volumes")
def asn_volumes(asn: str, ip_version: str = "both", start: str | None = None,
                end: str | None = None, period: str | None = None):
    if period is None and start is None and end is None: period = '1d'
    try:
        if period is None:
            asn, ip_version, start, end = validate_parameters(asn, ip_version, start, end)
            step = 60
        else:
            asn, ip_version, start, end, step = view_parameters(asn, ip_version, period, start, end)
    except ParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = Settings.from_env()
    try:
        links = parse_knownlinks(settings.knownlinks_path)
    except KnownlinksError as exc:
        logger.exception("Invalid knownlinks")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Cannot read knownlinks")
        raise HTTPException(status_code=503, detail="Cannot read knownlinks") from exc
    query, instant = build_query(asn, ip_version, start, end)
    try:
        vector = query_vm(source_url(settings, period or '1d'), query, instant)
        return make_response(asn, ip_version, start, end, links, vector)
    except VMError as exc:
        logger.exception("VictoriaMetrics request failed")
        raise HTTPException(status_code=exc.status_code, detail=f'Unable to load traffic for period {period or "1d"}. Please try again.') from exc


@app.get("/api/asn/series")
def asn_series(asn: str, ip_version: str = "both", start: str | None = None,
               end: str | None = None, period: str | None = None):
    if period is None and start is None and end is None: period = '1d'
    try:
        if period is None:
            asn, ip_version, start, end = validate_parameters(asn, ip_version, start, end)
            step = 60
        else:
            asn, ip_version, start, end, step = view_parameters(asn, ip_version, period, start, end)
    except ParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = Settings.from_env()
    try:
        links = parse_knownlinks(settings.knownlinks_path)
    except KnownlinksError as exc:
        logger.exception("Invalid knownlinks")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Cannot read knownlinks")
        raise HTTPException(status_code=503, detail="Cannot read knownlinks") from exc
    try:
        matrix = query_series_vm(source_url(settings, period or '1d'),
                                 build_series_query(asn, ip_version, step), start, end,
                                 step=step, evaluation_end=end-step)
        return make_series_response(asn, ip_version, start, end, links, matrix, step=step)
    except VMError as exc:
        logger.exception("VictoriaMetrics request failed")
        raise HTTPException(status_code=exc.status_code, detail=f'Unable to load traffic for period {period or "1d"}. Please try again.') from exc


@app.get("/api/top-asn")
def top_asn(limit: str = "20"):
    try:
        limit = validate_limit(limit)
    except ParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        settings = Settings.from_env()
        start, end = ranking_period()
        return top_cache.get(settings, ('1', start, end, limit),
                             lambda: fetch_top(settings.victoriametrics_url, limit, now=end, timeout_seconds=settings.top_asn_vm_timeout))
    except VMError as exc:
        logger.exception("VictoriaMetrics ranking request failed")
        raise HTTPException(status_code=exc.status_code,
                            detail="Traffic data request timed out" if exc.status_code == 504
                            else "Unable to load traffic ranking") from exc


from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    response = await http_exception_handler(request, exc)
    path = request.url.path.removeprefix(request.scope.get("root_path", ""))
    if path.endswith(".svg") or path.startswith(("/api/link-usage", "/api/ipv")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    response = await request_validation_exception_handler(request, exc)
    path = request.url.path.removeprefix(request.scope.get("root_path", ""))
    if path.endswith(".svg") or path.startswith(("/api/link-usage", "/api/ipv")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/asn/sparkline.svg")
def sparkline(request: Request, asn: str, start: str | None = None, end: str | None = None, tz: str = "UTC"):
    try:
        asn, start, end = validate_sparkline(asn, start, end)
        tz = validate_timezone(tz).key
    except ParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = Settings.from_env()
    try:
        data, etag = svg_cache.get((SVG_VERSION, settings.victoriametrics_url, str(settings.knownlinks_path), asn, start, end, tz, settings.svg_cache_ttl),
                                   lambda: load_svg(settings, asn, start, end, tz),ttl=settings.svg_cache_ttl)
    except (VMError, KnownlinksError, OSError, ValueError) as exc:
        logger.exception("Sparkline generation failed")
        raise HTTPException(status_code=getattr(exc, "status_code", 503),
                            detail="Unable to load traffic image") from exc
    headers = {"Cache-Control":f"public, max-age={settings.svg_cache_ttl}", "ETag":etag,
               "Content-Disposition":f'inline; filename="as{asn}-traffic.svg"'}
    supplied = [token.strip().removeprefix("W/") for token in request.headers.get("if-none-match", "").split(",")]
    if etag in supplied or "*" in supplied:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="image/svg+xml", headers=headers)


class MetadataBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asns: list[StrictStr]


def metadata_asns(batch):
    if len(batch.asns) > 3000:
        raise HTTPException(status_code=422, detail="Too many ASN entries")
    try:
        asns = list(dict.fromkeys(validate_asn(asn) for asn in batch.asns))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(asns) > 300:
        raise HTTPException(status_code=422, detail="At most 300 unique ASNs are allowed")
    return asns


@app.post("/api/asn/metadata")
def asn_metadata(batch: MetadataBatch):
    asns = metadata_asns(batch)
    try:
        return metadata_service.batch(asns, refresh=True)
    except OSError:
        logger.error("ASN metadata cache unavailable")
        raise HTTPException(status_code=503, detail="Metadata is temporarily unavailable") from None


@app.post("/api/asn/metadata/status")
def asn_metadata_status(batch: MetadataBatch):
    asns = metadata_asns(batch)
    try:
        return metadata_service.batch(asns, refresh=False)
    except OSError:
        logger.error("ASN metadata cache unavailable")
        raise HTTPException(status_code=503, detail="Metadata is temporarily unavailable") from None


@app.get("/link-usage", include_in_schema=False)
def link_usage_page(request: Request):
    return page_response("link-usage.html",request)


@app.get("/api/link-usage")
def link_usage_manifest():
    try:
        data = link_usage.manifest(Settings.from_env())
    except (KnownlinksError, OSError):
        logger.exception("Link Usage manifest failed")
        raise HTTPException(status_code=503, detail="Unable to load links") from None
    return Response(content=json.dumps(data),media_type="application/json",headers={"Cache-Control":"no-store"})


def usage_bundle(link_id, start, end, tz="UTC"):
    settings = Settings.from_env()
    try:
        tz = validate_timezone(tz).key
        link, start, end = link_usage.validate_link(settings,link_id,start,end)
        return link_usage.get_bundle(settings,link,start,end,tz)
    except ParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except (VMError, KnownlinksError, OSError, ValueError) as exc:
        logger.exception("Link Usage data request failed")
        raise HTTPException(status_code=getattr(exc,"status_code",503),detail="Unable to load link traffic") from None


def usage_response(request, data, etag, media_type, filename=None):
    headers = {"Cache-Control":f"public, max-age={Settings.from_env().svg_cache_ttl}", "ETag":etag}
    if filename is not None:
        headers["Content-Disposition"] = f'inline; filename="{filename}"'
    supplied = [token.strip().removeprefix("W/") for token in request.headers.get("if-none-match", "").split(",")]
    if etag in supplied or "*" in supplied:
        return Response(status_code=304,headers=headers)
    return Response(content=data,media_type=media_type,headers=headers)


@app.get("/api/link-usage/link")
def link_usage_data(request: Request, link_id: str, start: str | None = None, end: str | None = None, tz: str = "UTC"):
    bundle, _ = usage_bundle(link_id,start,end,tz)
    metadata = json.loads(bundle)
    metadata.pop("svg",None)
    metadata["svg_url"] = request.scope.get("root_path", "") + metadata["svg_url"]
    data = json.dumps(metadata,separators=(",",":")).encode("utf-8")
    etag = '"' + hashlib.sha256(data).hexdigest() + '"'
    return usage_response(request,data,etag,"application/json")


@app.get("/api/link-usage/sparkline.svg")
def link_usage_svg(request: Request, link_id: str, start: str | None = None, end: str | None = None, tz: str = "UTC"):
    bundle, _ = usage_bundle(link_id,start,end,tz)
    data = json.loads(bundle)['svg'].encode('utf-8')
    etag = '"' + hashlib.sha256(data).hexdigest() + '"'
    return usage_response(request,data,etag,"image/svg+xml",f"link-{link_id}-traffic.svg")


@app.get("/ipv", include_in_schema=False)
def ipv_page(request: Request):
    return page_response("ipv.html", request)


def ipv_parameters(mode, period, start=None, end=None, tz="UTC"):
    try:
        start, end, step = ipv.parameters(mode,period,start,end)
        tz = validate_timezone(tz).key
        return start,end,step,tz
    except ParameterError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/api/ipv")
def ipv_overview(request: Request, mode: str="compare", period: str="1w", tz: str="UTC"):
    start,end,step,tz=ipv_parameters(mode,period,tz=tz)
    settings=Settings.from_env()
    try:
        data,_=ipv.get_data(settings,mode,period,start,end,step)
        rows=ipv.display_rows(settings,data)
    except (VMError,KnownlinksError,OSError,ValueError):
        logger.exception("IPv data request failed")
        raise HTTPException(status_code=503,detail=f"Unable to load IPv traffic for period {period}. Please try again.") from None
    from urllib.parse import urlencode
    prefix=request.scope.get("root_path", "")
    directions=('in','out') if mode=='compare' else ('both',)
    urls=[prefix+'/api/ipv/traffic.svg?'+urlencode(dict(mode=mode,period=period,start=start,end=end,tz=tz,direction=d,v=ipv.SVG_VERSION)) for d in directions]
    return Response(json.dumps({'mode':mode,'period':period,'start':start,'end':end,'step':step,
        'svg_urls':urls,'legend':[{'name':r['name'],'color':r['color']} for r in rows],
        'history_limited':True},separators=(',',':')),media_type='application/json',headers={'Cache-Control':'no-store'})


@app.get("/api/ipv/traffic.svg")
def ipv_image(request: Request, mode: str="compare", period: str="1w", start: str|None=None,
              end: str|None=None, tz: str="UTC", direction: str="in"):
    start,end,step,tz=ipv_parameters(mode,period,start,end,tz)
    if direction not in (('in','out') if mode=='compare' else ('both',)):
        raise HTTPException(status_code=422,detail="Invalid IPv graph direction")
    settings=Settings.from_env()
    try:
        image=ipv.get_svg(settings,mode,period,start,end,step,tz,direction)
    except (VMError,KnownlinksError,OSError,ValueError):
        logger.exception("IPv SVG request failed")
        raise HTTPException(status_code=503,detail=f"Unable to load IPv traffic image for period {period}. Please try again.") from None
    etag='"'+hashlib.sha256(image).hexdigest()+'"'
    ttl=ipv.cache_settings(settings,period).web_cache_ttl
    headers={'Cache-Control':f'public, max-age={ttl}','ETag':etag,
             'Content-Disposition':f'inline; filename="ipv-{mode}-{period}-{direction}.svg"'}
    supplied=[token.strip().removeprefix('W/') for token in request.headers.get('if-none-match','').split(',')]
    if etag in supplied or '*' in supplied:return Response(status_code=304,headers=headers)
    return Response(image,media_type='image/svg+xml',headers=headers)
