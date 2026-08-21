# Phase 18 — deployment automation

Execution plan. Checked against the tree, not recalled.

## What already exists

| Piece | Where | State |
|---|---|---|
| A stdlib YAML reader | `slpie/environment/schema.py:parse_yaml` | reads the subset a manifest uses and **refuses** the rest |
| The declare-first manifest shape | `slpie/environment/{manifest,loader,schema}.py` | `Target`, `Declaration`, `validate()` — the pattern a deployment manifest mirrors |
| The one dangerous-action gate | `slpie/binding/guard.py` | confirmation, read-only-by-default, refusals recorded with a reason |
| A topology model | `slpie/enterprise/topology.py` | environments → zones → nodes, `unknown` rather than dropped |
| Subprocess dispatch | `slpie/dispatch/{tool,local,registry}.py` | argv lists, never `shell=True`, a missing binary is a reported gap (§27) |
| Kubernetes provisioning | `slpie_enterprise/spawn/kubernetes.py` | P3, ring 1 |
| 51 verbs, none named `deploy` | `slpie/compose/registry.py` | the gap this phase closes |

## The decision §18 left contradictory

§18 says two things that cannot both hold. Its prose: *"Emitting is text
generation and therefore ring-0-safe … Applying shells out to
`terraform`/`helm`/`kubectl`, which is ring 1."* Its file list: everything under
`slpie_enterprise/deploy/`.

**Decided: the model, the plan and the emitters are ring 0; only `apply` is ring 1.**

Three reasons, in order of weight:

1. **A capability with no surface is drift (§24).** `slpie/compose/registry.py`
   is ring 0 and cannot import ring 1, so a `deploy` living entirely in ring 1
   could not be a verb — no CLI subcommand, no route, no manual page, no
   planner vocabulary. §29 measured that exact failure and called it the largest
   instance of drift in the codebase.
2. **The air-gapped operator is the one who most needs `render`.** Producing a
   compose file or a systemd unit is text generation; refusing to do it without
   the enterprise extras would withhold the offline path from the only console
   that runs offline.
3. **Applying is genuinely different.** It needs binaries and cloud credentials,
   which are the operator's and not the kernel's.

So the split follows what each half actually touches, and §18's file list is
corrected rather than followed.

## The manifest

`slpie.deployment.yaml`, a sibling to the environment manifest, declare-first in
exactly the same way and parsed by the same reader. Sections: `topology`,
`elasticity`, `budget`, `regions`, `persistence`, `platform`, `cloud`, and the
one tag — `target: plan | apply`.

An unknown section is a **refusal naming the line**, not a warning. The
environment manifest already takes that position, for the reason its schema
docstring gives: a configuration file silently misread is worse than one that
fails to load.

## Steps

| Step | Delivers | Gate |
|---|---|---|
| **1** | `slpie/deploy/manifest.py` + `schema.py` — the model, validated, with `Platform` and `Cloud` closed | an unknown section is refused with its line; every field round-trips |
| **2** | `slpie/deploy/plan.py` — declared vs running, as a diff that touches nothing | a plan over an unchanged topology is empty; a changed replica count names the component and both numbers |
| **3** | `slpie/deploy/emitters/` — compose, kubernetes, systemd, helm, terraform, pipelines, all pure text under `ExtensionPoint.ARTIFACT` | same manifest in, byte-identical text out, twice; a fourth platform is a registration |
| **4** | `slpie/deploy/manual.py` — `docs/INSTALL.md` from the model | the ports it documents are the manifest's; a hand-edit is overwritten, and the file says it is generated |
| **5** | The `deploy` verbs — `plan` · `render` · `manual` · `status`, and `apply` behind the guard | `deploy apply` without `--confirm` is refused by `slpie/binding/guard.py`, not by a second gate |
| **6** | `slpie_enterprise/deploy/apply.py` — dispatch to `terraform`/`helm`/`kubectl` | a missing binary is a capability gap naming the tool, never a crash |
| **7** | The cost model — `ResourceMeter` readings as `RUNTIME_TRACE` evidence, cost rules in the forced family | `COST_OVERRUN` carries evidence and a `SCALE` remediation; suppressing it without a reason is refused |
| **8** | Reconciliation applied to itself | a rendered compose deployment, scanned back, reconciles with zero `DECLARED_NOT_FOUND` and zero `CONTRADICTED` |

## What is deliberately not in phase 18

- **Actually running `terraform apply` in CI.** No cloud account, and a green
  tick for something that was never applied is worse than an honest gap — the
  same position phase 17 took on Tauri.
- **A second live gate.** `deploy apply` routes through `slpie/binding/guard.py`
  or it is a hole; §16 refuses to reimplement that guard for FastAPI and the
  same reasoning covers this.
- **The `slpied` update daemon (§23).** It needs a signed release channel and a
  platform integration per OS, and neither is testable here.

## Risks

| Risk | Answer |
|---|---|
| The emitters drift from the model | One `_render_data()` walk, six emitters consuming it — the shape `contract.py` already uses for its four |
| `INSTALL.md` goes stale | It is generated, and a test asserts the committed file matches its generator, as the clients and the skill already are |
| Apply becomes reachable without confirmation | The verb carries `mutates=True`, and a test drives the refusal through the real guard rather than around it |
| Cost findings become decoration | The family is registered unconditionally and `Finding.suppress` already refuses an empty reason |

## What the plan got wrong

Written after building it, in the shape §15 and §17's post-mortems take.

**§18's file list contradicted its own prose, and the prose was right.** It said
emitting is text generation and therefore ring-0-safe, then listed every module
under `slpie_enterprise/deploy/`. Following the list would have made `deploy`
unable to be a *verb* — `slpie/compose/registry.py` is ring 0 and cannot import
ring 1 — so the platform would have gained a capability with no CLI subcommand,
no route, no manual page and no planner entry. That is the drift §24 exists to
prevent and §29 measured. The model, the plan and the emitters are ring 0;
`apply` is ring 1.

**Verb names are globally unique, and three of these are common words.** The
registry refused a second `status` with a sentence worth reading before naming
an apply loosely: *"a shadowed verb inherits confirmations it was never
granted."* So the verbs carry their group — `deploy-plan`, `deploy-apply` — as
`agent-tools` already does, and the CLI joins a two-token head when the joined
name is a verb. That join is a general rule rather than a special case, and it
applies after flag parsing, because before it `slpie --root /x deploy plan`
would try to join `--root` to a path.

**The compose emitter shipped a file that could not start.** It wrote
`depends_on: [postgres, redis]` and emitted neither service, so the YAML was
perfect and `docker compose up` would have failed on a service that did not
exist — the exact class of defect this phase exists to prevent, produced by the
tool that is supposed to prevent it. Caught by rendering the output and reading
it, which is now `test_compose_emits_every_service_it_depends_on`.

**A test can be machine-dependent without looking it.** The applier asked
`shutil.which` directly, so on a host without terraform it short-circuited
before dispatching and the assertions about command *order* passed or failed
according to what happened to be installed. Routing the probe through the same
registry that runs the command makes both observable together — which is what a
seam is for, and the reason §27 put probing there in the first place.

**Two of my own guards were too literal.** `api` is a substring of `apiVersion`,
and the word `default` appears in the description explaining why a password has
none. Both would have failed forever against correct output, which is how a
guard gets deleted rather than fixed.

## What was built

| Step | Delivered | Where the gate lives |
|---|---|---|
| 1 | `slpie/deploy/{schema,manifest}.py`, parsed by ring 0's own YAML reader | eight refusal tests, each naming what was wrong |
| 2 | `plan.py` — typed changes, `REMOVE` separated from `SCALE` | a settled estate plans to nothing; a float is not a change |
| 3 | Six emitters over one model | byte-identical twice; every one declares its limits |
| 4 | `manual.py` → `INSTALL.md` from the topology | the ports it documents are the manifest's |
| 5 | Five verbs; `deploy-apply` behind `binding/guard.py` | two gates, both required, neither reimplemented |
| 6 | `slpie_enterprise/deploy/apply.py` | a missing binary leaves the render and names the tool |
| 8 | The round trip: what the platform emits, it can read | the compose and kubernetes discoverers, on our own output |

**Step 7, the cost model, is not built.** `ResourceMeter` exists (§31) and the
`Rule` machinery exists (§11), but wiring spend into findings needs a meter
reading something real to be worth anything, and this environment has no cloud
account and no bill. Recorded here rather than half-built: a `COST_OVERRUN`
raised from a number nobody measured is the "counted, never modelled" rule
broken by the section that states it.

**Also not built: applying anything in CI.** No cloud account, and a green tick
for something never applied is worse than an honest gap — the position phase 17
took on Tauri.
