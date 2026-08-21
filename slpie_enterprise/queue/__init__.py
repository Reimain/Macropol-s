"""Celery as a `TaskRunner`. The protocol is ring 0's; this is one implementation."""

from __future__ import annotations

from .celery_runner import CeleryRunner, application

__all__ = ["CeleryRunner", "application"]
