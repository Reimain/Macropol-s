"""Discoverers for infrastructure: containers, orchestration and IaC.

These read the files that describe *where software runs* rather than what it is
made of. A Dockerfile names the base image a service inherits its CVEs from; a
Kubernetes manifest names the images actually scheduled; Terraform names the
cloud resources underneath both. Each is declarative and each cites a line, so
the evidence is as checkable as a lockfile pin even though it is weaker.

Nothing here reads a secret's contents. A Kubernetes ``Secret`` is recorded as
existing and nothing more — see :mod:`slpie.discovery.infrastructure.kubernetes`.
"""

from __future__ import annotations

from . import compose, dockerfile, helm, kubernetes, terraform

__all__ = ["dockerfile", "compose", "kubernetes", "helm", "terraform"]
