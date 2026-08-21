"""Deployment as a declaration — the manifest, the diff, the six emitters.

The property that matters most here is not that a file renders. It is that a
rendered file **means what the manifest said**, and that anything it could not
express is *reported* rather than quietly dropped. A `docker-compose.yaml` that
silently omitted an elasticity range would deploy a fixed pool, look correct,
and be discovered from the bill.

So the assertions come in three families:

* the manifest **refuses** what it cannot read, naming what was wrong;
* the plan **types** its changes, because an apply that deletes a component is
  not the same event as one that resizes it;
* every emitter is **deterministic**, and every one of them declares its own
  limits.
"""

from __future__ import annotations

import pytest

from slpie.deploy import emitters
from slpie.deploy.manifest import Cloud, DeployTarget, Platform, load, loads
from slpie.deploy.plan import ChangeKind, plan
from slpie.errors import ManifestError, TargetRefused

FULL = """
apiVersion: slpie/v1
kind: Deployment
environment: acme-production
target: plan

topology:
  api: { replicas: 3, cpu: 2, memory: 4Gi, ingress: api.acme.internal }
  workers: { min: 2, max: 40, cpu: 4, memory: 8Gi, queues: [scan, reason, govern] }
  scheduler: { replicas: 1 }

elasticity:
  curve: logarithmic
  target_queue_depth: 50
  scale_up_window: 30s
  scale_down_window: 10m
  drain_grace: 5m

budget:
  monthly_ceiling: 4000 USD
  warn_at: 0.75
  idle_after: 20m

regions:
  primary: eu-west-1
  replicas: [us-east-1, ap-southeast-1]
  freshness_budget: 30s

persistence:
  graph: { engine: postgres, size: 200Gi, replicas: 2 }
  ledger: { engine: postgres, retention: forever, partition: monthly }
  objects: { engine: s3, bucket: acme-slpie-artifacts }
  broker: { engine: redis, size: 8Gi }

platform: kubernetes
cloud: aws
"""

MINIMAL = """
apiVersion: slpie/v1
kind: Deployment
environment: small
topology:
  api: { replicas: 1 }
"""


@pytest.fixture()
def declared():
    return loads(FULL)


# --- the manifest -------------------------------------------------------------


def test_a_whole_manifest_round_trips_into_a_model(declared):
    assert declared.environment == "acme-production"
    assert declared.target is DeployTarget.PLAN
    assert declared.platform is Platform.KUBERNETES
    assert declared.cloud is Cloud.AWS
    assert declared.names == ("api", "scheduler", "workers")

    workers = declared.component("workers")
    assert workers.elastic and workers.minimum == 2 and workers.maximum == 40
    assert workers.queues == ("scan", "reason", "govern")
    # A fixed count is a decision and a range is a delegation. Collapsing them
    # would lose which one the operator meant.
    assert not declared.component("api").elastic
    assert declared.component("api").size == 3


def test_a_budget_keeps_its_currency_and_stays_arithmetic(declared):
    """`4000 USD` is how an operator writes it and not how anything computes it."""
    assert declared.budget.monthly_ceiling == 4000.0
    assert declared.budget.currency == "USD"
    assert declared.budget.stated

    # Unstated is not free. A ceiling of zero would put every deployment
    # permanently over budget, which is a finding nobody reads twice.
    assert not loads(MINIMAL).budget.stated


def test_an_unknown_section_is_refused_rather_than_ignored():
    """A misspelled section that parsed cleanly would deploy something else.

    The environment manifest takes this position and this one inherits it: a
    configuration file silently misread is worse than one that fails to load.
    """
    with pytest.raises(ManifestError) as raised:
        loads(MINIMAL.replace("topology:", "topolgy:\n  api: { replicas: 1 }\ntopology:"))
    assert "topolgy" in str(raised.value)


def test_an_environment_manifest_fed_to_deploy_says_which_file_was_meant():
    """The error names the mistake, not a missing section.

    Complaining that `topology` is absent would be true and unhelpful: the
    operator did not forget a section, they passed the wrong file.
    """
    with pytest.raises(ManifestError) as raised:
        loads("apiVersion: slpie/v1\nkind: Environment\nenvironment: acme\n")
    assert "slpie declare" in str(raised.value)


def test_a_component_must_say_how_big_it_is():
    with pytest.raises(ManifestError) as raised:
        loads("apiVersion: slpie/v1\nenvironment: acme\ntopology:\n  api: { cpu: 2 }\n")
    assert "must state a size" in str(raised.value)


def test_a_range_that_cannot_be_satisfied_is_caught_at_read_time():
    """Not by an autoscaler at three in the morning."""
    with pytest.raises(ManifestError) as raised:
        loads(
            "apiVersion: slpie/v1\nenvironment: acme\n"
            "topology:\n  workers: { min: 10, max: 2 }\n"
        )
    assert "min 10 above max 2" in str(raised.value)


def test_a_region_cannot_be_both_the_writer_and_a_replica():
    """The ledger has one writer, and §23's caching story needs that unambiguous."""
    with pytest.raises(ManifestError) as raised:
        loads(
            "apiVersion: slpie/v1\nenvironment: acme\n"
            "topology:\n  api: { replicas: 1 }\n"
            "regions:\n  primary: eu-west-1\n  replicas: [eu-west-1]\n"
        )
    assert "exactly one writer" in str(raised.value)


def test_a_closed_set_is_closed():
    for section, value in (("platform", "mesos"), ("cloud", "hetzner")):
        with pytest.raises(ManifestError) as raised:
            loads(MINIMAL + f"{section}: {value}\n")
        assert value in str(raised.value)


def test_a_missing_file_says_where_it_looked(tmp_path):
    with pytest.raises(ManifestError) as raised:
        load(tmp_path / "slpie.deployment.yaml")
    assert str(tmp_path) in str(raised.value)


# --- the plan -----------------------------------------------------------------


def test_a_plan_over_an_unobserved_estate_is_every_component_added(declared):
    answer = plan(declared, None)
    assert not answer.empty
    assert {change.kind for change in answer.changes} == {ChangeKind.ADD}
    assert len(answer.changes) == len(declared.components)


def test_a_settled_estate_plans_to_nothing(declared):
    running = {
        item.name: {"size": item.size, "cpu": item.cpu, "memory": item.memory,
                    "ingress": item.ingress, "queues": list(item.queues)}
        for item in declared.components
    }
    answer = plan(declared, running)
    assert answer.empty
    assert set(answer.unchanged) == set(declared.names)
    assert "nothing to do" in answer.summary()


def test_a_platform_writing_a_float_is_not_a_change(declared):
    """`2` and `2.0` are one allocation written by two systems.

    Reporting that as a change would put a line in every plan forever, and a
    plan nobody reads is a plan that stops catching the removals.
    """
    running = {item.name: {"size": item.size, "cpu": float(item.cpu)}
               for item in declared.components}
    assert plan(declared, running).empty


def test_what_a_platform_does_not_report_is_not_reported_as_changed(declared):
    """A platform's silence must not be rendered as our finding."""
    running = {item.name: {"size": item.size} for item in declared.components}
    assert plan(declared, running).empty


def test_an_undeclared_component_is_a_removal_and_says_so(declared):
    running = {item.name: {"size": item.size} for item in declared.components}
    running["legacy-worker"] = {"size": 4}

    answer = plan(declared, running)
    removals = [c for c in answer.changes if c.kind is ChangeKind.REMOVE]
    assert [c.component for c in removals] == ["legacy-worker"]
    assert answer.destructive
    assert "can lose something running" in answer.summary()


def test_a_resize_is_typed_as_a_scale_and_carries_both_numbers(declared):
    running = {item.name: {"size": item.size} for item in declared.components}
    running["api"] = {"size": 9}

    change = plan(declared, running).changes[0]
    assert change.kind is ChangeKind.SCALE
    assert (change.before, change.after) == (9, 3)
    assert "9 → 3" in str(change)


def test_a_shape_change_is_an_alter_rather_than_a_scale(declared):
    running = {item.name: {"size": item.size, "memory": "1Gi"}
               for item in declared.components}
    kinds = {change.kind for change in plan(declared, running).changes}
    assert ChangeKind.ALTER in kinds
    assert ChangeKind.SCALE not in kinds


def test_a_plan_is_ordered_so_two_runs_compare(declared):
    running = {"z-old": {"size": 1}, "a-old": {"size": 1}}
    twice = [plan(declared, dict(running)).to_dict() for _ in range(2)]
    assert twice[0] == twice[1]
    removed = [c["component"] for c in twice[0]["changes"] if c["kind"] == "remove"]
    assert removed == sorted(removed)


# --- the emitters -------------------------------------------------------------


def test_every_emitter_is_registered_and_named():
    assert set(emitters.names()) == {
        "compose", "helm", "kubernetes", "pipelines", "systemd", "terraform",
    }
    assert emitters.emitter("nonesuch") is None


@pytest.mark.parametrize("name", sorted(emitters.EMITTERS))
def test_an_emitter_renders_the_same_bytes_twice(declared, name):
    """Nothing reads the clock, the environment or a random source.

    This is what makes a rendered artifact reviewable in a diff, which is the
    entire reason §18 renders before it applies.
    """
    assert emitters.render(declared, emitter=name) == emitters.render(declared, emitter=name)


@pytest.mark.parametrize("name", sorted(emitters.EMITTERS))
def test_an_emitter_produces_files_and_names_its_limits(declared, name):
    files = emitters.render(declared, emitter=name)
    assert files, f"{name} produced nothing"
    assert all(text.strip() for text in files.values()), f"{name} produced an empty file"
    # Every emitter has a limit and every one of them states it. An emitter
    # reporting no gaps at all would mean it claims to express the whole model.
    assert emitters.gaps(declared, emitter=name)


@pytest.mark.parametrize("name", sorted(emitters.EMITTERS))
def test_every_rendered_file_says_it_is_generated(declared, name):
    for path, text in emitters.render(declared, emitter=name).items():
        assert "Generated by `slpie deploy render`" in text, path
        assert "Edits here are lost" in text, path


def test_an_unknown_emitter_is_refused_with_the_list(declared):
    with pytest.raises(KeyError) as raised:
        emitters.render(declared, emitter="ansible")
    assert "compose" in str(raised.value)


def test_compose_emits_every_service_it_depends_on(declared):
    """The defect this test exists for shipped, briefly.

    `depends_on: [postgres, redis]` was emitted and neither service was, so the
    file was syntactically perfect and `docker compose up` failed on a service
    that did not exist — the class of defect this whole phase exists to prevent,
    produced by the tool that is supposed to prevent it.
    """
    text = emitters.render(declared, emitter="compose")["docker-compose.yaml"]

    declared_services = {
        line.strip().rstrip(":") for line in text.splitlines()
        if line.startswith("  ") and line.rstrip().endswith(":")
        and not line.startswith("    ")
    }
    for line in text.splitlines():
        if "depends_on:" not in line:
            continue
        wanted = line.split("[", 1)[1].rstrip("]").split(", ")
        missing = [item for item in wanted if item not in declared_services]
        assert not missing, f"depends_on names services this file does not emit: {missing}"


def test_compose_declares_every_volume_it_mounts(declared):
    text = emitters.render(declared, emitter="compose")["docker-compose.yaml"]
    body, _, volumes = text.partition("\nvolumes:\n")
    declared_volumes = {
        line.strip().rstrip(":") for line in volumes.splitlines() if line.strip()
    }
    mounted = {
        item.split(":")[0].strip('"')
        for line in body.splitlines() if "volumes: [" in line
        for item in line.split("[", 1)[1].rstrip("]").split(", ")
    }
    assert mounted <= declared_volumes, f"mounted but not declared: {mounted - declared_volumes}"


def test_compose_carries_no_credential(declared):
    """A rendered artifact is a file somebody is about to commit."""
    text = emitters.render(declared, emitter="compose")["docker-compose.yaml"]
    assert "POSTGRES_PASSWORD" in text          # it is named
    assert "${POSTGRES_PASSWORD" in text        # and it comes from the environment


def test_kubernetes_renders_the_elasticity_range_as_an_autoscaler(declared):
    text = emitters.render(declared, emitter="kubernetes")["workers.yaml"]
    assert "HorizontalPodAutoscaler" in text
    assert "minReplicas: 2" in text and "maxReplicas: 40" in text

    # Asymmetric, and the asymmetry is §23's argument rather than a tuning
    # accident: over-provisioned costs money, under-provisioned costs
    # correctness, because a scan that times out is a gap in an answer.
    up = text.split("scaleUp:")[1].split("stabilizationWindowSeconds:")[1]
    down = text.split("scaleDown:")[1].split("stabilizationWindowSeconds:")[1]
    assert int(up.split()[0]) < int(down.split()[0])


def test_a_fixed_component_gets_no_autoscaler(declared):
    assert "HorizontalPodAutoscaler" not in emitters.render(
        declared, emitter="kubernetes")["api.yaml"]


def test_helm_templates_read_the_values_rather_than_hardcoding_them(declared):
    """Otherwise it is a Kubernetes emitter wearing Helm's filenames.

    A chart whose templates hardcode what `values.yaml` declares installs fine
    and then ignores `--set`, which is exactly the surprise a chart prevents.
    """
    files = emitters.render(declared, emitter="helm")
    assert "workers" in files["values.yaml"]

    template = files["templates/deployment.yaml"]
    assert "{{" in template and ".Values.components" in template
    # Matched on a word boundary: `api` is a substring of `apiVersion`, and a
    # test that read that as a hardcoded component would fail forever on a
    # correct template — the kind of false positive that gets a guard deleted.
    import re as _re

    for component in declared.names:
        assert not _re.search(rf"\b{_re.escape(component)}\b", template), (
            f"the template hardcodes {component!r}; --set would do nothing"
        )


def test_terraform_never_defaults_a_password(declared):
    text = emitters.render(declared, emitter="terraform")["variables.tf"]
    block = text.split('variable "database_password"')[1].split("}")[0]
    assert "sensitive = true" in block
    # `default =`, not `default`: the description explains *why* there is no
    # default and contains the word, which is prose rather than a declaration.
    assert "default =" not in block, (
        "a password with a default is a password somebody shipped"
    )


def test_terraform_for_onprem_still_states_the_contract():
    """`onprem` is not an absence: the other renders read these variables."""
    declared = loads(MINIMAL + "cloud: onprem\npersistence:\n  graph: { engine: postgres }\n")
    files = emitters.render(declared, emitter="terraform")
    assert "onprem" in files["main.tf"]
    assert "variable" in files["variables.tf"]
    assert any("onprem" in gap for gap in emitters.gaps(declared, emitter="terraform"))


def test_a_pipeline_gates_its_apply_behind_something_a_human_did(declared):
    """A pipeline that applied on every push is an apply nobody agreed to."""
    files = emitters.render(declared, emitter="pipelines")

    github = files[".github/workflows/deploy.yml"]
    assert "workflow_dispatch" in github
    assert "github.event_name == 'workflow_dispatch'" in github

    assert "when: manual" in files[".gitlab-ci.yml"]
    assert "- deployment: apply" in files["azure-pipelines.yml"]


def test_every_apply_in_a_pipeline_is_confirmed(declared):
    for path, text in emitters.render(declared, emitter="pipelines").items():
        for line in text.splitlines():
            if "deploy apply" in line:
                assert "--confirm" in line, f"{path}: an unconfirmed apply"


def test_systemd_honours_the_drain_grace(declared):
    """A worker killed mid-scan drops observations the ledger never recorded."""
    text = emitters.render(declared, emitter="systemd")["slpie-workers.service"]
    assert f"TimeoutStopSec={declared.elasticity.drain_grace}" in text
    assert "KillSignal=SIGTERM" in text


def test_the_install_script_stops_at_the_first_failure(declared):
    script = emitters.render(declared, emitter="systemd")["install.sh"]
    assert "set -euo pipefail" in script


def test_the_default_emitter_follows_the_declared_platform():
    assert emitters.default_for(loads(MINIMAL + "platform: systemd\n")) == "systemd"
    # `nomad` has no emitter yet, and falling back beats rendering nothing.
    assert emitters.default_for(loads(MINIMAL + "platform: nomad\n")) == "compose"


def test_one_vocabulary_serves_every_emitter(declared):
    """The image and the port are stated once, or six files drift apart."""
    from slpie.deploy.emitters._common import PORTS, image

    for name in ("compose", "kubernetes", "helm"):
        rendered = "\n".join(emitters.render(declared, emitter=name).values())
        assert image().split(":")[0] in rendered
    assert str(PORTS["api"]) in "\n".join(
        emitters.render(declared, emitter="compose").values()
    )


# --- the verbs, and the one gate that matters ---------------------------------


@pytest.fixture()
def written(tmp_path):
    """A deployment manifest on disk, which is how every verb finds one."""
    (tmp_path / "slpie.deployment.yaml").write_text(FULL, encoding="utf-8")
    return tmp_path


def _run(pipeline: str, root, *, confirmed: bool = False):
    from slpie.compose.pipeline import Composition
    from slpie.compose.registry import registry
    from slpie.compose.verb import Context

    verbs = registry()
    composition = Composition.read(pipeline, verbs=verbs)
    return composition.run(Context(root=str(root), confirmed=confirmed))


def test_every_deploy_verb_is_registered_and_reachable():
    from slpie.compose.registry import registry

    verbs = registry()
    names = {name for name in verbs.names if verbs.get(name).group == "deploy"}
    assert names == {
        "deploy-plan", "deploy-render", "deploy-manual",
        "deploy-status", "deploy-apply",
    }


def test_only_apply_mutates():
    """The other four read a file and produce text. Marking them would put a
    confirmation prompt in front of a diff, which teaches people to pass
    `--confirm` reflexively — and then it means nothing when it matters."""
    from slpie.compose.registry import registry

    verbs = registry()
    mutating = {
        name for name in verbs.names
        if verbs.get(name).group == "deploy" and verbs.get(name).mutates
    }
    assert mutating == {"deploy-apply"}


def test_plan_reads_the_manifest_and_touches_nothing(written):
    result = _run("deploy-plan", written)
    assert result.flow.value["environment"] == "acme-production"
    # Three components declared, nothing observed, so three additions.
    assert result.flow.value["changes"]
    assert list(written.iterdir()) == [written / "slpie.deployment.yaml"]


def test_an_unobserved_estate_is_reported_rather_than_assumed_empty(written):
    """A blind plan and a first install produce the same diff.

    They are not the same confidence, and a console that showed only the diff
    would be presenting a guess as an observation.
    """
    result = _run("deploy-plan", written)
    assert any("nothing was asked" in gap for gap in result.flow.value["gaps"])


def test_render_returns_files_without_writing_them(written):
    result = _run("deploy-render --emitter compose", written)
    assert "docker-compose.yaml" in result.flow.value["files"]
    assert result.flow.value["written"] == []
    assert not (written / "deploy").exists()


def test_render_write_puts_them_under_the_root(written):
    result = _run("deploy-render --emitter kubernetes --write", written)
    assert result.flow.value["written"]
    assert (written / "deploy" / "00-namespace.yaml").is_file()


def test_a_render_gap_travels_on_the_flow(written):
    """Invariant 5 holding through composition, applied to an emitter's limits.

    A gap that only printed would be lost the moment `deploy-render` was piped
    into anything, and the limit is exactly what a later stage needs to know.
    """
    result = _run("deploy-render --emitter compose", written)
    assert result.flow.gaps
    assert any("autoscaler" in gap.detail for gap in result.flow.gaps)


def test_the_manual_is_generated_from_the_model(written):
    result = _run("deploy-manual --emitter compose", written)
    text = result.flow.value

    from slpie.deploy.emitters._common import PORTS

    assert f"**{PORTS['api']}**" in text          # the manifest's port
    assert "docker compose up -d" in text          # the emitter's command
    assert "docker (with the compose plugin)" in text
    assert "2–40 (elastic)" in text                # the declared range


def test_the_manual_reports_what_the_render_cannot_do(written):
    result = _run("deploy-manual --emitter compose", written)
    assert "What this render does not do" in result.flow.value
    assert "no autoscaler" in result.flow.value


def test_apply_is_refused_without_confirmation(written):
    """Refused as a composition, before the first stage runs.

    The point is *when*: nothing has happened yet when the operator decides,
    which is what makes the refusal useful rather than an interruption.
    """
    from slpie.compose.pipeline import Composition
    from slpie.compose.registry import registry
    from slpie.compose.verb import Context

    verbs = registry()
    composition = Composition.read("deploy-apply", verbs=verbs)
    validation = composition.validate()
    assert validation.mutating == ("deploy-apply",)

    with pytest.raises(Exception) as raised:
        composition.run(Context(root=str(written), confirmed=False))
    assert "confirm" in str(raised.value).lower()


def test_apply_is_refused_when_the_manifest_only_says_plan(tmp_path):
    """Both gates are required, and they are not redundant.

    The manifest is a statement of intent reviewable in a diff; the flag is a
    person at the moment it happens. Neither is enough on its own — a stale file
    should not apply, and neither should a slip of the hand.
    """
    (tmp_path / "slpie.deployment.yaml").write_text(FULL, encoding="utf-8")
    # A stage's refusal is captured on the `Result` rather than raised — a
    # four-stage pipe that failed at stage three still carries what stages one
    # and two produced (§30's partial-render rule), and that is worth more than
    # a traceback. So the assertion is on the recorded failure.
    result = _run("deploy-apply", tmp_path, confirmed=True)
    assert not result.ok
    assert result.failed == "deploy-apply"
    assert TargetRefused.__name__ in result.error
    assert "target: apply" in result.error


def test_apply_without_an_adapter_is_a_gap_naming_what_is_missing(tmp_path):
    """Never a crash, and never a success nobody performed.

    §27 gives a missing binary this treatment and §3 gives a refused capability
    the same; an apply this build cannot do is the same shape of thing.
    """
    (tmp_path / "slpie.deployment.yaml").write_text(
        FULL.replace("target: plan", "target: apply"), encoding="utf-8")

    result = _run("deploy-apply", tmp_path, confirmed=True)
    assert result.flow.value["applied"] is False
    assert result.flow.gaps
    detail = result.flow.gaps[0].detail
    assert "slpie[enterprise]" in detail
    assert "deploy render --write" in detail, "it does not say what to do instead"


def test_the_root_flag_is_honoured_rather_than_the_working_directory(tmp_path):
    """The defect `docs/AUDIT.md` records for the environment loader, not repeated.

    `slpie --root /somewhere deploy plan` must plan against the manifest at
    `/somewhere`, not against whatever happens to be beside the shell.
    """
    (tmp_path / "slpie.deployment.yaml").write_text(MINIMAL, encoding="utf-8")
    result = _run("deploy-plan", tmp_path)
    assert result.flow.value["environment"] == "small"


def test_a_missing_manifest_says_what_to_do(tmp_path):
    result = _run("deploy-plan", tmp_path)
    assert not result.ok
    assert "slpie.deployment.yaml" in result.error
    # An error that says only "not found" leaves the reader to guess the shape
    # of a file they have never written.
    assert "deploy manual" in result.error


# --- reconciliation applied to the platform itself ----------------------------


def _discovered(text: str, uri: str = "file:///acme/docker-compose.yaml"):
    """Read a rendered file with the platform's *own* discoverer.

    Not a second parser written for the test. The whole claim of this section is
    that what SLPIE emits, SLPIE can read — so the reading has to be done by the
    code that reads a customer's compose file, or the round trip proves nothing
    about the round trip.
    """
    from slpie.discovery.base import Source
    from slpie.discovery.infrastructure.compose import discover_compose

    return discover_compose(Source(uri=uri, text=text, digest="sha256:test"))


def test_what_the_platform_emits_the_platform_can_read(declared):
    """§18's acceptance, in the half that needs no docker daemon.

    Deploy SLPIE, then point SLPIE at the deployment and scan it: the discovered
    topology must match the declared one. If the platform cannot correctly
    describe its own infrastructure, its description of anyone else's is not
    worth having.

    What this does **not** prove is that `docker compose up` succeeds — that
    needs a daemon, and a green tick for something never started would be worse
    than the honest gap `docs/PHASE18.md` records.
    """
    text = emitters.render(declared, emitter="compose")["docker-compose.yaml"]
    answer = _discovered(text)

    assert not answer.errors, answer.errors

    services = {
        observation.properties["service_name"]
        for observation in answer.observations
        if observation.properties.get("node_kind") == "service"
    }
    # Every declared component came back, and nothing was invented. The stores
    # are extra by design — compose runs them as containers on a single host —
    # so they are named rather than tolerated by a loose comparison.
    assert set(declared.names) <= services
    assert services - set(declared.names) == {"postgres", "redis"}


def test_the_round_trip_carries_the_ports_the_manifest_declared(declared):
    """A topology that discovered a different port would reconcile as CONTRADICTED."""
    from slpie.deploy.emitters._common import PORTS

    answer = _discovered(emitters.render(declared, emitter="compose")["docker-compose.yaml"])
    api = next(
        observation for observation in answer.observations
        if observation.properties.get("service_name") == "api"
    )
    assert api.properties["ports"] == [f"{PORTS['api']}:{PORTS['api']}"]


def test_the_round_trip_records_no_credential(declared):
    """The discoverer keeps environment *keys* and never their values.

    Asserted from this side too, because a rendered file and a read one are two
    chances to leak the same secret and only one of them has a test elsewhere.
    """
    answer = _discovered(emitters.render(declared, emitter="compose")["docker-compose.yaml"])
    for observation in answer.observations:
        recorded = observation.properties.get("environment_keys") or []
        assert all(isinstance(key, str) for key in recorded)
        assert not any("${" in key for key in recorded), "a value reached the graph"


def test_a_rendered_kubernetes_manifest_reads_back_as_a_workload(declared):
    """The same round trip for the other platform that has a discoverer."""
    from slpie.discovery.base import Source
    from slpie.discovery.infrastructure.kubernetes import discover_kubernetes

    text = emitters.render(declared, emitter="kubernetes")["workers.yaml"]
    answer = discover_kubernetes(
        Source(uri="file:///acme/workers.yaml", text=text, digest="sha256:test"))

    assert not answer.errors, answer.errors
    assert answer.observations, "the platform cannot read what it just emitted"

    # `workload_kind` for a Deployment and `resource_kind` for everything else —
    # the discoverer's own distinction, not one invented here. Reading both is
    # what makes this assert the round trip rather than one property name.
    read = {
        observation.properties.get("workload_kind")
        or observation.properties.get("resource_kind")
        for observation in answer.observations
    }
    assert {"Deployment", "HorizontalPodAutoscaler"} <= read

    workload = next(
        observation for observation in answer.observations
        if observation.properties.get("workload_kind") == "Deployment"
    )
    # The declared floor came back as the replica count, which is the number
    # that would reconcile as CONTRADICTED if the emitter and the reader
    # disagreed about what a range means.
    assert workload.properties["replicas"] == declared.component("workers").size
