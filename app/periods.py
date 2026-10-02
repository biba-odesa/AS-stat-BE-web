"""Stable daily ranking periods independent of SVG cache expiration."""
import time


def ranking_period(now=None):
    end = int(time.time() if now is None else now) // 1200 * 1200
    return end - 86400, end
