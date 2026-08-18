"""The environment the documentation and the demo are both built from.

One manifest, used by the screenshot pass and by the demo builder, so the two
cannot describe different systems. A screenshot of one estate beside a demo of
another is the kind of drift that makes a reader stop trusting both.

It is deliberately a *simulated* estate rather than this repository: the
documentation should show what the console looks like against something with
services, boundaries, a database and an external provider in it, and this
repository has none of those.
"""

from __future__ import annotations

from pathlib import Path

MANIFEST = """
apiVersion: slpie/v1
environment: acme-production
target: simulated

security:
  concerns: [pci-dss, gdpr, soc2]
  boundaries:
    - name: cardholder-data
      contains: [payments, vault]

codebase:
  - root: ./services/payments
    team: payments
    domain: billing
  - root: ./services/orders
    team: fulfilment
    domain: commerce
  - root: ./services/vault
    team: security
    domain: billing

data:
  - folder: ./warehouse/schemas
    kind: schema
  - uri: postgres://analytics/orders
    kind: database
    classification: pii

network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
  - name: order-events
    uri: kafka://broker/orders
    kind: event-stream

web:
  - name: storefront
    root: ./apps/storefront
    framework: next

providers:
  - name: stripe
    kind: external-api
"""


def build(root: str | Path):
    """Declare, materialise, attach and scan. Returns the live engine.

    Each step is allowed to fail loudly rather than being wrapped in a bare
    `except`: a screenshot pass that silently produced an empty console would
    publish a picture of nothing and call it documentation.
    """
    from slpie.engine import Engine

    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=str(root))
    engine.attach()
    engine.scan()
    return engine
