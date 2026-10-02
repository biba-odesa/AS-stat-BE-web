import ipaddress
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


class KnownlinksError(ValueError):
    pass


@dataclass(frozen=True)
class Link:
    link_id: str
    name: str
    color: str


def parse_knownlinks(path: Path) -> list[Link]:
    links = {}
    endpoints = {}
    mapping_count = 0
    with path.open("rb") as source:
        number = 0
        while raw := source.readline(4097):
            number += 1
            def fail(reason):
                raise KnownlinksError(f"knownlinks line {number}: {reason}")

            if number > 131072:
                fail("too many lines")
            if len(raw) > 4096:
                fail("line exceeds 4096 bytes")
            try:
                columns = shlex.split(raw.decode("utf-8"), comments=True)
            except (UnicodeError, ValueError) as exc:
                fail(f"invalid encoding or quoting: {exc}")
            if not columns:
                continue
            if len(columns) != 6:
                fail("expected six columns")
            exporter, index, link_id, name, color, _ignored = columns
            try:
                ipaddress.IPv4Address(exporter)
            except ValueError:
                fail("invalid exporter IPv4")
            if not re.fullmatch(r"[0-9]+", index) or not 1 <= int(index) <= 4294967295:
                fail("ifIndex must be a nonzero uint32")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", link_id):
                fail("invalid link_id")
            if not name:
                fail("empty display name")
            if not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
                fail("color must contain six hexadecimal digits")
            endpoint = (exporter, int(index))
            if endpoint in endpoints:
                fail(f"duplicate endpoint; first defined on line {endpoints[endpoint]}")
            link = Link(link_id, name, "#" + color.lower())
            if link_id in links and links[link_id][0] != link:
                fail(f"conflicting metadata for {link_id}; first defined on line {links[link_id][1]}")
            mapping_count += 1
            if mapping_count > 65536:
                fail("too many mappings")
            endpoints[endpoint] = number
            links.setdefault(link_id, (link, number))
    return [links[key][0] for key in sorted(links)]
