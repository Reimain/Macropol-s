"""The gateway: one ordered chain in front of `Api.handle`.

Eight steps, each able to refuse and each saying which one refused. The shape is
`slpie/capture/firewall.py`'s, which took its shape from `slpie/rbac/engine.py`,
so this is the third use of one pattern rather than a third pattern:

    identify → resolve → lifecycle → subscribe → authorize → throttle
             → mediate → dispatch → record

Two properties are load-bearing and both are asserted.

**It adds no privileges of its own.** `authorize` calls `AccessEngine.check` and
nothing else decides access. The live-target guard is not reimplemented here for
the same reason `api.py` does not reimplement it: a second copy of a rule is a
rule that drifts, and the one an API client bypasses.

**`gateway=None` is the default and changes nothing.** The entire pre-existing
suite passing untouched is the proof that the hook is inert until configured,
which is what makes it safe to land before anybody has written a policy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from .action import action_for
from .analytics import Analytics
from .application import Applications
from .catalog import ApiCatalog, ApiDefinition, Operation
from .chain import Chain, Situation, standard
from .credential import CredentialStore
from .errors import ApimError
from .subscription import SubscriptionLedger
from .throttle import Throttler


@dataclass(frozen=True, slots=True)
class Admission:
    """What the gateway decided, and everything needed to render it."""

    allowed: bool
    stage: str = ""                 # which step refused
    status: int = 200
    reason: str = ""
    obligation: str = ""            # what would allow it, when that is knowable
    headers: tuple[tuple[str, str], ...] = ()
    principal: Any = None
    api: str = ""
    operation: str = ""
    application: str = ""
    #: When admission started, so the call's duration is measured over the work
    #: rather than over the check. Zero on the shared `ALLOWED` sentinel, which
    #: is returned for routes the catalogue does not know and never recorded.
    started: float = 0.0

    def body(self) -> dict[str, Any]:
        """The refusal, as the client sees it.

        `refused: true` is what makes the interface render this as a refusal
        rather than as a fault — a 403 in the danger colour teaches people that
        policy is a bug.
        """
        payload: dict[str, Any] = {
            "error": self.reason,
            "refused": True,
            "stage": self.stage,
        }
        if self.obligation:
            payload["obligation"] = self.obligation
        return payload


ALLOWED = Admission(allowed=True)


@dataclass
class Gateway:
    """The chain. Every collaborator is optional and absent means "skip"."""

    catalog: ApiCatalog | None = None
    credentials: CredentialStore | None = None
    subscriptions: SubscriptionLedger | None = None
    applications: Applications | None = None
    access: Any = None                  # rbac.engine.AccessEngine
    throttler: Throttler = field(default_factory=Throttler)
    chain: Chain = field(default_factory=standard)
    analytics: Analytics = field(default_factory=Analytics)
    #: Anonymous callers are admitted to public APIs by default. A deployment
    #: that wants a key on everything sets this False, which is one flag rather
    #: than a policy file nobody has written yet.
    allow_anonymous: bool = True
    environment: str = "*"
    now: Any = time.time

    @classmethod
    def over(
        cls,
        routes: Sequence[tuple[str, str]],
        *,
        access: Any = None,
        **settings: Any,
    ) -> "Gateway":
        """A gateway with a catalogue derived from a route table."""
        return cls(
            catalog=ApiCatalog.from_registry(routes=routes),
            credentials=CredentialStore(),
            subscriptions=SubscriptionLedger(),
            applications=Applications(),
            access=access,
            **settings,
        )

    # -- the chain -------------------------------------------------------

    def admit(self, request: Any) -> Admission:
        """Whether this call proceeds, and which step said so."""
        started = time.monotonic()
        found = self._resolve(request)
        if found is None:
            # A route the catalogue does not know is not the gateway's to
            # refuse: `Api.handle` will 404 it, and refusing here would turn a
            # missing route into a permissions problem for whoever reports it.
            return ALLOWED

        definition, operation = found
        principal, credential = self._identify(request)
        application = credential.application_id if credential else "anonymous"

        for step in (self._lifecycle, self._subscribe, self._authorize, self._throttle):
            verdict = step(request, definition, operation, principal, application)
            if verdict is not None and not verdict.allowed:
                self._record(definition, operation, application, verdict, started)
                return verdict

        # The chain is consulted exactly once per call, and its verdict serves
        # both purposes: refusing, and carrying advisory headers on a call that
        # is allowed. Deciding twice would double every hit count in
        # `chain.report()`, and that report is what an operator reads to work
        # out which rule is actually firing.
        mediated = self.chain.decide(self._situation(
            request, definition, operation, application,
        ))
        if mediated.refuses:
            refusal = Admission(
                allowed=False,
                stage="mediate",
                status=mediated.status or 403,
                reason=mediated.explain(),
                headers=mediated.headers,
                api=definition.api_id,
                application=application,
            )
            self._record(definition, operation, application, refusal, started)
            return refusal

        sent = list(mediated.headers)
        if definition.sunset_at:
            # A deprecation notice belongs on the response a consumer is already
            # getting, not on a mailing list they are not reading.
            sent.append(("Sunset", str(int(definition.sunset_at))))

        # Not recorded here. An admitted call has no outcome yet — the handler
        # has not run — and recording it as 200 at this point is why the
        # analytics screen could only ever show one status class however the
        # request actually ended. `Api.handle` calls `complete()` with what the
        # caller was really sent.
        return Admission(
            allowed=True, principal=principal, api=definition.api_id,
            operation=f"{operation.method} {operation.path}",
            application=application,
            headers=tuple(sent),
            started=started,
        )

    # -- the steps -------------------------------------------------------

    def _resolve(self, request: Any) -> tuple[ApiDefinition, Operation] | None:
        if self.catalog is None:
            return None
        return self.catalog.for_route(request.method, request.path)

    def _identify(self, request: Any) -> tuple[Any, Any]:
        """`Authorization: Bearer slpie_…` → a principal, or anonymous.

        A bad key is *not* refused here. It is refused at `authorize`, as an
        anonymous caller would be, because telling an unauthenticated caller
        which of "the key is wrong" and "the key is fine but you may not do
        this" applies is a distinction worth denying them.
        """
        if self.credentials is None:
            return None, None
        offered = str(getattr(request, "headers", {}).get("authorization", ""))
        if not offered.lower().startswith("bearer "):
            return None, None
        try:
            return self.credentials.authenticate(offered[7:].strip())
        except ApimError:
            return None, None
        except Exception:  # noqa: BLE001 - a provider's own refusal
            return None, None

    def _lifecycle(
        self, request: Any, definition: ApiDefinition, operation: Operation,
        principal: Any, application: str,
    ) -> Admission | None:
        if definition.serves:
            return None
        from .lifecycle import ApiState

        gone = definition.state is ApiState.RETIRED
        return Admission(
            allowed=False,
            stage="lifecycle",
            # 410 rather than 404: it existed, and saying so is the point —
            # a consumer holding a key needs to know it is gone, not to wonder
            # whether they typed the path wrong.
            status=410 if gone else 403,
            reason=(
                f"the {definition.name} API has been retired"
                if gone else f"the {definition.name} API is {definition.state.value}"
            ),
            api=definition.api_id,
        )

    def _subscribe(
        self, request: Any, definition: ApiDefinition, operation: Operation,
        principal: Any, application: str,
    ) -> Admission | None:
        if self.subscriptions is None or definition.visibility == "public":
            return None
        if application == "anonymous":
            return None       # `authorize` refuses it; one refusal, not two
        if self.subscriptions.find(application, definition.api_id, now=self.now()):
            return None
        return Admission(
            allowed=False,
            stage="subscribe",
            status=403,
            reason=(
                f"{application} is not subscribed to the {definition.name} API"
            ),
            obligation=f"subscribe at #/portal/{definition.api_id}",
            api=definition.api_id,
            application=application,
        )

    def _authorize(
        self, request: Any, definition: ApiDefinition, operation: Operation,
        principal: Any, application: str,
    ) -> Admission | None:
        """The only place access is decided. `AccessEngine`, unchanged."""
        if self.access is None:
            return None

        mapped = action_for(
            request.method, request.path,
            environment=self.environment,
            pipeline=str(getattr(request, "body", {}).get("pipeline", "")),
        )
        if mapped.open_to_all:
            return None
        if principal is None and not self.allow_anonymous:
            return Admission(
                allowed=False, stage="identify", status=401,
                reason="this build requires an API key",
                obligation="issue one at #/apps",
                api=definition.api_id,
            )

        # A composition is every stage's action, and a deny on any one refuses
        # the call. `discover . | link | target --live` must be refused because
        # of the last stage, which only a per-stage check catches.
        wanted = mapped.stages or (mapped.action,)
        if mapped.stages == () and request.path == "/api/run":
            return Admission(
                allowed=False, stage="authorize", status=400,
                reason=(
                    "this composition could not be read, so its permissions "
                    "cannot be checked"
                ),
                api=definition.api_id,
            )

        asking = principal if principal is not None else _anonymous()
        for action in wanted:
            decision = self.access.check(
                asking, action, mapped.resource,
            )
            if not _permitted(decision):
                return Admission(
                    allowed=False,
                    stage="authorize",
                    status=403,
                    # `Decision.explain()` verbatim. A second sentence about the
                    # same rule is a second sentence to keep in step with it.
                    reason=_explain(decision, action, mapped.resource),
                    obligation=str(getattr(decision, "obligation", "") or ""),
                    api=definition.api_id,
                    application=application,
                )
        return None

    def _throttle(
        self, request: Any, definition: ApiDefinition, operation: Operation,
        principal: Any, application: str,
    ) -> Admission | None:
        tier = definition.throttle_for(operation)
        # Keyed on the subscription where there is one, so two applications
        # sharing a principal do not share a limit.
        key = f"{application}:{definition.api_id}"
        decision = self.throttler.admit(key, tier)
        if decision.allowed:
            return None
        return Admission(
            allowed=False,
            stage="throttle",
            status=429,
            reason=decision.reason,
            obligation=f"retry in {int(decision.retry_after + 0.5)}s",
            headers=decision.headers(),
            api=definition.api_id,
            application=application,
        )

    def _mediate(
        self, request: Any, definition: ApiDefinition, operation: Operation,
        principal: Any, application: str,
    ) -> Admission | None:
        verdict = self.chain.decide(self._situation(
            request, definition, operation, application,
        ))
        if not verdict.refuses:
            return None
        return Admission(
            allowed=False,
            stage="mediate",
            status=verdict.status or 403,
            reason=verdict.explain(),
            headers=verdict.headers,
            api=definition.api_id,
            application=application,
        )

    # -- the bits around them --------------------------------------------

    def _situation(
        self, request: Any, definition: ApiDefinition, operation: Operation,
        application: str,
    ) -> Situation:
        body = getattr(request, "body", {}) or {}
        return Situation(
            api=definition.api_id,
            version=definition.version,
            operation=f"{operation.method} {operation.path}",
            method=operation.method,
            path=operation.path,
            application=application,
            tier=definition.throttle_for(operation),
            bytes=len(str(body)),
            state=definition.state.value,
            cacheable=operation.cacheable,
        )

    def _advisory(
        self, definition: ApiDefinition, operation: Operation, application: str,
    ) -> tuple[tuple[str, str], ...]:
        """Headers that travel with an allowed call.

        A deprecation notice belongs on the response a consumer is already
        getting, not on a mailing list they are not reading.
        """
        verdict = self.chain.decide(Situation(
            api=definition.api_id, method=operation.method, path=operation.path,
            state=definition.state.value, cacheable=operation.cacheable,
            application=application, tier=definition.throttle_for(operation),
        ))
        sent = list(verdict.headers)
        if definition.sunset_at:
            sent.append(("Sunset", str(int(definition.sunset_at))))
        return tuple(sent)

    def _record(
        self, definition: ApiDefinition, operation: Operation, application: str,
        admission: Admission, started: float,
    ) -> None:
        self.analytics.record(
            api=definition.api_id,
            operation=f"{operation.method} {operation.path}",
            application=application,
            status=admission.status,
            refused_by="" if admission.allowed else admission.stage,
            seconds=time.monotonic() - started,
        )

    def complete(self, admission: Admission, status: int) -> None:
        """Record an admitted call, once its real status is known.

        Refusals are recorded by the chain that made them — the gateway knows
        those outcomes itself. Everything it let through is recorded here, so
        "by outcome" means what the caller received rather than what the
        gateway decided.

        Silent for a call it never admitted: a route outside the catalogue gets
        the shared `ALLOWED` sentinel, and counting those would report traffic
        on APIs that do not exist.
        """
        if not admission.allowed or not admission.api:
            return
        self.analytics.record(
            api=admission.api,
            operation=admission.operation,
            application=admission.application or "anonymous",
            status=status,
            refused_by="",
            seconds=max(0.0, time.monotonic() - admission.started),
        )

    def status(self) -> dict[str, Any]:
        """What the gateway screen renders."""
        return {
            "apis": len(self.catalog) if self.catalog else 0,
            "chain": self.chain.report(),
            "throttle": self.throttler.status(),
            "analytics": self.analytics.summary(),
            "subscriptions": (
                self.subscriptions.status() if self.subscriptions else {}
            ),
            "anonymous_allowed": self.allow_anonymous,
        }


def _permitted(decision: Any) -> bool:
    """True when the RBAC engine allowed it, whatever shape it answers in."""
    outcome = getattr(decision, "outcome", None)
    if outcome is not None:
        return str(getattr(outcome, "value", outcome)).lower() == "allowed"
    return bool(decision)


def _explain(decision: Any, action: str, resource: str) -> str:
    explain = getattr(decision, "explain", None)
    if callable(explain):
        try:
            return str(explain())
        except Exception:  # noqa: BLE001 - never fail while explaining a failure
            pass
    return f"{action} on {resource} is not permitted"


def _anonymous() -> Any:
    """A principal for a caller who presented no key.

    Built rather than passing `None`, so the *engine's* default-deny answers
    the question. Short-circuiting an unauthenticated caller here would be the
    gateway inventing an access rule, which is exactly what this module is
    arranged not to do — and it would answer differently from the engine the
    moment somebody bound a role to anonymous callers on purpose, which is a
    legitimate thing to want for a public read API.
    """
    from ..identity.principal import AuthMethod, Principal

    return Principal(
        issuer="urn:slpie:idp:anonymous",
        subject="anonymous",
        method=AuthMethod.API_KEY,
    )
