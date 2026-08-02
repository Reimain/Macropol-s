# Audit — the state of the codebase after phase 13

Taken at commit `16b6753`, before phase 14 begins the ring restructure. 328 source modules,
2403 tests, 45 verbs, **82.3%** statement coverage (4714 lines uncovered).

The point of writing this down is that phase 14 splits the tree into rings
(`slpie/`, `slpie_enterprise/`, `clients/`). Every problem below is cheap to fix now and
multiplies once there are three trees instead of one.

Findings are grouped by whether they are **measured** (reproducible right now, with the
command that shows them) or **structural** (a judgement about naming and layout). Measured
first, because those are defects rather than opinions.

---

## Part 1 — measured

### 1.1 Four tests pass over zero files instead of failing

The worst finding, because it makes a restructure report itself as safe.

`tests/test_reuse_boundaries.py` walks the crawler with a hardcoded glob:

```python
for module in GRATIMOS.rglob("crawl/**/*.py"):      # :64, :80, :124
    ...
assert not offenders, "the crawler must stay stdlib-only"
```

Rename `gratimos/crawl` to anything and the glob matches nothing. `assert not offenders`
over an empty set passes. Three tests go green having checked nothing:

```
$ python -c "from pathlib import Path; G=Path('gratimos');
  print(len(list(G.rglob('crawl/**/*.py'))), len(list(G.rglob('fetch/**/*.py'))))"
12 0
```

Same shape elsewhere:

| Site | Glob | Silently stops covering |
|---|---|---|
| `tests/test_reuse_boundaries.py:64,80,124` | `rglob("crawl/**/*.py")`, `rglob("reuse/**/*.py")` | a renamed package |
| `tests/test_slpie_dispatch.py:52,73` | `glob("*.py")` — **not** `rglob` | anything moved into a subpackage |
| `tests/test_slpie_boundaries.py:70` | `allowed_prefixes = ("binding/", "simulator/", "cli.py", "ui/")` | `cli.py` becoming `cli/` |
| `tests/test_reuse.py:42` | `pytestmark = skipif(not bridge.AVAILABLE)` | all 49 reuse tests, on a broken import |

That last one is the quietest: if `slpie.domain` moves, `bridge.AVAILABLE` goes false and
1435 lines of `gratimos/reuse` stop being tested, reported as skips.

**Fix:** a non-empty guard on every filesystem walk.

```python
modules = sorted(GRATIMOS.rglob("crawl/**/*.py"))
assert modules, "the crawl glob matched nothing — did the package move?"
```

### 1.2 Nothing runs the test suite

`.github/workflows/` contains one file, `jekyll-docker.yml`. It builds the docs site.

The only automated enforcement is `.pre-commit-config.yaml`, which runs exactly two test
files — `tests/test_slpie_boundaries.py` and `tests/test_slpie_audit.py`. Those are the two
most layout-fragile files in the repo (§1.1, §1.5), and the audit hook only fires on changes
under `slpie/audit/`.

So: 2403 tests, no CI.

### 1.3 Forty-seven bare raises, with the taxonomy class sitting unused

Both taxonomies open by stating the rule they are there to enforce:

> `slpie/errors.py:3` — Subsystems route on exception *type*, never on message text. Every
> failure mode the platform can produce deliberately is named here.

47 sites raise a builtin instead. The two worst clusters are the ones where the right class
already exists and is imported nowhere:

| Sites | Raises | Available since |
|---|---|---|
| `slpie/reasoning/l4_linking.py:46`, `l5_validation.py:57`, `l6_constraints.py:41`, `l7_impact.py:90`, `l8_optimize.py:55` | `ValueError` | `ReasoningError` — `slpie/errors.py:178` |
| `gratimos/meta/cast.py:92,105,163,169,216` | `ValueError` ×5 | `CastError` — `gratimos/errors.py:49` |
| `gratimos/transforms/policy.py:97` | `ValueError` | `SandboxViolation` — `gratimos/errors.py:79` |
| `gratimos/hubs/memory.py:85`, `hubs/spill.py:48` | `ValueError`, `TypeError` | `HubError` — `gratimos/errors.py:56` |

Five of the eight reasoning layers do it. `slpie/domain/finding.py` does both in one
constructor — `EvidenceRequired` at `:241`, then `ValueError` at `:242`.

Three subsystems have no taxonomy class at all and so raise builtins legitimately:
`slpie/spill/` (`budget.py:96,138`, `ident.py:74,99`, `sequence.py:173`, `store.py:61`),
`slpie/simulator/` (`clock.py:71`, `scenarios.py:73,341`), `slpie/core/bus.py:93`. Those need
`SpillError`, `SimulatorError`, `BusError` rather than a rewrite.

Separately: **`PolicyError` exists in both taxonomies** (`slpie/errors.py:197`,
`gratimos/errors.py:109`) with different roots and different meanings, so
`from ...errors import PolicyError` is ambiguous to a reader.

### 1.4 Capabilities no surface can reach

§24's thesis is that a capability the platform has and no surface reaches is drift. The
largest instance:

| Capability | Verb | CLI | Manual |
|---|---|---|---|
| `Engine.simulate()` — materialise the declared world | — | — | — |
| `Engine.fire(scenario)` — **12 scenarios** | — | — | — |
| `Engine.seal()` — content-addressed snapshot | — | — | — |
| `Engine.rebuild()` — refold from sequence 0 | — | — | — |

```
$ python -c "from slpie.simulator.scenarios import available; print(len(available()))"
12
```

Twelve scenarios — `cve`, `major-bump`, `unmaintained`, `duplicate-versions`,
`license-change`, `service-dies`, `capability-refused`, `contract-broken`,
`boundary-breach`, `shadow-dependency`, `declaration-drift`, `partial-scan` — each carrying
its own `expect_findings`/`expect_gaps` as data, and none reachable from any surface.

Commands the plan's §21 advertises that do not exist: `init`, `ui`, `simulate`, `snapshot`,
`deploy`, `scale`, `cost`, `region`. Two of those are named in user-facing text:

* `slpie/compose/verb.py:190` tells the user to run `slpie init` when no environment is
  declared. There is no `init`.
* `slpie/demo/runner.py:107` prints `slpie ui` in its "Next:" footer. There is no `ui`.

### 1.5 `--root` is ignored when opening an environment

`slpie/cli.py:319` looks for the manifest relative to the process CWD:

```python
for candidate in ("slpie.environment.yaml", "slpie.environment.yml"):
    path = Path(candidate)          # not context.root, not options["root"]
```

So:

```
$ python -m slpie.cli --root /tmp/enginedemo status
failed at `status`: VerbError: status needs an environment; declare one with a
manifest first (`slpie init` scaffolds it) — ...
```

Two defects in one line: the flag is ignored, and the remediation names a command that does
not exist.

The same shadowing bug in a different form affects four verbs: `Param("path", default=".")`
is materialised by `Verb.bind()` into `arguments`, where it wins the
`arguments.get("path") or context.root` chain — so `Context.root` is silently ignored by
`analysis`, `audit`, `capture` and (until fixed) `incremental`. Fixed for `changed` in
`16b6753`; the other three remain.

### 1.6 Coverage, and where it is thin

**82.3% overall.** The thinnest files, and the shape of the gap:

| File | Coverage | Statements |
|---|---:|---:|
| `gratimos/transforms/_runner.py` | 0% | 106 |
| `slpie/governance/policies.py` | 19% | 240 |
| `gratimos/probes/media.py` | 24% | 202 |
| **`slpie/compose/verbs/environment.py`** | **27%** | 67 |
| `slpie/governance/security/advisories.py` | 28% | 314 |
| `slpie/discovery/infrastructure/kubernetes.py` | 44% | 178 |
| `slpie/capture/strategies.py` | 48% | 264 |
| `slpie/compose/verbs/guidance.py` | 48% | 92 |

The fourth row is the one that matters. `slpie/compose/verbs/environment.py` holds
`declare attach scan reconcile graph search impact gaps status target` — the ten verbs that
require a live `Engine`, and therefore the ten that only an end-to-end run against a real
environment exercises. The audit's white space and the acceptance run's purpose are the same
gap.

Subpackages with **no dedicated test file**, largest first: `slpie/compose/verbs` (3264 LOC,
tested only indirectly), `slpie/environment` (1319), `slpie/artifacts` (1239 — the codegen
bridge the whole boundary invariant is built around), `slpie/incremental` (1148, folded into
a phase-named file), `slpie/connectors` (1058), `slpie/agent` (686), `slpie/planner` (573).
In `gratimos/`: `distill` (797), `ontology` (587), `trace` (400).

Thinnest by ratio: `gratimos/storage` + `gratimos/hubs` share one 16-test file across 2025
LOC.

### 1.7 Declared markers, unused

`pyproject.toml` declares `slow` and `network`. **No test uses either.** So the mechanism for
excluding the 10 000-node graph test (`tests/test_slpie_graph.py:493`, asserts
`elapsed < 5.0`) and the real-subprocess tests exists and is unwired — they always run in the
default suite, and they are the ones that flake on a loaded runner.

---

## Part 2 — structural

### 2.1 Duplication in the fixtures

`tests/conftest.py` carries 2 of the suite's 70 fixtures, and neither is used by any of the
24 slpie-facing test files.

| Fixture | Copies | Where |
|---|---:|---|
| `repository(tmp_path)` | 8 | `compose:46`, `contract:36`, `manual:45`, `suggest:62`, `phase10:48`, `phase13:76`, `governance:170`, `enterprise:1095` |
| `verbs()` — `return registry()` | 8 | same files |
| `run(pipeline, root, verbs)` | 2 | `governance`, `enterprise` |
| `cli(...)` harness | 3 | `manual:59`, `demo:73`, `suggest:542` |
| `imported_roots(path)` | 3 | `boundaries:26`, `reuse_boundaries:29`, and a *different* reimplementation at `audit:337` |

Four of the eight `repository()` fixtures are byte-identical. The literal
`AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"` appears verbatim in three files.

The `imported_roots` triplication is the costly one: any fix to import resolution — and
`tests/test_slpie_audit.py:165` proves relative-import handling matters — must land in three
places, one of which is subtly different already.

### 2.2 One path, three sources of truth

`slpie/artifacts/codegen.py` is the single module permitted to import Gratimos
(invariant 8). That fact is written down three times:

* `slpie/audit/engine.py:50` — `"allowed": "slpie.artifacts.codegen"` (the product rule)
* `tests/test_slpie_boundaries.py:19` — `CODEGEN_BRIDGE = "artifacts/codegen.py"`
* `tests/test_slpie_audit.py:354` — `assert offenders == ["slpie/artifacts/codegen.py"]`

Three spellings of one path, in three formats. Moving the bridge requires all three, and the
third is an exact repo-relative string literal in a list — the single most rename-fragile
line in the suite. Same shape for the licence bridge (`slpie/audit/engine.py:54`,
`tests/test_reuse_boundaries.py:22`).

### 2.3 Names that collide

Unqualified names carrying different meanings:

| Name | × | The divergence |
|---|---:|---|
| `registry.py` | 7 | verb registry · discoverer table · element attachments · tool table · plugin registry · probe table · agent cards |
| `engine.py` | 5 | the platform · suggestion ranking · audit runner · authorization decisions · a backward-chaining prover |
| `rules.py` | 4 | governance · audit · policymaker · logic |
| `class Outcome` | 6 | scenario · dispatch · demo beat · **an enum** in rbac · shell automation · validation budget |
| `class Finding` | 3 | `slpie/domain` · `slpie/rbac/audit` · `gratimos/shell/command` |
| `class Tool` | 3 | agent tool · external binary · reference entry |
| `class Observation` | 2 | a discovery fact · a calibration sample |
| `keyring.py` | 2 | credentials · a **path-keyed data structure** |
| `world.py` | 2 | `SimulatedWorld` · demo fixture writer |
| `PolicyError` | 2 | across the two taxonomies (see §1.3) |

`slpie/reasoning/` (the L1–L8 package) vs `slpie/domain/reasoning.py` (the dataclasses) vs
`gratimos/reason/` (a Prolog-style prover) is the same problem at package level;
`slpie/reasoning/__init__.py:19` already has to disambiguate in prose.

### 2.4 Two spellings, both exported

| Concept | Both live | Evidence |
|---|---|---|
| materialise / materialize | **yes** | `slpie/simulator/materialize.py:43 def materialize` and `slpie/discovery/registry.py:252 def materialise`, both exported. `slpie/simulator/world.py` imports `materialize` at `:31` and raises "was not materialised" at `:140` |
| licence / license | 197 / 133 | `licence_ok`, `licence_verdict` in `gratimos/reuse/` vs `license_rules`, `license_incompatible` in `slpie/governance/` |
| normalise / normalize | both | `slpie/normalize/licenses.py:153` returns a `Normalised` from `normalize_license` |
| catalogue / catalog | both | `slpie/connectors/catalogue.py` vs `gratimos/hubs/metahub.py:196 def catalog` |

### 2.5 `render()` vs `explain()`

Both mean "produce a human-readable string". The split is arbitrary, and the clearest
evidence is one package doing both with near-identical stated intent:

* `slpie/rbac/audit.py:165` — `render()`, *"ready to attach to a ticket"*
* `slpie/rbac/engine.py:179` — `explain()`, *"pasted into a ticket unedited"*

`gratimos/validate/budget.py` has both, at `:118` and `:254`. Signatures diverge too:
`render(self)`, `render(self, *, width=74)`, `render(self, *, width=78)`,
`render(self, *, verbose=False)`, and `slpie/agent/tools.py:83 render(self, value: str)`
which takes an argument and means something else entirely.

The good pattern exists once: `slpie/incremental/errors.py:95`, where `__str__` delegates to
`explain()`.

### 2.6 Serialization is one-way

290 `to_dict`, 32 `from_dict`. Not a defect on its own — most types are write-only wire
formats — but it is undocumented which round-trip, and the asymmetries land in odd places:
`Finding.from_dict` exists while its own `Remediation` field cannot be reconstructed;
`Fingerprint.from_dict` exists but `Skip` had no `from_dict` until `16b6753`.

The four `from_dict` signatures in use take `data`, `body`, `payload`, or
`(data, evidence)` — same contract, four spellings.

### 2.7 Test files named for phases

| File | Actually covers |
|---|---|
| `tests/test_slpie_phase10.py` | `slpie/reasoning`, `slpie/linking` |
| `tests/test_slpie_phase13.py` | `slpie/incremental`, `slpie/agent` |
| `tests/test_slpie_discovery_phase8.py` | `discovery/{ecosystems,infrastructure,interfaces}` |
| `tests/test_slpie_discovery_phase8b.py` | more of the same; the `b` means nothing outside git history |

And `test_phase_eight_is_complete` (`_phase8b.py:442`) is named for a milestone rather than a
behaviour. Phases are the order things were built, which stops being interesting the moment
they are built.

Related: the `agent-tools` verb sits in the `incremental` group, next to `changed`, with
which it has nothing in common.

### 2.8 Export conventions

Every `__init__.py` declares `__all__` except three, all in `slpie/discovery/`
(`code/`, `ecosystems/`, `scm/`) — their two siblings `interfaces/` and `infrastructure/` do.

Two incompatible styles, with no stated rule:

* **module names** — `slpie/artifacts` → `["c4", "codegen", "sbom"]`, also `normalize`,
  `governance`, `discovery/{interfaces,infrastructure}`
* **symbols** — everything else, e.g. `slpie/domain` with 66 names

So `from slpie.domain import Node` works and `from slpie.artifacts import SbomDocument` does
not, and the caller has to know which convention each package chose. The two root packages
diverge hardest: `slpie/__init__.py` exports 2 names, `gratimos/__init__.py` exports 33 with
a lazy-import mechanism.

---

## What this becomes

Remediation is §29 of the master plan, in five stages: harden the suite first (so the rename
has a net that fails loudly), then apply one stated naming rule, then build a corpus of real
artifacts to replace the hand-rolled fixtures, then make `simulate` and `fire` reachable, then
`acceptance.py` — one root script that runs the platform end to end and proves four things
about the run.

The rule this audit is written to serve: **a capability the platform has and no surface can
reach is drift, and a test that passes without checking anything is worse than no test.**
