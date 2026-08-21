"""What a connector needs before it can work — declared, not discovered.

The integration story most platforms ship is: install a plugin, edit a config
file, restart, discover you needed a different scope, repeat. The story here is
that a connector *declares* what authentication it requires, and the moment a
principal supplies that, the connector activates and its capabilities appear.
No config edit, no restart, no code.

That inversion only works if the declaration is honest and complete, so a
`ConnectorSpec` states four things and is refused at construction if it does
not:

* **Which sign-in methods can unlock it.** Google Drive is unlocked by a Google
  identity and by nothing else; an internal Postgres is unlocked by an API key
  or an enterprise IdP.
* **What minimum assurance it demands.** A read-only docs connector will take a
  magic link. A connector that can write to production will not, and says so
  rather than leaving it to whoever wires it up.
* **Which scopes it needs**, separated into required and optional, so a partial
  grant produces a *degraded* connector rather than a failure — the difference
  between "reads your calendar but not your contacts" and "does not work".
* **Which platform capabilities it grants.** This is what makes the automation
  real: activating a connector registers its capabilities with the station, and
  every answer that depended on a capability it did *not* grant carries that as
  a named gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..errors import SlpieError
from ..identity.principal import Assurance, AuthMethod


class ConnectorError(SlpieError):
    """A connector could not be declared, granted, or activated."""


class ConnectorKind(str, Enum):
    """What sort of thing is on the other end. Drives defaults, not behaviour."""

    SOURCE_CONTROL = "source_control"      # GitHub, GitLab, Bitbucket
    REGISTRY = "registry"                  # npm, PyPI, an internal Artifactory
    CLOUD = "cloud"                        # AWS, GCP, Azure
    DATABASE = "database"                  # Postgres, Snowflake, BigQuery
    STORAGE = "storage"                    # S3, GCS, Drive, SharePoint
    ISSUE_TRACKER = "issue_tracker"        # Jira, Linear
    COMMUNICATION = "communication"        # Slack, Teams, email
    OBSERVABILITY = "observability"        # Datadog, Grafana, Sentry
    CI = "ci"                              # GitHub Actions, Jenkins
    MODEL = "model"                        # an LLM backend
    CUSTOM = "custom"

    @property
    def touches_production(self) -> bool:
        """Kinds where a wrong grant has operational, not just data, consequences."""
        return self in (
            ConnectorKind.CLOUD, ConnectorKind.DATABASE, ConnectorKind.CI,
        )


@dataclass(frozen=True, slots=True)
class Scope:
    """One permission a connector asks for, in language a human can consent to.

    `description` is not documentation. It is what gets shown on the consent
    screen, and a scope whose description is `"repo"` is a scope nobody can
    meaningfully agree to — so it is required and non-empty.
    """

    name: str
    description: str
    required: bool = True
    writes: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConnectorError("a scope must be named")
        if not self.description.strip():
            raise ConnectorError(
                f"scope {self.name!r} has no description; a consent screen that "
                f"cannot say what it is asking for is not consent"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description,
            "required": self.required, "writes": self.writes,
        }

    def __str__(self) -> str:
        return f"{self.name}{' (write)' if self.writes else ''}"


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    """A connector's declaration of what it needs and what it gives back."""

    id: str
    kind: ConnectorKind
    title: str
    description: str = ""
    methods: tuple[AuthMethod, ...] = ()
    assurance: Assurance = Assurance.STANDARD
    scopes: tuple[Scope, ...] = ()
    capabilities: tuple[str, ...] = ()
    issuer: str = ""
    vendor: str = ""
    docs: str = ""
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ConnectorError("a connector must have an id")
        if not self.title.strip():
            raise ConnectorError(f"connector {self.id!r} has no title")
        if not self.methods:
            raise ConnectorError(
                f"connector {self.id!r} names no authentication method; a "
                f"connector that anything can unlock is not a connector"
            )
        if not self.capabilities:
            raise ConnectorError(
                f"connector {self.id!r} grants no capabilities, so activating it "
                f"would change nothing observable"
            )
        writing = [scope.name for scope in self.scopes if scope.writes]
        if self.read_only and writing:
            raise ConnectorError(
                f"connector {self.id!r} is declared read-only but asks for write "
                f"scopes {writing}; one of the two is wrong"
            )
        if self.kind.touches_production and self.assurance is Assurance.WEAK:
            # A magic link should not be enough to reach production infrastructure.
            raise ConnectorError(
                f"connector {self.id!r} reaches {self.kind.value} but accepts "
                f"weak assurance; raise it to STANDARD or above"
            )

    @property
    def required_scopes(self) -> tuple[Scope, ...]:
        return tuple(scope for scope in self.scopes if scope.required)

    @property
    def optional_scopes(self) -> tuple[Scope, ...]:
        return tuple(scope for scope in self.scopes if not scope.required)

    @property
    def write_scopes(self) -> tuple[Scope, ...]:
        return tuple(scope for scope in self.scopes if scope.writes)

    def accepts(self, method: AuthMethod) -> bool:
        return method in self.methods

    def unlocked_by(self, method: AuthMethod, assurance: Assurance) -> tuple[bool, str]:
        """Whether this identity can unlock it, and why not when it cannot.

        Returns the reason rather than logging it, because "why can I not see
        the GitHub connector?" is the single most common question a platform
        like this gets asked, and it should have an answer on screen.
        """
        if not self.accepts(method):
            return False, (
                f"{self.title} is unlocked by "
                f"{', '.join(sorted(m.value for m in self.methods))}, "
                f"not by {method.value}"
            )
        if not assurance.satisfies(self.assurance):
            return False, (
                f"{self.title} requires {self.assurance.name} assurance and this "
                f"sign-in is {assurance.name}; re-authenticate with a stronger method"
            )
        return True, f"{self.title} is unlocked by {method.value}"

    def missing_scopes(self, granted: Iterable[str]) -> tuple[Scope, ...]:
        held = set(granted)
        return tuple(scope for scope in self.required_scopes if scope.name not in held)

    def capabilities_for(self, granted: Iterable[str]) -> tuple[str, ...]:
        """Capabilities actually available under a given scope grant.

        A connector with all its required scopes grants everything it declared.
        Without them it grants nothing — a half-authorised connector that
        silently offered a subset would produce answers whose gaps nobody knew
        to look for.
        """
        return () if self.missing_scopes(granted) else self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "description": self.description,
            "methods": [method.value for method in self.methods],
            "assurance": self.assurance.name,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "capabilities": list(self.capabilities),
            "vendor": self.vendor,
            "docs": self.docs,
            "read_only": self.read_only,
        }

    def __str__(self) -> str:
        return f"{self.title} ({self.id})"


class ConnectorCatalogue:
    """Every connector the platform knows how to offer.

    The catalogue is deliberately separate from what any principal has
    *granted*: the answer to "what could I connect?" must be available before
    anybody has signed in, because that list is the product's shop window.
    """

    def __init__(self, *specs: ConnectorSpec) -> None:
        self._specs: dict[str, ConnectorSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ConnectorSpec) -> ConnectorSpec:
        if spec.id in self._specs:
            raise ConnectorError(f"connector {spec.id!r} is already registered")
        self._specs[spec.id] = spec
        return spec

    def get(self, connector_id: str) -> ConnectorSpec | None:
        return self._specs.get(connector_id)

    def require(self, connector_id: str) -> ConnectorSpec:
        spec = self._specs.get(connector_id)
        if spec is None:
            raise ConnectorError(
                f"no connector named {connector_id!r} "
                f"(known: {', '.join(sorted(self._specs)) or 'none'})"
            )
        return spec

    def unlocked_by(self, method: AuthMethod, assurance: Assurance = Assurance.STANDARD
                    ) -> tuple[ConnectorSpec, ...]:
        """What signing in this way would make available. The upsell, computed."""
        return tuple(
            spec for spec in self._specs.values()
            if spec.unlocked_by(method, assurance)[0]
        )

    def of_kind(self, kind: ConnectorKind) -> tuple[ConnectorSpec, ...]:
        return tuple(spec for spec in self._specs.values() if spec.kind is kind)

    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted({c for spec in self._specs.values() for c in spec.capabilities}))

    def to_dict(self) -> dict[str, Any]:
        return {"connectors": [spec.to_dict() for spec in self._specs.values()]}

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, connector_id: object) -> bool:
        return connector_id in self._specs

    def __iter__(self):
        return iter(tuple(self._specs.values()))

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ConnectorCatalogue {len(self._specs)} connectors>"
