"""Prometheus metrics (PRD 14).

Labels use ROUTE TEMPLATES only ("/repos/{repo_id}", never "/repos/42"; GitHub endpoints likewise),
so cardinality stays bounded and no URL, id or token can leak into a label.
"""

from __future__ import annotations

import math

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.github.ratelimit_state import rate_limit_state

CONTENT_TYPE = CONTENT_TYPE_LATEST
_SCAN_BUCKETS = (0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300)


def _rate_limit_remaining() -> float:
    remaining, _ = rate_limit_state.snapshot()
    return math.nan if remaining is None else float(remaining)


class Metrics:
    """One set of collectors on its own registry (so tests and apps never share counters)."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.http_requests = Counter(
            "gitwell_http_requests_total",
            "HTTP requests served.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "gitwell_http_request_duration_seconds",
            "HTTP request duration.",
            ["method", "route"],
            registry=self.registry,
        )
        self.scans = Counter(
            "gitwell_scans_total",
            "Scans by lifecycle status (queued, completed, failed) and trigger.",
            ["status", "trigger"],
            registry=self.registry,
        )
        self.scan_duration = Histogram(
            "gitwell_scan_duration_seconds",
            "Time a scan spent running.",
            buckets=_SCAN_BUCKETS,
            registry=self.registry,
        )
        self.rule_results = Counter(
            "gitwell_rule_results_total",
            "Rule results by rule and status.",
            ["rule_id", "status"],
            registry=self.registry,
        )
        self.github_requests = Counter(
            "gitwell_github_requests_total",
            "Requests to GitHub by route template and HTTP status (or 'error').",
            ["endpoint", "status"],
            registry=self.registry,
        )
        self.rate_limit_remaining = Gauge(
            "gitwell_github_rate_limit_remaining",
            "GitHub requests remaining in the current window (NaN until known).",
            registry=self.registry,
        )
        self.rate_limit_remaining.set_function(_rate_limit_remaining)

    def render(self) -> bytes:
        return generate_latest(self.registry)


_current = Metrics()


def get_metrics() -> Metrics:
    return _current


def set_metrics(metrics: Metrics) -> None:
    """Install a fresh set (create_app does this, so every app starts from zero)."""
    global _current
    _current = metrics
