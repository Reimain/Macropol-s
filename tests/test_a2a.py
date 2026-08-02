"""The A2A surface: wire types, task lifecycle, transports, and the adapters."""

from __future__ import annotations

import json

import pytest

from gratimos.a2a import (
    A2AClient,
    AgentRegistry,
    AgentServer,
    AgentSkill,
    DataPart,
    HttpTransport,
    Message,
    Method,
    Role,
    RpcRequest,
    Task,
    TaskState,
    TextPart,
    build_card,
    serve_http,
)
from gratimos.a2a.adapters.uipath import (
    JOB_STATES,
    UiPathConfig,
    UiPathOrchestrator,
    UiPathTransport,
    job_to_task,
)
from gratimos.contextflow import ContextFlow
from gratimos.errors import AgentError, ProtocolError
from gratimos.meta import Wrapped

from fixtures import ORDERS


def auditor(context):
    """A specialist that counts rows and flags blanks."""
    context.working("auditing")
    rows = context.data[0] if context.data else []
    issues = [i for i, row in enumerate(rows) if isinstance(row, dict) and not row.get("customer")]
    context.artifact("audit", {"rows": len(rows), "issues": issues})
    context.complete(f"audited {len(rows)} rows, {len(issues)} issue(s)")


def build_server(flow=None) -> AgentServer:
    card = build_card(
        "shape-auditor",
        description="Audits shaped payloads",
        skills=[AgentSkill(id="audit", name="Audit", description="Find gaps", tags=("qa",))],
    )
    return AgentServer(card, auditor, flow=flow)


# --- wire types ----------------------------------------------------------

def test_messages_round_trip_through_the_wire_format():
    message = Message(
        role=Role.USER,
        parts=(TextPart(text="hello"), DataPart(data=[{"a": 1}], metadata={"entity": "t"})),
        context_id="ctx-1",
    )
    restored = Message.from_dict(json.loads(json.dumps(message.to_dict())))
    assert restored.text_content() == "hello"
    assert restored.data_content() == [[{"a": 1}]]
    assert restored.context_id == "ctx-1"


def test_agent_cards_preserve_unknown_fields():
    raw = {
        "name": "peer", "version": "9", "skills": [],
        "capabilities": {"streaming": True},
        "somethingNewInTheSpec": {"x": 1},
    }
    card = build_card("x")  # sanity: our own builder works
    parsed = type(card).from_dict(raw)
    assert parsed.extra["somethingNewInTheSpec"] == {"x": 1}
    assert parsed.to_dict()["somethingNewInTheSpec"] == {"x": 1}


def test_task_states_classify_correctly():
    assert TaskState.COMPLETED.terminal and not TaskState.COMPLETED.waiting_on_caller
    assert TaskState.INPUT_REQUIRED.waiting_on_caller and not TaskState.INPUT_REQUIRED.terminal
    assert TaskState.WORKING.terminal is False


# --- server / client -----------------------------------------------------

def test_in_process_exchange_completes_with_artifacts():
    flow = ContextFlow("kernel")
    client = A2AClient(build_server(flow).transport(), flow=flow)

    payload = Wrapped.of(ORDERS + [{**ORDERS[0], "customer": ""}], name="orders")
    exchange = client.delegate(payload, skill="audit", instruction="check gaps")

    assert exchange.ok
    assert "audited 4 rows" in exchange.text()
    assert exchange.data() == [{"rows": 4, "issues": [3]}]
    assert any(e.kind.value == "agent" for e in flow)


def test_unadvertised_skills_are_refused_before_sending():
    client = A2AClient(build_server().transport())
    with pytest.raises(AgentError, match="does not advertise skill"):
        client.send("x", skill="nope")


def test_streaming_yields_the_lifecycle_in_order():
    client = A2AClient(build_server().transport())
    events = list(client.stream("go", skill="audit"))
    kinds = [e.get("kind") for e in events]
    assert kinds[0] == "task"
    assert "artifact-update" in kinds
    assert events[-1]["status"]["state"] == TaskState.COMPLETED.value


def test_executor_faults_become_failed_tasks():
    def broken(context):
        raise RuntimeError("kaboom")

    server = AgentServer(build_card("broken"), broken)
    client = A2AClient(server.transport())
    exchange = client.send("go")
    assert exchange.state is TaskState.FAILED
    assert "kaboom" in exchange.text()


def test_unknown_methods_and_tasks_return_typed_errors():
    server = build_server()
    unknown = server.handle(RpcRequest(method="does/not/exist"))
    assert unknown.error.code == -32601

    missing = server.handle(RpcRequest(method=Method.GET.value, params={"id": "nope"}))
    assert missing.error.code == -32001


def test_completed_tasks_cannot_be_reopened():
    server = build_server()
    client = A2AClient(server.transport())
    exchange = client.send("go", skill="audit")
    with pytest.raises(ProtocolError, match="already completed"):
        client.respond(exchange.task.id, "more")


def test_cancel_moves_a_live_task_to_canceled():
    def waiter(context):
        context.input_required("need more")

    server = AgentServer(build_card("waiter"), waiter)
    client = A2AClient(server.transport())
    exchange = client.send("go")
    assert exchange.needs_input

    canceled = client.cancel(exchange.task.id)
    assert canceled.state is TaskState.CANCELED


def test_http_transport_discovers_the_card_and_runs_a_task():
    server = build_server(ContextFlow("kernel"))
    httpd = serve_http(server)
    try:
        client = A2AClient(HttpTransport(httpd.gratimos_url))
        assert client.card.name == "shape-auditor"
        assert client.skills() == ["audit"]

        exchange = client.send("audit this", skill="audit")
        assert exchange.ok
        assert client.get(exchange.task.id).state is TaskState.COMPLETED
    finally:
        httpd.shutdown()


def test_auth_never_appears_in_a_repr():
    from gratimos.a2a import HttpAuth

    auth = HttpAuth(scheme="Bearer", token="super-secret-value")
    assert "super-secret" not in repr(auth)
    assert auth.headers()["Authorization"] == "Bearer super-secret-value"


# --- registry ------------------------------------------------------------

def test_registry_routes_by_capability():
    registry = AgentRegistry()
    registry.host(build_server(), tags=("qa",))

    assert registry.best_for("audit") is not None
    assert registry.best_for("nonexistent") is None
    assert registry.with_tag("qa")
    assert registry.catalog()["by_skill"] == {"audit": ["shape_auditor"]}

    exchange = registry.ask([{"customer": "Ada"}], skill="audit")
    assert exchange.ok


def test_registry_reports_unhealthy_peers():
    def broken(context):
        raise RuntimeError("down")

    registry = AgentRegistry()
    registry.host(AgentServer(build_card("flaky"), broken))
    for _ in range(3):
        registry.ask("go")
    peer = registry.require("flaky")
    assert not peer.healthy
    assert "flaky" in registry.catalog()["unhealthy"]


def test_asking_with_no_capable_peer_raises():
    with pytest.raises(AgentError, match="no peer can handle"):
        AgentRegistry().ask("go", skill="audit")


# --- UiPath bridge -------------------------------------------------------

class FakeOrchestrator(UiPathOrchestrator):
    """Walks a job through Pending -> Running -> Successful."""

    SEQUENCE = ["Pending", "Running", "Successful"]

    def __init__(self, config, *, final: str = "Successful"):
        super().__init__(config)
        self.jobs: dict[int, dict] = {}
        self.started: list[dict] = []
        self.final = final

    def release(self, name):
        return {
            "Key": "release-key", "Name": name, "ProcessVersion": "2.1",
            "Description": "Posts invoices",
            "Arguments": {"Input": json.dumps([{"name": "records", "type": "IEnumerable"}])},
        }

    def start_job(self, release_key, arguments=None):
        index = len(self.jobs) + 1
        self.started.append(dict(arguments or {}))
        self.jobs[index] = {"Id": index, "State": "Pending", "ReleaseName": "Invoice"}
        return self.jobs[index]

    def job(self, job_id):
        job = self.jobs[int(job_id)]
        sequence = self.SEQUENCE[:-1] + [self.final]
        position = min(sequence.index(job["State"]) + 1, len(sequence) - 1)
        job["State"] = sequence[position]
        if job["State"] == "Successful":
            job["OutputArguments"] = json.dumps({"posted": 3, "ledger_ref": "LG-77"})
        return job

    def stop_job(self, job_id, *, strategy="SoftStop"):
        self.jobs[int(job_id)]["State"] = "Stopped"
        return self.jobs[int(job_id)]


def _uipath_client(**kwargs):
    config = UiPathConfig(base_url="https://cloud.uipath.com/acme/Default", access_token="t")
    return A2AClient(UiPathTransport(config, "Invoice",
                                     orchestrator=FakeOrchestrator(config, **kwargs)))


def test_uipath_process_appears_as_an_agent_card():
    card = _uipath_client().card
    assert card.name == "uipath:Invoice"
    assert card.version == "2.1"
    assert "records: IEnumerable" in card.skills[0].description


def test_uipath_job_output_comes_back_as_task_data():
    client = _uipath_client()
    exchange = client.delegate(Wrapped.of(ORDERS, name="orders"), instruction="post these")

    assert exchange.ok
    assert exchange.data() == [{"posted": 3, "ledger_ref": "LG-77"}]
    assert exchange.task.metadata["source"] == "uipath"
    sent = client.transport.orchestrator.started[0]
    assert set(sent) == {"records", "input", "a2a_context_id"}


def test_uipath_faults_map_to_failed_tasks():
    exchange = _uipath_client(final="Faulted").send("run")
    assert exchange.state is TaskState.FAILED


@pytest.mark.parametrize(
    "job_state,expected",
    [
        ("Pending", TaskState.SUBMITTED),
        ("Running", TaskState.WORKING),
        ("Successful", TaskState.COMPLETED),
        ("Faulted", TaskState.FAILED),
        ("Stopped", TaskState.CANCELED),
        ("Suspended", TaskState.INPUT_REQUIRED),
        ("SomethingNew", TaskState.UNKNOWN),
    ],
)
def test_job_state_mapping(job_state: str, expected: TaskState):
    assert job_to_task({"Id": 1, "State": job_state}).state is expected


def test_uipath_config_never_prints_its_token():
    config = UiPathConfig(base_url="https://x/a/b", access_token="secret-token-value")
    assert "secret-token" not in repr(config)
    assert config.headers()["Authorization"].endswith("secret-token-value")


def test_uipath_streaming_reports_each_transition():
    client = _uipath_client()
    states = [event["status"]["state"] for event in client.stream("run")]
    assert states[-1] == TaskState.COMPLETED.value


# --- Claude adapter ------------------------------------------------------

def test_claude_prompt_preserves_shape_and_records():
    from gratimos.a2a.adapters.claude import ClaudeExecutor
    from gratimos.a2a.server import ExecutionContext

    executor = ClaudeExecutor()
    payload, _ = Wrapped.of(ORDERS, name="orders").cast()
    message = Message(parts=(
        TextPart(text="Find mistyped fields."),
        DataPart(data=payload.records(), metadata={
            "entity": "orders", "shape": payload.shape.to_dict(), "rows": payload.rows,
        }),
    ))
    rendered = executor._render(ExecutionContext(task=Task(), message=message, server=None))

    assert "Find mistyped fields." in rendered
    assert "<observed_shape>" in rendered
    assert "placed: date" in rendered
    assert '"customer": "Ada"' in rendered


def test_claude_request_uses_current_api_parameters():
    from gratimos.a2a.adapters.claude import ClaudeConfig, ClaudeExecutor

    kwargs = ClaudeExecutor(ClaudeConfig()).\
        _request_kwargs()
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "high"}
    # Sampling parameters are rejected by this model family.
    assert "temperature" not in kwargs and "top_p" not in kwargs
    assert kwargs["max_tokens"] == 64_000  # streaming default


def test_claude_agent_advertises_kernel_tools_when_given_a_hub():
    from gratimos.a2a.adapters.claude import claude_agent, hub_tools
    from gratimos.hubs import DataHub

    hub = DataHub()
    hub.publish(Wrapped.of(ORDERS, name="orders"))
    agent = claude_agent(hub=hub)

    assert {s.id for s in agent.card.skills} == {"analyze", "explain", "inspect"}
    assert agent.card.provider["organization"] == "Anthropic"

    tools = {t.name: t for t in hub_tools(hub)}
    assert tools["list_entities"].run({})["entities"] == ["orders"]
    assert tools["sample_entity"].run({"entity": "orders", "limit": 2})["total_rows"] == 3
    assert "error" in tools["describe_entity"].run({"entity": "nope"})
