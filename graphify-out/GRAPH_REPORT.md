# Graph Report - .  (2026-07-31)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 10076 nodes · 24887 edges · 242 communities (233 shown, 9 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 1412 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `50a17a7f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- EvidenceKind
- test_slpie_domain.py
- Evidence
- Node
- VerbRegistry
- ContextFlow
- Urn
- Engine
- a2a/__init__.py
- TransformRegistry
- Verb
- test_storage_hubs.py
- test_slpie_demo.py
- Audit
- ModuleRegistry
- test_slpie_auth.py
- Key
- test_slpie_discovery.py
- FieldShape
- LedgerRecord
- Verdict
- test_reasoning.py
- DomainEvent
- EventKind
- Context
- test_slpie_manual.py
- slpie/errors.py
- astmerge.py
- test_reuse.py
- binding/resolver.py
- ProjectionSet
- ConnectorSpec
- PolicyEngine
- governor.py
- test_crawl.py
- Fact
- test_slpie_simulator.py
- test_slpie_ledger.py
- Path
- Pipeline
- AgentError
- Environment
- test_slpie_core.py
- test_slpie_normalize.py
- test_shell.py
- api.py
- Wrapped
- test_slpie_capture.py
- openapi.py
- test_slpie_binding.py
- capture/__init__.py
- test_slpie_plugins.py
- RoleGraph
- crawl/__init__.py
- Composition
- Host
- test_slpie_contract.py
- Candidate
- test_slpie_suggest.py
- test_slpie_rbac.py
- plan_for
- SimulatedWorld
- Ontology
- assess.py
- RunLedger
- test_slpie_discovery_phase8b.py
- Module
- advisories.py
- test_slpie_discovery_phase8.py
- rbac/audit.py
- test_slpie_audit.py
- test_slpie_jwt.py
- suggest/__init__.py
- MigrationLedger
- Format
- QueryBus
- Keyring
- ResponseCache
- capture/parse.py
- DataShape
- test_slpie_graph.py
- History
- SlpieClient
- SlpieClient
- SlpieClient
- shell/__init__.py
- SqliteGraph
- Touch
- Declaration
- test_runtime.py
- ExecutionContext
- slug
- Registry
- versions.py
- simulator/__init__.py
- Attention
- test_reference.py
- web/package.json
- Config
- coordinates.py
- Version
- graph/__init__.py
- A2AClient
- Chain
- ids.py
- MetaHub
- c4.py
- JwtError
- RbacError
- Requirement
- maven.py
- Policy
- Journal
- sbom.py
- Cli
- ImpactResult
- Protocol
- validate/__init__.py
- Enrichment
- new_id
- ledger.py
- test_validate.py
- RuleSet
- claude.py
- connectors.py
- ProbeRegistry
- Bin
- SlpieError
- JsonProbe
- test_a2a.py
- infer_shape
- RuleFamily
- LocalRepository
- Block
- test_slpie_ui.py
- XlsxProbe
- Artifact
- Response
- MemoryBudget
- probes/__init__.py
- test_slpie_dispatch.py
- Advisory
- GitHubSource
- probes/media.py
- Binding
- ToolRegistry
- test_slpie_station.py
- GateResult
- Engine
- Resolved
- test_slpie_linking.py
- Need
- Capture
- reference/__init__.py
- Reference
- load
- Evaluation
- GratimosError
- dispatch/registry.py
- PipelineResult
- Routine
- .discover
- Origin
- database.py
- Assessment
- Candidate
- test_slpie_constraints.py
- EventStream
- BacktrackingSolver
- Guard
- constraints/__init__.py
- PyPISource
- Verdict
- Decision
- Station
- .of
- Solution
- git.py
- Target
- Affected
- Capability
- app.js
- test_probes.py
- shell/environment.py
- ObjectRef
- calibration.py
- _raw
- StorageError
- Report
- Joined
- shaping.py
- Assurance
- Command
- FilesystemConnector
- Page
- tauri.conf.json
- register_builtins
- C4Element
- Suggestions
- compose.js
- probe
- GraphProjection
- Fetcher
- Permission
- Separation
- .gaps
- test_reuse_boundaries.py
- Linked
- PluginManifest
- RobotsDecision
- blocks
- LinkerSet
- UpgradeStep
- _runner.py
- extract
- .from_dict
- matches_resource
- Focus
- test_slpie_boundaries.py
- ConnectorRegistry
- LocalDispatcher
- DeltaStore
- S3Store
- Any
- .to_rule
- Outcome
- test_an_illegal_filter_is_rejected_rather_than_recorded
- ImportToPackageLinker
- Change
- _fingerprint_callable
- Interval
- .refuse
- Traversal
- .to_dict
- ResolutionError
- sw.js
- example_normalize.py
- server
- code/__init__.py
- ecosystems/__init__.py
- gratimos

## God Nodes (most connected - your core abstractions)
1. `Evidence` - 123 edges
2. `DomainEvent` - 95 edges
3. `Context` - 93 edges
4. `VerbRegistry` - 90 edges
5. `Node` - 88 edges
6. `ContextFlow` - 83 edges
7. `Wrapped` - 79 edges
8. `EvidenceKind` - 78 edges
9. `DiscoveryResult` - 77 edges
10. `DataShape` - 72 edges

## Surprising Connections (you probably didn't know these)
- `FakeOrchestrator` --uses--> `ClaudeConfig`  [INFERRED]
  tests/test_a2a.py → gratimos/a2a/adapters/claude.py
- `FakeOrchestrator` --uses--> `ClaudeExecutor`  [INFERRED]
  tests/test_a2a.py → gratimos/a2a/adapters/claude.py
- `test_asking_with_no_capable_peer_raises()` --calls--> `AgentRegistry`  [EXTRACTED]
  tests/test_a2a.py → gratimos/a2a/registry.py
- `FakeOrchestrator` --uses--> `ExecutionContext`  [INFERRED]
  tests/test_a2a.py → gratimos/a2a/server.py
- `test_auth_never_appears_in_a_repr()` --calls--> `HttpAuth`  [EXTRACTED]
  tests/test_a2a.py → gratimos/a2a/transport.py

## Import Cycles
- None detected.

## Communities (242 total, 9 thin omitted)

### Community 0 - "EvidenceKind"
Cohesion: 0.02
Nodes (238): declares(), depends(), empty(), evidence_at(), imports(), Any, Observation, What a discoverer is, and the helpers every one of them needs. A discoverer… (+230 more)

### Community 1 - "test_slpie_domain.py"
Cohesion: 0.02
Nodes (150): The one Gratimos module that imports SLPIE. Everything else goes through it.…, Any, _range_facts(), What the shared range parser makes of a NuGet version range. NuGet's interval…, aggregate_risk(), Finding, FindingKind, Gap (+142 more)

### Community 2 - "Evidence"
Cohesion: 0.02
Nodes (158): A discoverer's output: nodes and edges, each with its evidence. Submitted as…, RecordObservation, Discoverers for infrastructure: containers, orchestration and IaC. These read…, discover_kubernetes(), looks_like_kubernetes(), Kubernetes manifests — what is scheduled, and what it is wired to. Two things…, Cheap pre-check so a values.yaml is not parsed five times over., Read every document in the stream, and report what each one declares. (+150 more)

### Community 3 - "Node"
Cohesion: 0.02
Nodes (106): Edge, EdgeKind, Any, Enum, str, Edges — the relationships, and the platform's central invariant. **No…, Derived from evidence. Deliberately a property, so it cannot be assigned., The audit trail for this edge's confidence, for explanations and the UI. (+98 more)

### Community 4 - "VerbRegistry"
Cohesion: 0.03
Nodes (96): The empty flow a source verb is handed., parse(), `a --x 1 | b | c` → three stages. Never runs, never expands., Parse, validate and run one pipeline. The one-call form., run(), Any, Kind, Verbs by group, for a `--help` that is readable rather than a wall. (+88 more)

### Community 5 - "ContextFlow"
Cohesion: 0.03
Nodes (62): KeyringEvent, Any, An immutable entry on the ContextFlow spine. `paths` is what makes concurrent…, Content address of the event, excluding its position in history., Return a copy carrying the clock and base assigned at append time., ContextFlow, Any, True when ``version`` is on the live chain leading to ``of``. (+54 more)

### Community 6 - "Urn"
Cohesion: 0.04
Nodes (77): excerpt_at(), line_of(), module_urn(), The identity of a source module inside an element., The literal line, for the explanation to quote., deployment_urn(), Source, The deployment a compose file describes — named for where it lives. (+69 more)

### Community 7 - "Engine"
Cohesion: 0.04
Nodes (58): _as_fact(), Derivation, Engine, ForwardReport, _partial(), Proof, Any, Rule (+50 more)

### Community 8 - "a2a/__init__.py"
Cohesion: 0.06
Nodes (62): UiPath interop: an A2A connection interface over the UiPath Agent SDK. UiPath…, Calling other agents. :class:`A2AClient` is the outbound half of the protocol.…, A2A: speaking to other agents, and being spoken to., The peer registry: who else is out there, and what can they do. An agent that…, AgentServer, Executor, Hosting an A2A agent. :class:`AgentServer` implements the protocol surface —…, An A2A endpoint: a card, a task store, and an executor behind them. (+54 more)

### Community 9 - "TransformRegistry"
Cohesion: 0.04
Nodes (59): AsyncFunctionDef, Attribute, FunctionDef, Global, A transformation module failed to load or execute., Static or runtime policy rejected a transformation., SandboxViolation, TransformError (+51 more)

### Community 10 - "Verb"
Cohesion: 0.04
Nodes (60): Flow, Kind, Any, Enum, Gap, str, What travels down the pipe. A shell pipe carries bytes, which is why `curl | jq…, The next flow: new value, everything explanatory carried forward. Gaps… (+52 more)

### Community 11 - "test_storage_hubs.py"
Cohesion: 0.04
Nodes (62): HubError, Routing, channel, or hub registration failure., all_of(), any_of(), by_name(), by_probe(), by_source(), Channel (+54 more)

### Community 12 - "test_slpie_demo.py"
Cohesion: 0.04
Nodes (78): _agree(), _check(), _contract(), _http(), _pipe(), _pipeline(), _plan(), _projection() (+70 more)

### Community 13 - "Audit"
Cohesion: 0.05
Nodes (69): Judgement, audit_self(), Auditor, generic_checks(), Any, Projection, Running the judge — over our tree, or over anybody's. The audit takes a *tree*,…, This repository, against its own stated invariants. (+61 more)

### Community 14 - "ModuleRegistry"
Cohesion: 0.04
Nodes (54): ConflictPolicy, Decision, merge_sources(), MergeResult, Any, Why one symbol ended up the way it did., The merged source and a full account of how it was reached., Three-way merge two versions of a generated module. ``base`` is the previously… (+46 more)

### Community 15 - "test_slpie_auth.py"
Cohesion: 0.03
Nodes (70): builtin_catalogue(), Everything SLPIE knows how to be connected to, as declarations., Principal, An authenticated identity, and the terms on which we believe it., Stable across re-authentication, email changes and display renames. The issuer…, What to put in a log line or an error, without leaking more than needed., Mint an agent principal acting on this principal's authority. The agent…, ApiKeyProvider (+62 more)

### Community 16 - "Key"
Cohesion: 0.05
Nodes (54): Who is calling, proved — with nothing installed. The kernel verifies real…, Key, One verification key. Its `alg` is authoritative, the token's is not. A key…, AuthMethod, IdentityError, PrincipalKind, Enum, str (+46 more)

### Community 17 - "test_slpie_discovery.py"
Cohesion: 0.04
Nodes (78): discover_javascript_source(), _mask(), _module_path(), Source, Blank comments, and replace each string's body with a reference to it. Two…, Turn a masked capture back into the literal it stands for., The path within its element, so a module urn does not repeat the name., Scan one JS or TS file for the modules it pulls in. (+70 more)

### Community 18 - "FieldShape"
Cohesion: 0.05
Nodes (64): CastError, A value could not be cast into the requested type under the active policy., cast_records(), cast_value(), Caster, CastMode, CastReport, detect_media() (+56 more)

### Community 19 - "LedgerRecord"
Cohesion: 0.04
Nodes (49): EventKind, Any, Enum, str, Domain events — the write side of CQRS, and the only source of truth. The…, Derive a follow-on event that records this one as its cause. ``kind`` and…, The closed taxonomy of things that can happen. Closed on purpose: a projector…, Whether the graph projection has to react to this. (+41 more)

### Community 20 - "Verdict"
Cohesion: 0.04
Nodes (41): Distillation — remembering what the agent said, so it need not be asked twice.…, Any, Enum, str, The acknowledged base — what has been validated, and how much it is still…, Confidence discounted for age, and capped for being a recollection. Exponential…, Whether this can answer on its own, or only inform a fresh look., The verdict as a fact the reasoning engine can chain over. Provenance is… (+33 more)

### Community 21 - "test_reasoning.py"
Cohesion: 0.04
Nodes (65): KnowledgeBase, Retire it. Kept rather than deleted — how the answer changed matters., Everything the system has been told and has not forgotten. Indexed by need…, Remember a judgement, superseding any earlier one it replaces., Retire what is known about a subject, because reality moved. The hook that…, Record that the agent had to be asked. Drives the savings figure., DistillationLoop, Answers needs, calling the validator only when memory cannot. Holds a knowledge… (+57 more)

### Community 22 - "DomainEvent"
Cohesion: 0.08
Nodes (72): AskQuestion, ChangeTarget, Command, DeclareEnvironment, DeriveEnrichment, DetachElement, FireScenario, GenerateArtifact (+64 more)

### Community 23 - "EventKind"
Cohesion: 0.04
Nodes (48): compare(), happens_before(), LogicalClock, merge(), Ordering, Enum, str, Causal ordering primitives. Wall clock time cannot order events produced by… (+40 more)

### Community 24 - "Context"
Cohesion: 0.06
Nodes (75): The steps that assert something about the world. A filter keeping the first…, Context, What a verb is allowed to reach. Everything ambient, named. Passed explicitly…, A verb was declared or invoked wrongly., VerbError, _constraints(), _discover(), _findings() (+67 more)

### Community 25 - "test_slpie_manual.py"
Cohesion: 0.05
Nodes (77): The process-wide registry, built once from the built-in verbs., registry(), groups(), offline(), Any, The recipes — where composition is taught rather than asserted. A verb list…, The recipes that work with no manifest and no database., One composition worth knowing, and why it is shaped that way. (+69 more)

### Community 26 - "slpie/errors.py"
Cohesion: 0.05
Nodes (46): CapabilityRefused, ConstraintUnsatisfiable, ContradictoryEvidence, EvidenceError, PluginError, PluginProtocolError, PluginQuarantined, Error taxonomy for SLPIE. Subsystems route on exception *type*, never on… (+38 more)

### Community 27 - "astmerge.py"
Cohesion: 0.04
Nodes (61): _assemble(), diff_symbols(), _identify(), _merge_imports(), ModuleParts, parse_module(), Enum, str (+53 more)

### Community 28 - "test_reuse.py"
Cohesion: 0.06
Nodes (70): Crawl, validate, gate, rank — the four steps, composed once., ReuseAssessor, licence_obligation(), licence_ok(), Any, The obligation class alone — permissive, weak-copyleft, and so on. Used for…, Whether a package supporting `supported` can run on `required`. `required` is a…, A bridged answer, in Gratimos' own vocabulary. Deliberately not SLPIE's… (+62 more)

### Community 29 - "binding/resolver.py"
Cohesion: 0.05
Nodes (33): Connector, Any, The Connector protocol — byte-identical on both sides of the binding. This…, A connector for something that will not let us in. A refusal is a first-class…, One thing a connector can hand back, and where it came from. ``uri`` is always…, How the platform reaches an element. Identical shape, simulated or live., Whether the element can be reached at all right now., Fetch one resource. Raises :class:`BindingError` when it cannot. (+25 more)

### Community 30 - "ProjectionSet"
Cohesion: 0.04
Nodes (31): ABC, Build a platform. Everything in memory unless given paths., Attachment, CountingProjection, FindingProjection, Projection, ProjectionSet, Any (+23 more)

### Community 31 - "ConnectorSpec"
Cohesion: 0.05
Nodes (36): The connectors shipped in the box. Fifteen declarations, no implementations.…, Connectors that activate when authentication arrives. A principal signs in;…, Activation, GrantStatus, KeyringEvent, Offer, Enum, str (+28 more)

### Community 32 - "PolicyEngine"
Cohesion: 0.06
Nodes (35): Condition, PolicyError, No policymaker could decide, or a decision was rejected., One difference between two revisions of a shape., Whether replaying this change can invalidate existing readers., ShapeChange, Policy: named rules, explicable verdicts., _channel() (+27 more)

### Community 33 - "governor.py"
Cohesion: 0.05
Nodes (34): Budget, __getattr__(), Gratimos — a self-building agent kernel. Point it at an environment. It reads…, Resolve the reuse-path exports on first access. `gratimos.reuse` reaches SLPIE…, Budget, Depth, Any, IntEnum (+26 more)

### Community 34 - "test_crawl.py"
Cohesion: 0.05
Nodes (39): Transport, RateLimiter, One request per host per interval, by sleeping rather than by dropping. Per…, CrawlPolicy, official_policy(), Any, How long to wait before retry number ``attempt`` (1-based). An explicit…, A policy restricted to the registries that publish an API for this. (+31 more)

### Community 35 - "Fact"
Cohesion: 0.05
Nodes (34): Feed the engine an observation and propagate it. This is how a crawl result…, Add facts and work out what now follows. The default is to propagate, because a…, Fact, FactBase, Any, Content-addressed on the claim itself, not on how it was reached. Two routes to…, False for an inference that cites nothing., Unify against a fact, extending bindings. None when it cannot. Extends rather… (+26 more)

### Community 36 - "test_slpie_simulator.py"
Cohesion: 0.04
Nodes (40): skipif, Clock, ControlledClock, A controllable clock, so bitemporal behaviour is testable. The graph has two…, Time, as the platform reads it., Nanoseconds since the epoch., Real time. What a live target runs on., Time that only moves when told to. Two properties make it useful rather than… (+32 more)

### Community 37 - "test_slpie_ledger.py"
Cohesion: 0.05
Nodes (40): open_ledger(), Any, A durable ledger for a path, or an in-memory one when there is none. The…, An append-only event store on disk. Connections are per-thread: SQLite objects…, SqliteLedger, InMemoryLedger, EventKind, Everything that ever happened to one thing, in order. (+32 more)

### Community 38 - "Path"
Cohesion: 0.06
Nodes (50): main(), Where the kernel keeps what it builds. Everything the run produces lands under…, Workspace, _checks_for(), _declared(), Which checks apply to this tree. A tree we have not been told about gets the…, A tree's own declared dependencies, from its packaging metadata., Path (+42 more)

### Community 39 - "Pipeline"
Cohesion: 0.08
Nodes (48): Layer, LayerNumber, int, The ten layers, named. Ordering is the pipeline's execution order., The layered pipeline — where evidence becomes understanding, traceably. Ten…, DiscoveryLayer, L1 — what was read, recorded as facts that later layers may cite. The discovery…, Raw observations in; grounded first-order facts out. (+40 more)

### Community 40 - "AgentError"
Cohesion: 0.06
Nodes (32): _arguments_of(), _job_id(), job_to_task(), Any, Message, A minimal Orchestrator REST client. Deliberately stdlib-only. When the official…, Published processes (releases) visible in the configured folder., Poll a job until it leaves the running states. (+24 more)

### Community 41 - "Environment"
Cohesion: 0.05
Nodes (26): Ladder, Any, Guard, IntEnum, Consecutive successes since the last failure. Promotion runs on this. Total…, Whether this could ever be automated. Fixed by risk, not by history., A move on the ladder, and why. Every one of these is worth recording., Holds routines and moves them up and down. Never runs anything. (+18 more)

### Community 42 - "test_slpie_core.py"
Cohesion: 0.05
Nodes (39): DeliveryReport, EventBus, EventKind, The event bus — ordered delivery, at-least-once, deduplicated on event id.…, Redeliver history, ignoring the dedupe window. This is how a projection…, Per-subscriber delivery state — rendered directly in the UI., A subscriber that just keeps events. The test fixture, and the SSE backlog., Anything that reacts to events. Projectors are the main implementers. (+31 more)

### Community 43 - "test_slpie_normalize.py"
Cohesion: 0.06
Nodes (48): Whether two references name one package, version aside. The question linking…, same_package(), _forms(), from_classifiers(), Normalised, normalize_license(), Any, Every spelling worth looking up, most literal first. Returning several rather… (+40 more)

### Community 44 - "test_shell.py"
Cohesion: 0.07
Nodes (60): analyse(), parse(), Tokenise a command line. Never runs anything, never expands anything., Read a command and report everything worth knowing before it runs. `trusted`…, machine(), Shell intelligence: the analyser, the guard, and the ladder to one click. The…, `-rf` and `-r -f` must be the same thing, which naive checks get wrong., The empty-expansion incident: `rm -rf $BUILD/` with BUILD unset. (+52 more)

### Community 45 - "api.py"
Cohesion: 0.06
Nodes (27): BaseHTTPRequestHandler, The interface layer failed., UiError, Api, Any, The HTTP API — a thin skin over the query and command buses. Every read route…, One parsed HTTP request., One JSON response, with a status the server maps onto HTTP. (+19 more)

### Community 46 - "Wrapped"
Cohesion: 0.05
Nodes (22): DataHub, Any, Decide residency, evicting or staging as the budget requires., Move a resident payload to the spill tier., Add a channel at runtime — delegates to the router., Holds payloads, routes them, and keeps memory honest., Route, shape-register, and hold (or stage) a payload., Any (+14 more)

### Community 47 - "test_slpie_capture.py"
Cohesion: 0.05
Nodes (59): identify(), What this is. Reads only as far as the requested depth. The order is decisive-…, _mp4_duration(), The `mvhd` atom's timescale and duration. Nested, so it is searched for.…, Which media container this is, or None. Reads only the header., sniff(), parse(), Depth (+51 more)

### Community 48 - "openapi.py"
Cohesion: 0.10
Nodes (36): _channel_key(), discover_asyncapi(), _message_name(), _operations_v2(), _operations_v3(), _protocols(), Any, Source (+28 more)

### Community 49 - "test_slpie_binding.py"
Cohesion: 0.04
Nodes (36): Guard, Any, Target, Gatekeeper for anything that reaches the real environment., Decide an element's target, refusing an unconfirmed live binding., Refuse a write against a live target unless writing was granted., Permit writes to the live environment. Deliberately explicit. Takes a reason…, What the guard permitted and blocked — rendered in the Simulator view. (+28 more)

### Community 50 - "capture/__init__.py"
Cohesion: 0.06
Nodes (45): BlockKind, Enum, str, One canonical document, so four container formats stop being four dialects.…, What a piece of a document is. Small on purpose: every format maps here., Whether it groups other blocks rather than carrying text itself., Format intelligence and the capture firewall. Every store is downstream of a…, at_body() (+37 more)

### Community 51 - "test_slpie_plugins.py"
Cohesion: 0.06
Nodes (47): Limits, Any, What an external plugin is allowed to consume., Whether this platform can enforce them at all., A callable to run in the child, after fork and before exec. Returns None where…, A minimal environment for the child. Deliberately narrow. A plugin inheriting…, go_plugin(), npm_handler() (+39 more)

### Community 52 - "RoleGraph"
Cohesion: 0.09
Nodes (32): AccessEngine, Roles, bindings and decisions. Embedded, deterministic, explainable. No server,…, allow(), Every defined role, with inheritance resolved and validated. Resolution is…, Refuse dangling parents, cycles, and chains too deep to read., Longest inheritance chain below a role. Computed separately from `_walk`…, These roles plus everything they inherit., Everything a role grants, own and inherited, denies first. Denies are ordered… (+24 more)

### Community 53 - "crawl/__init__.py"
Cohesion: 0.07
Nodes (38): A conditional-request cache — the difference between re-crawling and re-asking.…, Crawler, CrawlResult, Engine, The crawler — a need goes in, grounded facts about real packages come out. This…, What one crawl found, and what it could not see., Turns a need into observed facts about packages that might answer it., FetchReport (+30 more)

### Community 54 - "Composition"
Cohesion: 0.06
Nodes (31): ParseError, Any, Reading a pipeline the way a shell reads one. `scan --changed | link | findings…, Split on `|` outside quotes. `||` is a separator too, not a pipe carrying a…, Stages back to one line. Round-trips through `parse`., A pipeline could not be read. It names the stage that failed., One verb invocation in a pipeline, parsed but not yet validated., Back to text. Round-trips, which is what makes `--explain` honest. (+23 more)

### Community 55 - "Host"
Cohesion: 0.06
Nodes (21): Host, Point, Any, An extension plus its live state. The host's bookkeeping., The plug/deplug plane. Owns lifecycle, isolation and ordering., Register an extension. Nothing is imported yet., Activation order: dependencies first, then declaration order. Stable rather…, Import, construct, check the contract, and activate. Never raises. Contained on… (+13 more)

### Community 56 - "test_slpie_contract.py"
Cohesion: 0.07
Nodes (56): _error(), _flow_schema(), _gap_schema(), openapi(), _operation_id(), Any, A typed client, generated. A route change becomes a compile error. That is the…, Every route the contract declares. Compared against the server's own. This is… (+48 more)

### Community 57 - "Candidate"
Cohesion: 0.06
Nodes (35): Budget, Candidate, Queue, Peaks at 0.5, zero at either extreme. What we might actually learn., Sub-linear in dependents, so no single hub swallows the budget., Expected information gain per unit spent., What may be spent, and what has been. Mutable by design — it is a meter., What may be spent *now* — the reserve is held back for escalations. (+27 more)

### Community 58 - "test_slpie_suggest.py"
Cohesion: 0.05
Nodes (46): The claimed routines, keyed. Persisted as plain JSON., A short, mnemonic, unused key. From the *name* first — a reviewer who calls it…, Routines, mint_key(), A short deterministic key for a pipeline. Deterministic so that the same…, engine(), fixture, parametrize (+38 more)

### Community 59 - "test_slpie_rbac.py"
Cohesion: 0.07
Nodes (52): parse_rule(), The five roles every deployment starts with. Five, not fifty. A shipped role…, One permission from one line. Errors quote the offending line verbatim. A…, system_roles(), engine(), human(), fixture, RBAC: the decisions, the refusals, and the access review that finds the holes.… (+44 more)

### Community 60 - "plan_for"
Cohesion: 0.06
Nodes (41): The planner — it writes a composition, and shows it before running it. `slpie…, Intent, _path(), Any, Reading a question well enough to compose an answer, with no model. This is the…, What shape of answer a question wants, and what it is about., A question → an intent. Never guesses a target it did not see evidence for., The most likely thing being asked about. Empty when nothing stands out. (+33 more)

### Community 61 - "SimulatedWorld"
Cohesion: 0.08
Nodes (39): boundary_breach(), capability_refused(), contract_broken(), cve_lands(), declaration_drift(), duplicate_versions(), _first_codebase(), _first_network() (+31 more)

### Community 62 - "Ontology"
Cohesion: 0.07
Nodes (27): Concept, Domain, _normalise(), Ontology, OntologyError, Any, Enum, str (+19 more)

### Community 63 - "assess.py"
Cohesion: 0.04
Nodes (40): Gate, base_ontology(), The starting lattice. Small on purpose. It covers the concepts a reuse query…, ConstraintKind, extract_concepts(), Enum, str, A need — what the context requires, in a form that can be looked up. The whole… (+32 more)

### Community 64 - "RunLedger"
Cohesion: 0.06
Nodes (26): _clip(), Any, One agent invocation, recorded so a reviewer can check it., Content-addressed, so a record cannot be edited without becoming another., Whether a credential was found in what was sent to the model., The short record the paper asks for — one screen, not a transcript., Every agent run, append-only, queryable by what it produced., Record a run. Re-appending identical content is a no-op, not an error. (+18 more)

### Community 65 - "test_slpie_discovery_phase8b.py"
Cohesion: 0.07
Nodes (50): at(), kinds(), objects(), Cargo, Go modules, GraphQL, gRPC and MQTT — the rest of Discovery II. The tests…, Direct and indirect need different remediation advice., It states a version the module refuses. An edge would put it in every blast…, A replaced requirement is not what gets built., Left in place it closes the first type early and everything after is lost. (+42 more)

### Community 66 - "Module"
Cohesion: 0.06
Nodes (27): Widen the import set — the deliberate way to allow a dependency., _call_name(), Definition, _dotted(), Import, Module, project(), Projection (+19 more)

### Community 67 - "advisories.py"
Cohesion: 0.06
Nodes (41): The strongest observation — what an explanation leads with., The single most trustworthy observation — what an explanation leads with., strongest(), _bounds_for_caret(), _bounds_for_tilde(), _bounds_for_wildcard(), Comparator, Op (+33 more)

### Community 68 - "test_slpie_discovery_phase8.py"
Cohesion: 0.08
Nodes (49): objects(), parametrize, Discovery II: JVM, .NET, containers, orchestration, IaC and interface…, Scope is a qualifier. Dropping test dependencies hides real CVEs., `group:` `name:` `version:` — the form a regex over strings misses., Attribute and child-element — the second is easy to miss entirely. The version…, `FROM build` names an earlier stage, not something on a registry., One `---` file holding a Deployment and a Service is the normal case. (+41 more)

### Community 69 - "rbac/audit.py"
Cohesion: 0.08
Nodes (36): Binding, _concentration(), _depth(), _elevations(), _expired(), Finding, _is_write(), Kind (+28 more)

### Community 70 - "test_slpie_audit.py"
Cohesion: 0.07
Nodes (47): audit(), Check, One call. Uses the generic checks unless a tree's own are supplied., One rule with the options that aim it at a particular tree., ours(), fixture, The judge — deterministic, reproducible, and honest about its blind spots. Four…, Asserting somebody else's layer rules would be inventing their architecture and… (+39 more)

### Community 71 - "test_slpie_jwt.py"
Cohesion: 0.11
Nodes (44): b64url_encode(), parse(), Split and decode a compact JWS. **This verifies nothing.** Named `parse` rather…, Verify a token end to end, and return it only if every check passed. Order…, verify(), claims(), keypair(), _prime() (+36 more)

### Community 72 - "suggest/__init__.py"
Cohesion: 0.07
Nodes (31): EventKind, Enum, str, Where the reviewer's attention actually is — inferred, then made deterministic.…, One observable act of attention. Deliberately few and unambiguous., How much this signal indicates *confusion*, not interest. A click is close to…, Whether this signal suggests the reviewer is *getting* somewhere. Only a plain…, SignalKind (+23 more)

### Community 73 - "MigrationLedger"
Cohesion: 0.09
Nodes (16): MigrationLedger, Operation, Any, A ledger entry: one atomic set of operations with a parent., Inverse operations in reverse order; empty when not reversible., Append-only chain of revisions per entity, with replay and rollback., Record a pending revision for a set of shape changes., The initial revision for a newly discovered entity. (+8 more)

### Community 74 - "Format"
Cohesion: 0.05
Nodes (32): digest_of(), Hit, Enum, str, The capture firewall. Ordered chains, default deny, explicit deny wins. A…, One rule's outcome, recorded whether or not it decided the verdict., What the chain decided. Ordered by severity of refusal., Content address for a capture. Quarantine keeps this even when it keeps nothing… (+24 more)

### Community 75 - "QueryBus"
Cohesion: 0.08
Nodes (32): causation_chain(), Walk back to the root cause — why did the platform do this? Returned oldest-…, Causation, History, LedgerIntegrity, OpenFindings, ProjectionStatus, Any (+24 more)

### Community 76 - "Keyring"
Cohesion: 0.07
Nodes (17): ConnectorSpec, _digest(), Grant, Keyring, Any, Safe to render anywhere: there is no secret in a grant to redact., Grants, as an append-only log with a derived view of the present. The log is…, Everything this principal could connect, and why not where they cannot.… (+9 more)

### Community 77 - "ResponseCache"
Cohesion: 0.07
Nodes (22): Entry, _header(), _key(), Any, Response, In-memory by default; durable when given a directory. Persistence matters more…, The stored entry, fresh or not. Freshness is the caller's question., A response usable without contacting the origin, or None. (+14 more)

### Community 78 - "capture/parse.py"
Cohesion: 0.09
Nodes (42): A block standing in for something that could not be read. Its existence is the…, unreadable(), at_line(), _lines(), _media(), _model(), _pdf(), Block (+34 more)

### Community 79 - "DataShape"
Cohesion: 0.08
Nodes (19): emit_module(), GeneratedModule, _literal(), module_name_for(), Any, PythonEmitter, Emitting Python from shapes. Generated code has to be readable by the people…, Render a JSON-ish value as readable Python source. (+11 more)

### Community 80 - "test_slpie_graph.py"
Cohesion: 0.07
Nodes (32): Turns a graph's raw traversal output into explainable results. A thin layer on…, One query for every label, rather than one per result row., Traverser, depends(), package(), populated(), fixture, The graph: bitemporal projection, in-SQL traversal, snapshots and diffs. (+24 more)

### Community 81 - "History"
Cohesion: 0.08
Nodes (26): History, Any, Teaching by acceptance — the learned half, kept auditable. The system does not…, How much to favour this suggestion here. Bounded, always. Acceptances in the…, Whether this has been refused often enough to stop offering it. Scoped to the…, Whether anything has been learned about this situation yet., Why this sits where it sits. The learned part, made interrogable., Accepted paths, in the order they were taken. The routine candidate. (+18 more)

### Community 82 - "SlpieClient"
Cohesion: 0.08
Nodes (7): ClientOptions, Flow, Gap, Kind, SlpieClient, VERB_TYPES, VerbName

### Community 83 - "SlpieClient"
Cohesion: 0.08
Nodes (7): ClientOptions, Flow, Gap, Kind, SlpieClient, VERB_TYPES, VerbName

### Community 84 - "SlpieClient"
Cohesion: 0.08
Nodes (7): ClientOptions, Flow, Gap, Kind, SlpieClient, VERB_TYPES, VerbName

### Community 85 - "shell/__init__.py"
Cohesion: 0.08
Nodes (28): Outcome, The ladder from suggestion to one click — and the rails that make it safe. The…, One recorded run. The evidence promotion is built from., Analysis, Category, Finding, Any, Enum (+20 more)

### Community 86 - "SqliteGraph"
Cohesion: 0.05
Nodes (20): has_fts5(), The graph schema — bitemporal tables, and the indexes that make them usable.…, Whether this SQLite was built with FTS5. Checked rather than assumed: search…, _nullable(), Any, The graph projection. Rebuildable from the ledger at any moment., Batch writes into one transaction. Connections run in autocommit, which is…, Insert or supersede a node, carrying its evidence with it. Upsert rather than… (+12 more)

### Community 87 - "Touch"
Cohesion: 0.10
Nodes (21): _evidence_of(), Any, A failed command. The reviewer knows least here, so be concrete., Nothing came back. Almost always the filter, or the wrong stage., A reviewer opened a finding. They need the evidence and the blast radius., The dictionary case — and the reason this is not a dictionary. A definition…, What is a RESOLUTION?" — answered by producing one., A suggestion that would not run must never be offered. The short key is a… (+13 more)

### Community 88 - "Declaration"
Cohesion: 0.11
Nodes (35): Declaration, One declared element. The unit the platform attaches, tracks and checks., The specific kind where the declaration says so, the default otherwise., Where this element physically is — a path, a URL, or a broker., Whether it lives on the filesystem, and so can be materialised., Artifact, _codebase(), container_artifacts() (+27 more)

### Community 89 - "test_runtime.py"
Cohesion: 0.07
Nodes (35): Extension, extensions_from_config(), One declared extension. Data until it is activated., Read `[[extension]]` blocks out of a loaded configuration. Config carries…, ExplodesOnActivate, ExplodesOnConstruction, ExplodesOnUse, Incomplete (+27 more)

### Community 90 - "ExecutionContext"
Cohesion: 0.09
Nodes (13): Poll a task until it reaches a terminal state or needs input., ExecutionContext, Any, Artifact, Message, The skill the caller asked for, if they named one., Drop the oldest terminal tasks first; never evict live work., What an executor is handed, and how it reports progress. (+5 more)

### Community 91 - "slug"
Cohesion: 0.10
Nodes (15): AgentRegistry, Peer, Any, Register a remote peer by URL., Register an in-process :class:`~gratimos.a2a.server.AgentServer`., Highest-priority healthy peer that can do the work., Send work to the best-matching peer., Ask every matching peer — for consensus or comparison. (+7 more)

### Community 92 - "Registry"
Cohesion: 0.09
Nodes (15): InProcess, Any, Gap, Convenience for built-ins — the same path, less ceremony., Register an external plugin from its manifest on disk., Load every plugin under a directory. A directory whose manifest is broken is…, Which plugins claim this file, in priority order. Several may claim one file,…, Run every plugin claiming a source, merging what they produce. A plugin that… (+7 more)

### Community 93 - "versions.py"
Cohesion: 0.06
Nodes (47): IdentityError, InvalidPurl, An identifier could not be parsed, normalized, or rendered., A Package URL is malformed or violates the purl specification., Canonical identity — making two dialects for one package land on one node. A…, canonical_coordinate(), canonical_purl(), canonical_string() (+39 more)

### Community 94 - "simulator/__init__.py"
Cohesion: 0.08
Nodes (19): _contradicted(), Fault, FaultInjector, FaultKind, FaultyConnector, Any, Enum, str (+11 more)

### Community 95 - "Attention"
Cohesion: 0.09
Nodes (23): Attention, from_events(), Any, One observation about attention. Aggregated, then discarded., This signal's contribution. Dwell scales with time; the rest do not., Accumulates signals and reduces them to one focus. Holds no trail. Deliberately…, Focus per subject, capped at 1. Aggregate only — no ordering kept. Hesitation…, Where attention settled. Declines to guess when nothing stands out. (+15 more)

### Community 96 - "test_reference.py"
Cohesion: 0.05
Nodes (18): paper(), protocols(), fixture, The reference registries, and the discipline that keeps them honest. The load-…, Single-vendor governance is a real adoption risk, so it is queryable., The registry and the code must not drift apart silently., We derive confidence; we have never measured whether it is calibrated., A reference listing only what we already do is a brochure. (+10 more)

### Community 97 - "web/package.json"
Cohesion: 0.05
Nodes (38): dependencies, react, react-native, description, devDependencies, typescript, react, name (+30 more)

### Community 98 - "Config"
Cohesion: 0.07
Nodes (25): Config, Layered settings, resolved once, explainable afterwards., The winning setting, or None. The single resolution path., Fetch, or raise naming what would have to be set and where. The error lists the…, Every resolved key under a prefix, with the prefix stripped., The whole layer stack for one key. The incident-response answer., Configured but never read — usually a typo in a key name. The most common…, Every resolved value. Secrets stay redacted unless explicitly revealed. (+17 more)

### Community 99 - "coordinates.py"
Cohesion: 0.07
Nodes (36): CoordinateError, describe(), _maven(), Any, IdentityError, Native coordinates ↔ purl — the cross-walk convergence actually needs. Every…, `group:artifact[:packaging[:classifier]]:version` — the ambiguous one. Three…, A purl → the notation that ecosystem's own tooling accepts. Reports render this… (+28 more)

### Community 100 - "Version"
Cohesion: 0.07
Nodes (11): _atoms(), Any, Maven `1.0.0.RELEASE`, PEP 440 `1.0rc1`, Go pseudo-versions, and friends., Where a qualified version sits relative to the bare release. Semver rule 11…, Which level changed — the input to upgrade risk scoring., A disjunction of conjunctions: ``(a AND b) OR (c)``. Every ecosystem's dialect…, Highest allowed version — the default upgrade target., `post1` -> `["post", 1]`. Without this a marker glued to its number is an… (+3 more)

### Community 101 - "graph/__init__.py"
Cohesion: 0.09
Nodes (24): The knowledge graph — a bitemporal projection of the ledger, traversed in SQL.…, _current_ids(), diff_ids(), GraphDiff, _ids_at(), Any, Snapshots — a content-addressed name for the graph at a moment. A snapshot id…, A digest over the ordered live id sets. Sorted before hashing so that discovery… (+16 more)

### Community 102 - "A2AClient"
Cohesion: 0.09
Nodes (18): A2AClient, Exchange, Any, Part, Speaks A2A to one peer., Connect to a peer over HTTP., Send a message and return the resulting exchange., Send text, get text. The simplest possible delegation. (+10 more)

### Community 103 - "Chain"
Cohesion: 0.08
Nodes (28): Chain, default_chain(), Depth, One rule in a chain. Ordered, and every one is evaluated. `silent` is what…, An ordered rule chain. Default deny; explicit deny always wins., The deepest any rule needs. What the caller must parse to evaluate it. Computed…, A chain is always truthy, even empty. Without this, `__len__` makes an empty…, The chain every capture passes unless an operator replaces it. Ordered… (+20 more)

### Community 104 - "ids.py"
Cohesion: 0.07
Nodes (21): MemoryBudgetExceeded, A payload could not be held in memory and no spill target accepted it., Memory budget: how much the kernel is allowed to hold. "Hold it in memory if…, Record a payload as resident. Call after any required eviction., One payload's occupancy record., Residency, MetaHub: the registry of what the kernel believes about its data. Shapes are…, _b32() (+13 more)

### Community 105 - "MetaHub"
Cohesion: 0.09
Nodes (12): LineageEdge, MetaHub, Any, Register the shape carried by a wrapper, and record its lineage., What would change if this observation were adopted., Walk back through derivations, breadth-first, cycle-safe., The shape of the whole context, as a policymaker would read it., One accepted belief about an entity's structure. (+4 more)

### Community 106 - "c4.py"
Cohesion: 0.10
Nodes (27): _alias(), _build(), c4_views(), C4Level, C4View, code_view(), component_view(), container_view() (+19 more)

### Community 107 - "JwtError"
Cohesion: 0.08
Nodes (26): b64url_decode(), b64url_int(), _check_claims(), ClaimRejected, JwtError, Any, JWS verification, in the standard library alone. Signing in with Google or…, RSASSA-PKCS1-v1_5 verification, from the arithmetic up. Recovers the encoded… (+18 more)

### Community 108 - "RbacError"
Cohesion: 0.09
Nodes (17): Condition, Outcome, Enum, str, The decision engine. Every answer says why, including the refusals. An…, Plug in customer logic without touching the kernel. Refuses to replace an…, Why a decision went the way it did. Distinguishes the three refusals., Whether the caller could do something about it and retry. (+9 more)

### Community 109 - "Requirement"
Cohesion: 0.09
Nodes (24): An index built from what discovery already found. No network, ever. This is the…, Candidates, newest first — the order a solver should try them in. Sorted by the…, One package asking for a version window of another. `requested_by` is not…, The bare package name, for messages a human reads., The range as an algebra. Raises `VersionError` on a range we cannot read., Whether a candidate satisfies this requirement. An unreadable range **admits…, Requirement, StaticIndex (+16 more)

### Community 110 - "maven.py"
Cohesion: 0.13
Nodes (32): find_line(), The 1-based line a substring first appears on, or 0. Line numbers are what make…, child(), children(), _dependency(), _dependency_nodes(), descendants(), discover_pom() (+24 more)

### Community 111 - "Policy"
Cohesion: 0.08
Nodes (29): PolicyError, A governance rule could not be registered or evaluated., _as_sequence(), _attribute(), Condition, load_policies(), load_policy_file(), _numeric() (+21 more)

### Community 112 - "Journal"
Cohesion: 0.11
Nodes (26): ArgumentParser, build_parser(), cmd_connectors(), cmd_govern(), cmd_report(), cmd_scan(), cmd_serve(), cmd_shapes() (+18 more)

### Community 113 - "sbom.py"
Cohesion: 0.10
Nodes (30): _components(), cyclonedx_document(), _cyclonedx_licenses(), _dependencies(), _direct_dependencies(), _hashes(), _license_expression(), Any (+22 more)

### Community 114 - "Cli"
Cohesion: 0.11
Nodes (18): Cli, _line(), main(), Any, Flow, A claimed key to its composition. Checked only when the token is not a verb and…, `slpie contract --openapi | --typescript` — the generated contract. Emitted…, `slpie plan "<question>"` — write the composition, then show it. (+10 more)

### Community 115 - "ImpactResult"
Cohesion: 0.08
Nodes (10): Cycle, Impacted, ImpactResult, Any, A circular dependency, normalised so it compares equal from any entry., What breaks if this changes., What this transitively rests on., One node reached by a traversal, with how far and how certainly. (+2 more)

### Community 116 - "Protocol"
Cohesion: 0.08
Nodes (11): Protocol, Any, The protocols, addressable by id, layer or what they interoperate with., Everything recorded as speaking a given protocol., The ones no single vendor can unilaterally change., Every specification this registry rests on. Read before relying on it., One interoperability protocol, as recorded — with where to check it., Registry (+3 more)

### Community 117 - "validate/__init__.py"
Cohesion: 0.08
Nodes (26): BudgetError, Outcome, Enum, str, Spending a finite validation budget on the claims that most deserve it. This is…, One validation that actually happened., A budget was misconfigured, or asked to spend what it does not have., What the budget is denominated in. One run, one unit. (+18 more)

### Community 118 - "Enrichment"
Cohesion: 0.09
Nodes (17): Enrichment, One derived fact, and the chain it came from. ``derived_from`` holds evidence…, False for a fact asserted out of nowhere by a layer., LayerContext, Any, The ids at the end of the chain — the raw evidence it rests on., What a layer reads, and where it puts what it worked out. Deliberately mutable…, Append one derived fact. Refuses to overwrite, and refuses to float. An… (+9 more)

### Community 119 - "new_id"
Cohesion: 0.09
Nodes (8): Transport, FilePart, part_from_dict(), Any, Part, A file, either inline (base64) or by URI., new_id(), `evt_01J2...` — prefix makes ids self-describing in traces and logs.

### Community 120 - "ledger.py"
Cohesion: 0.11
Nodes (21): MigrationError, A data-as-code mutation could not be recorded or replayed., alembic_available(), AlembicAdapter, _pg_cast(), Any, Rendering ledger revisions as Alembic migration scripts. The ledger is the…, Renders ledger revisions into Alembic scripts. (+13 more)

### Community 121 - "test_validate.py"
Cohesion: 0.13
Nodes (30): Calibrator, Collects predictions and outcomes, and says what the numbers are worth., feed(), The validation plane: measured confidence, recorded runs, spent budget. The…, Not the same fault: this wastes validation budget rather than misplacing trust., A predictor wrong in both directions must not look honest., Its interval is half the number line wide; calling it wrong invents findings., Adjusting from noise is how a system argues itself into a wrong number. (+22 more)

### Community 122 - "RuleSet"
Cohesion: 0.10
Nodes (17): _attribute(), An ordered collection of rules that runs all of them., Register one rule. Duplicate ids are refused, never overwritten. Two rules…, Run every matching rule and return everything they found. Not the first…, Stamp a finding with the rule that raised it, if it did not do so itself., Every rule family registered at the RULE extension point, as one set., One thing the platform knows how to check. ``matches`` is separate from…, registered_rules() (+9 more)

### Community 123 - "claude.py"
Cohesion: 0.12
Nodes (19): claude_agent(), ClaudeConfig, ClaudeExecutor, ClaudeTool, hub_tools(), Any, Claude as an A2A agent. Wraps the Anthropic Messages API in the…, Drives a task by asking Claude, with an optional tool loop. (+11 more)

### Community 124 - "connectors.py"
Cohesion: 0.11
Nodes (17): ObjectStore, Minimal, synchronous blob interface., _available(), _CloudSpec, ConnectorSpec, default_registry(), _delta_factory(), _file_factory() (+9 more)

### Community 125 - "ProbeRegistry"
Cohesion: 0.10
Nodes (12): Probe, A reader for one family of sources., Confidence in ``[0, 1]`` that this probe can read the target., Read the target and return shaped payloads., Discovery, ProbeRegistry, Any, Target (+4 more)

### Community 126 - "Bin"
Cohesion: 0.07
Nodes (12): Bin, Any, One confidence bucket: what was claimed, what happened., Signed: positive means overconfident, negative means underconfident., Whether the claim is within tolerance, *or* the interval covers it. The second…, What the numbers turned out to be worth., Whether there is enough data to say anything at all., One sentence, written for somebody deciding whether to trust a score. (+4 more)

### Community 127 - "SlpieError"
Cohesion: 0.12
Nodes (28): The primary front door. `slpie` is a composition runner before it is anything…, crossable(), decode(), encode(), _evidence_in(), _gap_in(), Any, Flow (+20 more)

### Community 128 - "JsonProbe"
Cohesion: 0.10
Nodes (17): _BaseAddress, JsonProbe, Any, JSON documents and JSON Lines streams., Find the record-bearing parts of a document. A top-level object whose values…, AccessDenied, ApiProbe, fetch() (+9 more)

### Community 129 - "test_a2a.py"
Cohesion: 0.11
Nodes (28): build_card(), Build an agent card without assembling the nested objects by hand. Spec-defined…, Expose an :class:`AgentServer` over HTTP using the standard library. Returns a…, serve_http(), auditor(), build_server(), parametrize, The A2A surface: wire types, task lifecycle, transports, and the adapters. (+20 more)

### Community 130 - "infer_shape"
Cohesion: 0.14
Nodes (26): infer_shape(), Infer a shape from an iterable of records. Accepts rows as mappings, sequences…, operations_for(), Translate shape changes into ledger operations., Policymakers, Any, Convenience façade binding the engine to the four decision points., Explicable decisions, and the reversible ledger they act on. (+18 more)

### Community 131 - "RuleFamily"
Cohesion: 0.29
Nodes (4): A rule set behind the plugin registry's callable contract. Rule families…, Register a rule set with the plugin registry. Returns the Registration., register_rule_family(), RuleFamily

### Community 132 - "LocalRepository"
Cohesion: 0.11
Nodes (10): LocalRepository, PathLike, Drop staged objects by age or count, newest kept first., Remove every object. Only ever touches paths under the root., Path-contained, atomic, owner-private object store on local disk., Map a key to a path, refusing anything that escapes the root., parametrize, test_local_repository_refuses_paths_outside_its_root() (+2 more)

### Community 133 - "Block"
Cohesion: 0.11
Nodes (12): Block, Document, Any, A normalised document, and an honest account of what could not be read., Everything readable, in reading order. For search and for extraction., Whether everything in the source was read. Reported, never assumed., How much of it was read, as a fraction. Never rounded up to 1.0., Blocks whose text contains this. Each one carries its own locator. (+4 more)

### Community 134 - "test_slpie_ui.py"
Cohesion: 0.11
Nodes (24): call(), parametrize, The interface: API contract, SSE delivery, and offline self-containment. The…, The gate is enforced at the write side. A UI that enforced its own copy would…, The architectural dividend: a live feed is a subscriber, not a poller., test_a_confirmed_target_change_is_accepted(), test_a_node_comes_back_with_its_evidence_and_both_edge_directions(), test_an_asset_path_cannot_escape_the_app_directory() (+16 more)

### Community 135 - "XlsxProbe"
Cohesion: 0.13
Nodes (16): date, column_index(), CsvProbe, excel_serial_to_date(), Any, Element, Target, Tabular probes: delimited text and Excel workbooks. The XLSX reader is native.… (+8 more)

### Community 136 - "Artifact"
Cohesion: 0.07
Nodes (17): Artifact, Any, Whether there is enough here to judge it at all., Everything a concept extractor should look at, concatenated once., Combine two sources' views, preferring whichever actually knows. A GitHub…, Candidates matching these concept terms, best first., Everything known about one named package, or None if absent., A source could not answer, or answered in a shape it does not promise. (+9 more)

### Community 137 - "Response"
Cohesion: 0.09
Nodes (14): Any, Replays captured responses. The offline half of the seam. Unknown URLs return…, Register a payload. JSON-serialisable values are encoded for you., What came back, plus how we got it., Case-insensitive lookup — HTTP header casing is not dependable., Parse as JSON, naming the URL when it is not. A registry returning an HTML…, RecordedTransport, Response (+6 more)

### Community 138 - "MemoryBudget"
Cohesion: 0.08
Nodes (9): Admission, MemoryBudget, Any, Decide whether a payload can be held, and what to evict if not. This does not…, Drop a payload from the ledger, returning the bytes freed., Least-recently-touched first; pinned entries are never candidates., Keys to evict to free ``target_bytes`` (or to clear the high water)., The budget's answer to "can I hold this?". (+1 more)

### Community 139 - "probes/__init__.py"
Cohesion: 0.13
Nodes (19): Probes: how the kernel reads whatever it was pointed at., ExecPolicy, Any, Target, Shell probe: scripts as data first, as executables only on request. A folder…, Conditions under which a script may actually be run., Outcome of an executed script., Run a script under an :class:`ExecPolicy`, capturing bounded output. (+11 more)

### Community 140 - "test_slpie_dispatch.py"
Cohesion: 0.09
Nodes (24): Forget what is installed. For tests that pretend a tool is missing., reset_probes(), reset(), One external binary this platform knows how to ask for. Declared as data so the…, Every argv worth trying, preferred first. `rg` before `grep` is the point of…, Tool, fixture, Dispatch — the kernel does not reimplement the device. Three properties matter… (+16 more)

### Community 141 - "Advisory"
Cohesion: 0.11
Nodes (9): Advisory, AdvisoryDatabase, One OSV document, parsed into the parts matching actually needs., A retracted advisory. It never fires, at any severity., Every identifier this advisory answers to — its id and its aliases., The CVE among the identifiers, if there is one. Reports lead with it., Advisories in memory, indexed by package coordinate and by alias. Construction…, Load an OSV mirror already on disk. A read, never a fetch. (+1 more)

### Community 142 - "GitHubSource"
Cohesion: 0.11
Nodes (16): GitHubSource, Any, Artifact, GitHub, through the REST API — repositories rather than packages. The other two…, `name` is `owner/repo`, or a URL one can be recovered from., `owner/repo` out of anything that carries one, or empty. Package registries…, Describes repositories, so a candidate can be judged on its upkeep., _timestamp() (+8 more)

### Community 143 - "probes/media.py"
Cohesion: 0.12
Nodes (22): avi_info(), _find_box(), _find_sub_box(), image_size(), ImageProbe, _jpeg_size(), MediaInfo, mp4_info() (+14 more)

### Community 144 - "Binding"
Cohesion: 0.11
Nodes (11): Scope, Binding, Assign a role. Refuses anything that would break a separation rule., Break-glass: time-boxed, reasoned, and impossible to make permanent., Withdraw a role. Supersedes rather than deletes., Every role in force here, inheritance included., Every action this principal may take here — the UI's question. Answered from…, Every binding transition, in order. (+3 more)

### Community 145 - "ToolRegistry"
Cohesion: 0.11
Nodes (11): Any, Gap, Outcome, Tool, One gap per tool that was wanted and missing. The fallback is announced rather…, How reproducible anything built on these dispatches can claim to be., Which tool serves which capability, and who runs it., Install a driver. This is the seam Gratimos's guarded executor takes. (+3 more)

### Community 146 - "test_slpie_station.py"
Cohesion: 0.08
Nodes (15): negotiate(), Ask a connector what it offers, and record everything it withholds. ``wanted``…, manifest(), fixture, The station: attach, negotiate, hand over, detach — and the gaps that result., A refusal the ledger never saw cannot become a gap, and a gap nobody recorded…, A capability granted after a refusal stops producing a gap, without anyone…, A relocated repository that re-attached under a new name would split its… (+7 more)

### Community 147 - "GateResult"
Cohesion: 0.11
Nodes (13): Blocker, GateResult, Any, Candidate, Evaluate one candidate. Never raises; a failure is a returned verdict., Split into (admitted, refused), both fully explained., A need may name licences directly, beyond the obligation analysis. `not…, `not dependency: X` — the constraint that keeps a tree from creeping. Checked… (+5 more)

### Community 148 - "Engine"
Cohesion: 0.11
Nodes (10): Engine, Any, Gap, Materialise the declared world and bind to it., Register every declared element and negotiate capabilities., Everything limiting the platform's answers right now. Collected from the…, The plugin registry, with the built-in discoverers registered. Built lazily so…, Run discovery over every bound element. Everything discovery produces goes… (+2 more)

### Community 149 - "Resolved"
Cohesion: 0.10
Nodes (11): Any, Observation, Everything the resolver made of one batch of observations., How many observations collapsed into fewer identities., Collapse a batch onto identities, and turn the rest into links., Keep pins and ranges apart. Collapsing them loses reconciliation. `identity` is…, One identity, and every observation that reached it., Whether more than one independent source reached this identity. Independence is… (+3 more)

### Community 150 - "test_slpie_linking.py"
Cohesion: 0.17
Nodes (25): merges_with(), Collapses observations onto canonical identities. Merges only on identity., Whether two identities are the same thing. Identity only, never likeness.…, Resolver, declares(), evidence(), Observation, Merging identities, and joining what spans two files. The load-bearing case is… (+17 more)

### Community 151 - "Need"
Cohesion: 0.10
Nodes (14): Constraint, Need, Any, A stable identifier, so a need can be a subject in the fact base., One requirement a candidate is measured against., The canonical form that enters the signature., What the context wants, keyed by meaning rather than by phrasing., The cache key. Order-independent, phrasing-independent, stable. Sorted so that… (+6 more)

### Community 152 - "Capture"
Cohesion: 0.13
Nodes (15): Capture, iter_targets(), The probe contract: how the kernel learns what is in front of it. A probe…, Walk a directory, yielding a target per file., What a probe found: shaped payloads plus how it got them., Target, Document probes: JSON, JSON Lines, YAML, and plain text. JSON is where…, YAML configuration and data files, when PyYAML is available. (+7 more)

### Community 153 - "reference/__init__.py"
Cohesion: 0.12
Nodes (21): What this system knows about the world it operates in — with citations. Two…, Governance, Layer, protocol_registry(), Enum, str, The agent-interoperability protocols, recorded with their provenance. The…, The protocols an agent platform has to know about, with citations. (+13 more)

### Community 154 - "Reference"
Cohesion: 0.11
Nodes (11): Any, The paper's vocabulary and citations, queryable., What the paper asks for that this system does not do. The useful list., The four the paper sets out for policymakers, in its own order., One idea from the paper, defined, sourced, and mapped to our code., A system the paper cites, and why it cites it., Reference, Term (+3 more)

### Community 155 - "load"
Cohesion: 0.11
Nodes (15): _coerce(), _flatten(), load(), Any, Environment variables are strings; configuration is not. Conservative on…, `{"crawl": {"interval": 1}}` → `{"crawl.interval": 1}`. Flat keys mean a…, Merge a nested mapping into one layer., Read `gratimos.toml`. A missing optional file is not an error. (+7 more)

### Community 156 - "Evaluation"
Cohesion: 0.11
Nodes (9): AbcSequence, Evaluation, Any, Finding, A rule that abstained, and why. Kept as data rather than logged because it…, The result of running a rule set: a sequence of findings, plus what broke. It…, How many rules abstained. Non-zero means the answer has holes., Live nodes from the graph, or nothing when no graph was supplied. (+1 more)

### Community 157 - "GratimosError"
Cohesion: 0.11
Nodes (19): GratimosError, Exception, Root of every error the kernel raises deliberately., A shape could not be inferred, merged, or reconciled., ShapeError, ConfigError, Layer, IntEnum (+11 more)

### Community 158 - "dispatch/registry.py"
Cohesion: 0.14
Nodes (17): Dispatch — the kernel does not reimplement the device. A verb is a syscall, a…, DispatchError, The default driver: subprocess, argv lists, no shell, ever. Three rules, and…, A dispatch was constructed wrongly. A tool failing is an `Outcome`., Dispatcher, Capability → tool, as data. The driver table. A caller asks for a *capability*…, What a driver must implement. `LocalDispatcher` is the default., registry() (+9 more)

### Community 160 - "PipelineResult"
Cohesion: 0.09
Nodes (11): Layer, LayerResult, What one layer did, and what it could not do., One stage of the pipeline., PipelineResult, Any, Gap, Why the platform believes one thing, ending in files and line numbers. The path… (+3 more)

### Community 161 - "Routine"
Cohesion: 0.11
Nodes (10): Any, Claim a composition as a routine. Refuses anything that would not run., Claim the longest accepted pipeline from a session. The *longest* rather than a…, A key to its composition. Raises rather than guessing., Record a use. What makes `slpie routine` show what actually pays off., Most used first — the ones that earned their key., A routine could not be claimed, or a key could not be resolved., A named composition, invoked by a short key. (+2 more)

### Community 162 - ".discover"
Cohesion: 0.11
Nodes (10): Any, Artifact, Source, Concepts plus their synonyms, canonical form first. Synonyms are included…, The registries this need permits. An ecosystem constraint is hard: a Python…, Search every permitted source and return what was actually read., Fold repository upkeep into a package record. A registry knows what was…, Everything read about one artifact, as triples the engine can chain. Note what… (+2 more)

### Community 163 - "Origin"
Cohesion: 0.14
Nodes (15): Origin, Where one artifact came from, precisely enough to go back and check., _licence(), NpmSource, Any, Artifact, npm, through the registry's own endpoints. Unlike PyPI, npm publishes a real…, npm's `repository` is a string in old packages and an object in new ones. (+7 more)

### Community 164 - "database.py"
Cohesion: 0.17
Nodes (13): Connection, Cursor, describe_connection(), Any, Target, Database probes and streaming capture. SQLite gets a first-class probe because…, Stream a query as a sequence of shaped, bounded payloads. Works with any DB-API…, Infer a shape per table without materializing the tables. (+5 more)

### Community 165 - "Assessment"
Cohesion: 0.11
Nodes (10): Assessment, Any, Artifact, Why this answer, in the order the decisions were made. Rendered rather than…, Answer one need. Never raises on a missing source; it reports a gap., Ask the agent — or the memory of the last time it was asked. The candidate list…, One candidate offered, with everything behind the offer., Whether this rests on a checked claim rather than a naming guess. (+2 more)

### Community 166 - "Candidate"
Cohesion: 0.10
Nodes (11): Candidate, Decision, Any, Gap, Whether it matches. A rule that raises does not match — and is loud. A raising…, The verdict, and the full chain evaluation behind it. `explain()` reads like…, What the *caller* is told. A DROP tells them nothing but the verdict. This is…, The operator's view. Everything, including a DROP's reason. (+3 more)

### Community 167 - "test_slpie_constraints.py"
Cohesion: 0.13
Nodes (23): classify(), overlaps(), A range as a disjunction of windows. One interval per clause., Whether any version satisfies both ranges., How big a step it is from one version to another., Every upgrade from `current`, each labelled with who it breaks. Returns *all*…, safe_upgrades(), to_intervals() (+15 more)

### Community 168 - "EventStream"
Cohesion: 0.09
Nodes (14): EventStream, Any, Yield SSE frames for one client until it goes away., Trim an event payload to what a feed line needs. A node assertion carries its…, Enqueue, discarding the oldest first if this client is behind., Fans domain events out to connected clients. Subscribes to the bus like any…, Bus entry point. Never raises — a dead client is not an outage., Register a client, priming it with recent history. The backlog matters: a tab… (+6 more)

### Community 169 - "BacktrackingSolver"
Cohesion: 0.14
Nodes (12): Conflict, PackageIndex, Where versions and their dependencies come from. A protocol so the solver never…, BacktrackingSolver, A provably impossible pair among these demands, if there is one., Why this coordinate had nothing left, said as specifically as possible., Most informative first: a named pair beats a lone unmet requirement., How far each package sits from the root. Cycle-safe by construction. (+4 more)

### Community 170 - "Guard"
Cohesion: 0.10
Nodes (21): Guard, Policy, Reviews commands. Cannot run them — there is no code path that would., Where the lines are. Stated once, applied everywhere. The defaults are…, ladder(), fixture, Even a low-risk routine does not automate if the guard would refuse it., Context in, guarded suggestion out, automation earned rather than assumed. (+13 more)

### Community 171 - "constraints/__init__.py"
Cohesion: 0.12
Nodes (12): Dependency resolution, with the failure explained rather than announced. The…, Assignment, Conflict, What a dependency problem is, stated so a solver can be swapped out. The plan…, Every evidence id behind every requirement this assignment satisfies., Why nothing works, named precisely enough to act on. Both requirements, both…, The two requesters at odds. The second is empty when only one demand exists., One paragraph a human can act on without opening a dependency tree. (+4 more)

### Community 172 - "PyPISource"
Cohesion: 0.16
Nodes (12): Any, Artifact, PyPISource, Concept terms → plausible distribution names, most likely first. Kept public…, Describes Python distributions from the official JSON endpoint., Resolve concept terms to distributions by asking for each by name., test_a_development_status_of_inactive_marks_the_package_deprecated(), test_a_licence_field_holding_the_whole_licence_text_is_not_treated_as_spdx() (+4 more)

### Community 173 - "Verdict"
Cohesion: 0.13
Nodes (11): Any, Command, The answer, and everything needed to act on it or defend it., Written to be shown to whoever must decide, unedited., Decide one command. Never raises for a refusal — see :meth:`permit`., Review, and raise unless the result may run. For callers about to act., Review a whole plan. Every command, not up to the first refusal. Stopping at…, A whole plan's verdicts as one block, worst first. (+3 more)

### Community 174 - "Decision"
Cohesion: 0.12
Nodes (8): Decision, Any, The answer, and everything needed to act on it or defend it., What the caller must do to turn this refusal into an allow, if anything., One line, written to be pasted into a ticket unedited., Decide, and say why. Never raises for a refusal — see :meth:`require`., Decide, and raise on refusal with the explanation in the message., Recorded decisions. Denials always; allows only if asked for.

### Community 175 - "Station"
Cohesion: 0.16
Nodes (7): Attaches declared elements and keeps them tracked. Holds a command bus and a…, Register an element and negotiate what it will let us see., Move an element without changing what it is. The identity is preserved on…, Which attached elements can answer a given kind of question., One attached element, as the station tracks it., Registration, Station

### Community 176 - ".of"
Cohesion: 0.12
Nodes (17): Merge many observations into one shape., unify(), Wrap a raw payload, inferring its shape on the way in., Shapes, inference, casting, and the self-describing wrappers., test_casting_modes_differ_on_the_same_value(), test_inference_reads_semantics_out_of_text(), test_inference_records_nulls_and_nested_structure(), test_map_reinfers_the_shape() (+9 more)

### Community 177 - "Solution"
Cohesion: 0.12
Nodes (8): Any, The outcome of a solve. Satisfiable or not, it explains itself. `conflicts` is…, Turn a `Resolution` into requirements, keeping the evidence chain. Ranges…, requirements_from(), Solution, The solve, rendered as explanation lines for the reasoning path., The type refuses the shrug. 'It does not work' is not a usable answer., test_an_unsatisfiable_solution_cannot_be_built_without_a_conflict()

### Community 178 - "git.py"
Cohesion: 0.16
Nodes (19): needs_git, available(), Commit, discover_repository(), _git(), is_repository(), last_changed(), _log() (+11 more)

### Community 179 - "Target"
Cohesion: 0.13
Nodes (6): BaseProbe, Any, Shared plumbing: suffix matching, limits, and error capture., Something in the environment that might contain data., Build a target, peeking at local files so probes can sniff content., Target

### Community 180 - "Affected"
Cohesion: 0.14
Nodes (10): Affected, AffectedRange, _match(), Version, One OSV range, kept as its events so the semantics stay visible., The interval as a range expression the version parser understands., None when the range cannot be compared against a version at all., One affected package within an advisory. (+2 more)

### Community 181 - "Capability"
Cohesion: 0.12
Nodes (9): Capability, Negotiation, Any, Gap, The outcome of asking an element what it will allow. Both halves are kept.…, One capability, granted or refused, with the reason when refused., The gap this refusal creates. Attached to every answer it limits.…, How much certainty this refusal costs, from the evidence it blocks. (+1 more)

### Community 182 - "app.js"
Cohesion: 0.23
Nodes (18): api, append(), ask(), connect(), el(), escape(), fireScenario(), list() (+10 more)

### Community 183 - "test_probes.py"
Cohesion: 0.18
Nodes (18): AccessPolicy, What the API probe is permitted to reach., default_registry(), The probe set the kernel boots with. Network and shell probes take their…, Reading the environment: every format, and the guards around the risky ones., test_access_policy_refuses_private_addresses(), test_access_policy_refuses_unlisted_hosts_and_schemes(), test_contested_targets_are_reported() (+10 more)

### Community 184 - "shell/environment.py"
Cohesion: 0.17
Nodes (16): _ci(), _distribution(), _in_container(), Platform, _platform_of(), probe(), Enum, str (+8 more)

### Community 185 - "ObjectRef"
Cohesion: 0.15
Nodes (6): MemoryStore, ObjectRef, Any, A handle to stored bytes: where it is, how big, and what it hashes to., In-process store. Useful for tests and for ephemeral staging., _memory_factory()

### Community 186 - "calibration.py"
Cohesion: 0.12
Nodes (13): CalibrationError, Observation, Measuring whether our confidence means anything. This system assigns confidence…, Record one prediction and its outcome., A calibration question was asked of data that cannot answer it., A proportion's confidence interval, done properly. The naive interval (`p ±…, One prediction and what actually happened., wilson() (+5 more)

### Community 187 - "_raw"
Cohesion: 0.11
Nodes (19): A page that looks fine and silently cannot be installed is the worst kind of…, `addAll` semantics aside, a shell listing an asset that 404s is a shell that…, A cached SSE response would replay history as though it were happening now., The honesty rule does not weaken because the client is a laptop on a plane., The kernel's zero-dependency rule applies to the UI too: no CDN, no fonts, no…, A palette listing verbs by hand would drift the moment one was added., It must agree with the server, or the builder offers compositions the server…, One GET, returning status, headers and body. No JSON assumed. (+11 more)

### Community 188 - "StorageError"
Cohesion: 0.13
Nodes (13): Factory, An object store rejected a read or write., A key resolved outside its secured repository root., StorageError, UnsafePathError, _canonical(), digest(), Content digest that is stable across processes for JSON-like payloads. (+5 more)

### Community 189 - "Report"
Cohesion: 0.12
Nodes (5): Any, What a budgeted run checked, what it skipped, and what that cost. `deferred`…, The best thing we could not afford. The argument for a bigger budget., Fraction of the available information gain this run actually took., Report

### Community 190 - "Joined"
Cohesion: 0.16
Nodes (10): Resolution, _enrich(), Joined, Every link carries the ids of what it read. No chain, no link., The distribution that provides this import, or empty., A service and the interface contract it serves. Joined on the service urn both…, What Kubernetes schedules against what a Dockerfile built. The join is on the…, One link a linker made, and the reasoning behind it. (+2 more)

### Community 191 - "shaping.py"
Cohesion: 0.29
Nodes (17): _count(), _explain(), _field(), _filter(), _head(), _json(), Any, Flow (+9 more)

### Community 192 - "Assurance"
Cohesion: 0.11
Nodes (29): Assurance, int, Who is calling — human, agent, or service — and how well we know it. Three…, How much the authentication is worth. Ordered, and compared as such., Role-based access control: embedded, deterministic, and explainable. Keycloak…, Effect, Enum, str (+21 more)

### Community 193 - "Command"
Cohesion: 0.12
Nodes (9): Command, _inspect(), One stage of a pipeline., The program being run, seeing past `sudo` and `env VAR=x`., Handles both `-rf` and `-r -f`, which is where naive checks fail., A parsed command line. Data — nothing here can run., `curl … | sh` — remote code, executed unread, in one line., The specific footguns, each named. (+1 more)

### Community 194 - "FilesystemConnector"
Cohesion: 0.15
Nodes (8): FilesystemConnector, Reads files under a root. Used unchanged for simulated and live targets.…, Map a logical uri onto disk, refusing anything outside the root. The…, Any, Target, The guarantee, stated as an assertion. Both connectors are pointed at the same…, test_only_the_mode_field_distinguishes_the_two(), test_simulated_and_live_connectors_return_identical_results()

### Community 195 - "Page"
Cohesion: 0.13
Nodes (11): _code_list(), _columns(), Page, Any, The page as markdown — what `slpie manual` writes to a file., Wrap a paragraph. Stdlib `textwrap` would do, and does., Names wrapped into a block, so twenty successors do not print one per line., One verb, everything a reader needs, derived from its declaration. (+3 more)

### Community 196 - "tauri.conf.json"
Cohesion: 0.12
Nodes (15): app, security, windows, build, devUrl, frontendDist, identifier, plugins (+7 more)

### Community 197 - "register_builtins"
Cohesion: 0.12
Nodes (16): Registry, Register every built-in discoverer, through the public plugin path., register_builtins(), Written but unregistered is the state this phase was frozen in., test_every_phase_eight_discoverer_is_wired_into_the_scanner(), test_packaging_is_read_before_the_source_that_uses_it(), Every ecosystem, infrastructure format and interface contract planned., test_all_five_are_wired_into_the_scanner() (+8 more)

### Community 198 - "C4Element"
Cohesion: 0.13
Nodes (10): C4Element, C4Relationship, _element_line(), _escape(), Any, Mermaid label text: quotes and brackets would end the label early., One box on the diagram, and the node it is., One arrow, labelled with the relationship the graph actually recorded. (+2 more)

### Community 199 - "Suggestions"
Cohesion: 0.12
Nodes (6): A term worth answering with a path rather than a definition., _TermPath, Any, What the CLI prints after a command. Terse when nothing is wrong., What was offered for one touch, and why in this order., Suggestions

### Community 200 - "compose.js"
Cohesion: 0.32
Nodes (15): addStage(), canFollow(), checkPipeline(), compose, kindAfter(), loadCompose(), loadPipeline(), pipelineText() (+7 more)

### Community 201 - "probe"
Cohesion: 0.18
Nodes (14): available(), determinism_of(), _environment(), probe(), Outcome, Tool, Run it, or report honestly why it did not run., The weakest reproducibility guarantee among everything that was dispatched. A… (+6 more)

### Community 202 - "GraphProjection"
Cohesion: 0.19
Nodes (6): GraphProjection, Any, Projection, Claims dropped because their evidence never arrived. Non-empty means the event…, Folds node, edge, evidence and enrichment events into a graph store., Refold the whole ledger inside one transaction. A rebuild replays every event…

### Community 203 - "Fetcher"
Cohesion: 0.15
Nodes (9): Fetcher, Any, Response, Block until this host may be contacted again. Returns time waited., Polite, cached, retrying HTTP GET. The crawler's only door outward., Fetch one URL under the full policy. Raises rather than returning junk., Fetch and parse. The shape every official registry API speaks., Fetch, or return None when it was refused or failed. For the many places in a… (+1 more)

### Community 204 - "Permission"
Cohesion: 0.15
Nodes (6): deny(), Permission, Any, One rule. Readable in a line, checkable by a human., Whether this grants breadth an auditor should look at., test_a_permission_with_an_empty_resource_is_refused()

### Community 205 - "Separation"
Cohesion: 0.16
Nodes (7): Any, Which of these roles conflict. Empty when the rule is satisfied., Every separation these roles violate, with the offending roles. Evaluated over…, Roles that must not be held by one principal at one scope. `reason` is…, Separation, test_a_separation_naming_one_role_is_refused(), test_a_separation_without_a_reason_is_refused()

### Community 206 - ".gaps"
Cohesion: 0.18
Nodes (6): Any, Gap, Every gap the station knows about, deduplicated. This is what gets attached to…, (element, capability, reason) for everything withheld., The fraction of attached elements that granted everything asked., Everything about this registration that limits an answer.

### Community 207 - "test_reuse_boundaries.py"
Cohesion: 0.18
Nodes (12): gratimos_modules(), imported_roots(), parametrize, The mirror boundary: Gratimos reaches into SLPIE in exactly one file.…, The crawler is stdlib-only, exactly as the SLPIE kernel is. A crawler is the…, Direction: reuse depends on crawl, never the reverse. Both directions would…, The reuse path is lazy, so `import gratimos` stays cheap. Asserted in a…, test_every_new_module_parses_and_carries_a_docstring() (+4 more)

### Community 209 - "Linked"
Cohesion: 0.23
Nodes (4): Linked, Any, A resolution plus the cross-file joins made over it. Exists so that the joins…, Both kinds, in one list. A node contradicted by two lockfiles and a pin…

### Community 210 - "PluginManifest"
Cohesion: 0.15
Nodes (7): PluginManifest, Any, A plugin's declaration of itself., Whether this plugin is permitted to produce this evidence kind. An empty…, Whether this plugin claims a source file., test_a_manifest_without_an_id_or_entrypoint_is_refused(), test_an_in_process_plugin_with_no_handler_is_refused()

### Community 211 - "RobotsDecision"
Cohesion: 0.17
Nodes (6): Any, The host's requested delay, or zero. Used by the rate limiter., Whether a URL may be fetched, why, and how long to wait first., Decide one URL. Cheap after the first call to a given origin., RobotsDecision, RobotFileParser

### Community 212 - "blocks"
Cohesion: 0.17
Nodes (9): blocks(), Media, Any, Block, WAV's `fmt ` chunk gives rate, channels and — with `data` — a duration. Chunks…, A media file as a document: its metadata, and a stated absence. The…, What a container says about itself, without decoding it., `45m 12s` — the unit the missing transcript's cost is expressed in. (+1 more)

### Community 213 - "LinkerSet"
Cohesion: 0.12
Nodes (11): Merging identities and joining what spans two files. Two responsibilities that…, CorroborationLinker, Linker, LinkerSet, One piece of cross-file knowledge, applied to a resolution., A lockfile pin against the manifest range that asked for it. Three outcomes,…, Terraform resources under the deployments that run on them., Every linker, run over one resolution. A linker that raises **abstains** and is… (+3 more)

### Community 215 - "UpgradeStep"
Cohesion: 0.17
Nodes (5): Overlap, Any, Whether two windows can both hold, and how sure we are of that. `certain` false…, One candidate upgrade, with what it costs and what it would break., UpgradeStep

### Community 216 - "_runner.py"
Cohesion: 0.27
Nodes (10): _apply_limits(), _load_module(), _log(), main(), Subprocess entry point for sandboxed transformations. Runs as ``python -I -S…, Install OS resource limits. Returns the ones that actually took., Enforce the import allow list at runtime, not just statically. The AST gate…, The `log` helper handed to transformations, in place of print. (+2 more)

### Community 217 - "extract"
Cohesion: 0.18
Nodes (11): extract(), _paragraphs(), Block, Text-showing operators, in order, as text., PDF string escapes. `\\(`, `\\)`, `\\\\`, and the octal form., Collapse the operator soup into readable lines., PDF text as page blocks, plus an honest block per page not extracted., Every content stream, decompressed where it is Flate-encoded. (+3 more)

### Community 218 - ".from_dict"
Cohesion: 0.27
Nodes (5): _from_score(), Any, Severity, The advisory's severity, from whichever field the feed used. CVSS *vectors* are…, _severity_of()

### Community 219 - "matches_resource"
Cohesion: 0.22
Nodes (10): matches_action(), matches_resource(), Dotted actions: `graph.*` covers `graph.read`, `*` covers everything., Whether a resource pattern covers a concrete resource. Segment-wise rather than…, parametrize, `repo:acme/*` means one segment, not "anything below acme"., test_a_pattern_covers_the_resources_it_should(), test_a_pattern_does_not_cover_what_it_should_not() (+2 more)

### Community 220 - "Focus"
Cohesion: 0.20
Nodes (4): Focus, Where attention settled, and how stuck the reviewer appears to be., Whether anything resolving happened. Dwell without action is the case., The focus, as the trigger the suggestion engine consumes.

### Community 221 - "test_slpie_boundaries.py"
Cohesion: 0.27
Nodes (10): imported_roots(), parametrize, The architectural boundaries, asserted rather than documented. SLPIE owns…, Including the UI. Anything importable must ship with Python itself., Simulated and live differ only in which connector is bound. A `if target ==…, slpie_modules(), test_every_module_parses_and_carries_a_docstring(), test_exactly_one_slpie_module_may_import_gratimos() (+2 more)

### Community 222 - "ConnectorRegistry"
Cohesion: 0.27
Nodes (3): ConnectorRegistry, Resolves URIs to stores, caching one instance per URI., Build (or reuse) the store behind a URI.

### Community 223 - "LocalDispatcher"
Cohesion: 0.20
Nodes (8): LocalDispatcher, Runs a tool on this machine. The stdlib default driver., Refuse an argv that only makes sense if a shell were involved. Nothing here…, Nothing here interprets `;`, so its presence means the caller thought something…, `--pretty=format:%h;%s` is a legitimate git flag., test_a_flag_is_never_mistaken_for_shell_syntax(), test_an_allow_list_constrains_what_a_dispatcher_will_run(), test_an_argument_carrying_shell_syntax_is_refused()

### Community 227 - ".to_rule"
Cohesion: 0.40
Nodes (3): Rule, RuleSet, Compile to a :class:`Rule`. The compilation is a closure, not code. Nothing is…

### Community 228 - "Outcome"
Cohesion: 0.25
Nodes (3): Outcome, Any, What a dispatch produced, including its failure and its provenance.

### Community 229 - "test_an_illegal_filter_is_rejected_rather_than_recorded"
Cohesion: 0.29
Nodes (7): matches(), Whether an MQTT filter covers a concrete topic. Implemented level by level…, parametrize, `+` is one level and `#` is the rest. Flattening them makes a narrow subscriber…, A broker would refuse it, so recording it describes an estate that cannot exist., test_an_illegal_filter_is_rejected_rather_than_recorded(), test_the_two_wildcards_are_not_interchangeable()

### Community 230 - "ImportToPackageLinker"
Cohesion: 0.29
Nodes (6): ImportToPackageLinker, `import yaml` → `pkg:pypi/pyyaml`, and nothing when the table cannot say. The…, Several PyPI packages share a stdlib module's name and are not it., test_a_stdlib_import_is_never_attributed_to_a_package(), test_an_import_whose_name_is_the_distribution_name_passes_through(), test_the_import_table_knows_that_yaml_is_pyyaml()

### Community 231 - "Change"
Cohesion: 0.29
Nodes (5): Change, Enum, str, How far apart two versions are, in the terms semver defines., Whether a consumer *might* be broken. Not whether it will be.

### Community 232 - "_fingerprint_callable"
Cohesion: 0.33
Nodes (4): _fingerprint_callable(), A fingerprint of this rule's definition, so its meaning cannot drift. Covers…, The fingerprint of the whole set — every rule definition it contains., The text of a rule's own logic, for the definition digest. Source text is…

### Community 234 - ".refuse"
Cohesion: 0.33
Nodes (5): Refuse a capability. A reason is required, not optional. A refusal without a…, A refusal with no reason produces a gap nobody can act on., Losing a lockfile read costs more than losing message inspection — a lockfile…, test_a_refusal_costs_what_the_evidence_it_blocks_was_worth(), test_a_refusal_must_carry_a_reason()

### Community 236 - ".to_dict"
Cohesion: 0.40
Nodes (4): _mask_phone(), Any, Redacted by construction: raw claims never cross this boundary., Last four digits only. A phone number in a log is a phone number leaked.

### Community 237 - "ResolutionError"
Cohesion: 0.40
Nodes (4): IdentityError, One identity string → (canonical, node id, coordinate). Purls go through…, An observation could not be resolved to an identity., ResolutionError

### Community 239 - "example_normalize.py"
Cohesion: 0.50
Nodes (3): Example transformation: tidy customer names and derive a size flag. Copy this…, Return the transformed records. Args: records: list of dicts, one per row of…, transform()

### Community 240 - "server"
Cohesion: 0.67
Nodes (3): engine(), fixture, server()

## Knowledge Gaps
- **54 isolated node(s):** `$schema`, `productName`, `identifier`, `frontendDist`, `devUrl` (+49 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Path` connect `Path` to `Evidence`, `Node`, `LocalRepository`, `infer_shape`, `VerbRegistry`, `TransformRegistry`, `probes/__init__.py`, `test_slpie_demo.py`, `Audit`, `ModuleRegistry`, `probes/media.py`, `Advisory`, `test_storage_hubs.py`, `Verdict`, `test_reasoning.py`, `Engine`, `Capture`, `Context`, `test_slpie_manual.py`, `astmerge.py`, `load`, `ProjectionSet`, `governor.py`, `test_slpie_simulator.py`, `test_slpie_ledger.py`, `api.py`, `Wrapped`, `test_slpie_capture.py`, `.of`, `test_slpie_binding.py`, `git.py`, `Target`, `test_slpie_plugins.py`, `Composition`, `test_probes.py`, `shell/environment.py`, `test_slpie_contract.py`, `test_slpie_suggest.py`, `plan_for`, `SimulatedWorld`, `RunLedger`, `Assurance`, `Module`, `FilesystemConnector`, `test_slpie_audit.py`, `MigrationLedger`, `ResponseCache`, `test_reuse_boundaries.py`, `test_slpie_graph.py`, `History`, `SqliteGraph`, `Declaration`, `Registry`, `test_slpie_boundaries.py`, `graph/__init__.py`, `Policy`, `Journal`, `sbom.py`, `Cli`, `ImpactResult`, `ledger.py`, `ProbeRegistry`?**
  _High betweenness centrality (0.224) - this node is a cross-community bridge._
- **Why does `Protocol` connect `Protocol` to `a2a/__init__.py`, `Verb`, `Key`, `LedgerRecord`, `Verdict`, `reference/__init__.py`, `astmerge.py`, `binding/resolver.py`, `dispatch/registry.py`, `PipelineResult`, `database.py`, `test_slpie_simulator.py`, `BacktrackingSolver`, `test_slpie_core.py`, `constraints/__init__.py`, `crawl/__init__.py`, `LinkerSet`, `test_reference.py`, `c4.py`, `Traversal`, `RbacError`, `connectors.py`, `ProbeRegistry`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Why does `GratimosError` connect `GratimosError` to `JsonProbe`, `ContextFlow`, `Engine`, `TransformRegistry`, `test_storage_hubs.py`, `Verdict`, `test_reasoning.py`, `reference/__init__.py`, `astmerge.py`, `PolicyEngine`, `governor.py`, `AgentError`, `crawl/__init__.py`, `calibration.py`, `StorageError`, `Ontology`, `shell/__init__.py`, `validate/__init__.py`, `ledger.py`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 11 inferred relationships involving `Path` (e.g. with `cmd_trace()` and `cmd_transforms()`) actually correct?**
  _`Path` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `Evidence` (e.g. with `Edge` and `EdgeKind`) actually correct?**
  _`Evidence` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `DomainEvent` (e.g. with `DeliveryReport` and `EventBus`) actually correct?**
  _`DomainEvent` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Context` (e.g. with `Composition` and `CompositionError`) actually correct?**
  _`Context` has 7 INFERRED edges - model-reasoned connections that need verification._