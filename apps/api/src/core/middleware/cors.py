"""Tenancy-aware CORS configuration."""

import re
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.config import get_learnhouse_config


_SINGLE_TENANCY_LOCALHOST_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def _host_from(value: str) -> str:
    """Extract a bare hostname from a config value that may be a host or URL."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    host = urlparse(value).hostname if "://" in value else value
    return (host or "").removeprefix("www.").lower()


def _single_tenancy_origin_regex(config) -> str:
    """Build a CORS origin regex pinned to the configured host(s) plus Vercel and Railway domains."""
    import os
    hosts = set()
    for cfg_value in (
        os.environ.get("LEARNHOUSE_FRONTEND_DOMAIN"),
        os.environ.get("NEXT_PUBLIC_LEARNHOUSE_MAIN_DOMAIN_NAME"),
        config.hosting_config.frontend_domain,
        config.hosting_config.domain,
    ):
        host = _host_from(cfg_value)
        if host and host not in ("localhost", "127.0.0.1"):
            hosts.add(host)

    if not hosts:
        return r"^https?://(?:localhost|127\.0\.0\.1|.*\.vercel\.app|.*\.up\.railway\.app)(:\d+)?$"

    host_alternation = "|".join(
        rf"(?:www\.)?{re.escape(h)}" for h in sorted(hosts)
    )
    return (
        rf"^https?://(?:{host_alternation}|.*\.vercel\.app|.*\.up\.railway\.app|localhost|127\.0\.0\.1)(:\d+)?$"
    )


def get_cors_origin_regex() -> str:
    """
    Compute the regex for ``CORSMiddleware``'s ``allow_origin_regex`` based on
    the active tenancy mode.

    - ``single`` → pin to the configured frontend/domain host(s) only (S25),
      since credentials are allowed and reflecting any origin would be unsafe.
    - ``multi``  → use the configured ``LEARNHOUSE_ALLOWED_REGEXP`` (matches
      the configured domain and its subdomains). Verified per-org custom
      domains are a known gap; they require backend restart or per-request
      DB resolution.
    """
    config = get_learnhouse_config()
    if config.hosting_config.tenancy == "single":
        return _single_tenancy_origin_regex(config)
    # Multi-tenancy: use the configured regexp. If the operator left it empty
    # (it is not required for multi mode, only the base domain is), fall back to
    # a domain-and-subdomain regex derived from the configured domain. Returning
    # an empty/None value here makes CORSMiddleware reject every cross-origin
    # request with credentials, taking the whole SaaS frontend offline.
    allowed_regexp = config.hosting_config.allowed_regexp
    if allowed_regexp:
        return allowed_regexp
    domain = _host_from(config.hosting_config.domain)
    if domain:
        return rf"^https?://(?:[a-z0-9-]+\.)*{re.escape(domain)}(:\d+)?$"
    return _SINGLE_TENANCY_LOCALHOST_REGEX


def configure_cors(app: FastAPI) -> None:
    """Register CORS middleware on ``app`` with tenancy-aware origin policy."""
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=get_cors_origin_regex(),
        allow_methods=["*"],
        allow_credentials=True,
        allow_headers=["*"],
    )
