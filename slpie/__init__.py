"""SLPIE — Universal Smart Layered Package Intelligence Engine.

An architecture intelligence platform that discovers, links, validates, governs
and explains every dependency, artifact, interface, service, dataset and
infrastructure component in an ecosystem.

It operates like a base station rather than a crawler. You declare an
environment — codebases, data folders, network links, web apps, IoT interfaces,
security concerns — and elements *attach*, negotiate what they will let the
platform see, and stay tracked. Discovery then corroborates the declaration, and
the delta between what was declared and what was observed is itself
intelligence.

Three commitments run through every module:

* **No relationship exists without evidence.** Enforced in the constructor, not
  by review.
* **Confidence is derived, never assigned.** No caller anywhere passes a number.
* **Every answer carries its reasoning path and its gaps.** What the platform
  could not see is part of the answer.

The kernel has zero third-party dependencies, including the interface.
"""

from __future__ import annotations

from .errors import SlpieError

__version__ = "0.1.0"

__all__ = ["SlpieError", "__version__"]
