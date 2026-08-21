"""The diff between what was declared and what is running. It touches nothing.

`slpie deploy plan` is the safe half of the pair, and safe here means something
stronger than "does not write": it does not *reach* anything either. A plan is
computed from two models — the manifest and whatever the platform reported — so
it is exactly as fast as reading a file, and it can be produced for a cluster
nobody has credentials to.

── Every change says which direction it goes ────────────────────────────

An operator reading a plan is deciding whether to run it, and the decision turns
on the kind of change rather than the count. Three replicas becoming five and
three becoming one are both "replicas: changed"; only one of them risks
capacity. So a `Change` carries `before` and `after` and renders them, and
`Kind` separates the four cases that a human treats differently:

    ADD       something declared that is not running
    REMOVE    something running that is no longer declared  ← the dangerous one
    SCALE     the same component, a different size
    ALTER     the same component and size, a different shape

`REMOVE` is the one worth naming separately. An apply that quietly deletes a
component because a key was dropped from the manifest is the failure mode this
whole model exists to make visible, and a plan that reported it as "1 change"
would be technically complete and useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .manifest import Component, Deployment


class ChangeKind(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    SCALE = "scale"
    ALTER = "alter"

    @property
    def destructive(self) -> bool:
        """Whether applying this can lose something that is running."""
        return self in (ChangeKind.REMOVE, ChangeKind.SCALE)


@dataclass(frozen=True, slots=True)
class Change:
    """One difference, in the words a plan prints."""

    kind: ChangeKind
    component: str
    field: str = ""
    before: Any = None
    after: Any = None

    def __str__(self) -> str:
        if self.kind is ChangeKind.ADD:
            return f"+ {self.component} — not running, would be created"
        if self.kind is ChangeKind.REMOVE:
            return f"- {self.component} — running, no longer declared"
        return f"~ {self.component}.{self.field}: {self.before} → {self.after}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value, "component": self.component,
            "field": self.field, "before": self.before, "after": self.after,
            "destructive": self.kind.destructive,
            "summary": str(self),
        }


@dataclass(frozen=True, slots=True)
class Plan:
    """What applying this manifest would do, and nothing else."""

    environment: str
    platform: str
    changes: tuple[Change, ...] = ()
    #: Components declared and running and identical. Counted rather than
    #: listed: a plan over a settled estate should be short, and "37 unchanged"
    #: is the useful form of that.
    unchanged: tuple[str, ...] = ()
    gaps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def empty(self) -> bool:
        return not self.changes

    @property
    def destructive(self) -> tuple[Change, ...]:
        return tuple(change for change in self.changes if change.kind.destructive)

    def summary(self) -> str:
        if self.empty:
            return (
                f"{self.environment} matches its manifest — "
                f"{len(self.unchanged)} component(s), nothing to do"
            )
        counts: dict[str, int] = {}
        for change in self.changes:
            counts[change.kind.value] = counts.get(change.kind.value, 0) + 1
        parts = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
        text = f"{self.environment}: {parts}"
        if self.destructive:
            text += f" — {len(self.destructive)} of them can lose something running"
        return text

    def render(self) -> str:
        """The plan as an operator reads it before deciding."""
        lines = [self.summary()]
        if self.changes:
            lines.append("")
            lines.extend(f"  {change}" for change in self.changes)
        if self.gaps:
            lines.append("")
            lines.extend(f"  ! {gap}" for gap in self.gaps)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment, "platform": self.platform,
            "empty": self.empty,
            "changes": [change.to_dict() for change in self.changes],
            "unchanged": list(self.unchanged),
            "destructive": len(self.destructive),
            "gaps": list(self.gaps),
            "summary": self.summary(),
        }


#: The fields a plan compares, and the order it reports them in. Deliberately
#: not `dataclasses.fields(Component)`: `name` is the key rather than a field,
#: and the reflected order would be the declaration order of a dataclass, which
#: is not the order an operator cares about.
COMPARED = ("cpu", "memory", "ingress", "queues")


def plan(declared: Deployment, running: Mapping[str, Mapping[str, Any]] | None = None,
         *, gaps: Sequence[str] = ()) -> Plan:
    """Diff a manifest against what the platform says is running.

    `running` is `{component: {size, cpu, …}}` — deliberately a plain mapping
    rather than a `Deployment`, because it comes from a platform's own reply and
    forcing it through the manifest model would mean inventing values nobody
    reported. What is absent stays absent and is simply not compared.

    A `running` of `None` means *nothing is deployed yet*, which is a first
    install and reads as every component being added. `{}` means the same thing
    and is kept distinct in the caller, not here: a platform that answered "no
    components" and a platform nobody asked are the same plan but not the same
    confidence, and the difference belongs in `gaps`.
    """
    live = dict(running or {})
    changes: list[Change] = []
    unchanged: list[str] = []

    for component in declared.components:
        current = live.pop(component.name, None)
        if current is None:
            changes.append(Change(ChangeKind.ADD, component.name,
                                  field="size", after=component.size))
            continue
        found = _differences(component, current)
        if found:
            changes.extend(found)
        else:
            unchanged.append(component.name)

    # Whatever is left is running and undeclared. Sorted, because a plan is read
    # and compared, and an arbitrary order makes two runs look different.
    for name in sorted(live):
        changes.append(Change(ChangeKind.REMOVE, name, field="size",
                              before=live[name].get("size")))

    return Plan(
        environment=declared.environment,
        platform=declared.platform.value,
        changes=tuple(changes),
        unchanged=tuple(unchanged),
        gaps=tuple(gaps),
    )


def _differences(component: Component, current: Mapping[str, Any]) -> list[Change]:
    found: list[Change] = []

    if "size" in current and int(current["size"]) != component.size:
        found.append(Change(
            ChangeKind.SCALE, component.name, field="size",
            before=int(current["size"]), after=component.size,
        ))

    for name in COMPARED:
        if name not in current:
            # Not reported is not the same as changed. A platform that does not
            # say what CPU a container has must not produce a diff claiming it
            # moved — that is the platform's silence rendered as our finding.
            continue
        want = getattr(component, name)
        have = current[name]
        if name == "queues":
            want, have = tuple(want), tuple(have or ())
        if _same(want, have):
            continue
        found.append(Change(ChangeKind.ALTER, component.name, field=name,
                            before=have, after=want))
    return found


def _same(want: Any, have: Any) -> bool:
    """Equal after the comparisons a manifest and a platform actually differ in.

    `2` and `2.0` are the same CPU allocation written by two systems; reporting
    that as a change would put a line in every plan forever and teach operators
    to skim them.
    """
    if isinstance(want, (int, float)) and isinstance(have, (int, float)):
        return float(want) == float(have)
    return want == have
