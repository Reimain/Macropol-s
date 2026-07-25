"""UiPath interop: an A2A connection interface over the UiPath Agent SDK.

UiPath agents and processes reach the world in two ways, and this adapter
handles both behind one interface:

1. **Native A2A.** If a UiPath agent publishes an agent card, it is already a
   peer — :meth:`UiPathConnection.connect` returns an ordinary HTTP transport
   with the tenant's bearer token attached, and nothing here is involved
   beyond authentication.

2. **Orchestrator jobs.** Everything else is reached by starting a job and
   polling it. :class:`UiPathTransport` implements the A2A transport contract
   on top of that: ``message/send`` starts a job, ``tasks/get`` maps its state,
   ``tasks/cancel`` stops it. From the caller's side it is A2A; from UiPath's
   side it is a normal job.

The state mapping is the substance of the bridge. A UiPath job has no notion of
``input-required``, and an A2A task has no notion of ``Suspended`` — so
:data:`JOB_STATES` is where the two lifecycles are reconciled, in one table
rather than scattered through the code.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence
from urllib import request as _request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from ...errors import AgentError, TransportError
from ...ids import new_id
from ..client import A2AClient
from ..transport import HttpAuth, HttpTransport
from ..types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Artifact,
    DataPart,
    ErrorCode,
    Message,
    Method,
    Role,
    RpcError,
    RpcRequest,
    RpcResponse,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)

#: UiPath job state to A2A task state. The interesting rows are the last three:
#: a suspended job is waiting on something outside itself, which is exactly what
#: ``input-required`` means to an A2A caller.
JOB_STATES: dict[str, TaskState] = {
    "Pending": TaskState.SUBMITTED,
    "Running": TaskState.WORKING,
    "Stopping": TaskState.WORKING,
    "Terminating": TaskState.WORKING,
    "Resumed": TaskState.WORKING,
    "Successful": TaskState.COMPLETED,
    "Faulted": TaskState.FAILED,
    "Stopped": TaskState.CANCELED,
    "Suspended": TaskState.INPUT_REQUIRED,
}

DEFAULT_ORCHESTRATOR_PATH = "orchestrator_"


@dataclass(frozen=True, slots=True)
class UiPathConfig:
    """Where the UiPath tenant is and how to authenticate to it."""

    base_url: str = ""
    access_token: str = ""
    organization: str = ""
    tenant: str = ""
    #: Orchestrator folder ("organization unit") jobs run in.
    folder_id: str = ""
    folder_path: str = ""
    orchestrator_path: str = DEFAULT_ORCHESTRATOR_PATH
    timeout_s: float = 60.0
    poll_interval_s: float = 2.0
    job_timeout_s: float = 900.0

    @classmethod
    def from_env(cls, **overrides: Any) -> "UiPathConfig":
        """Read the standard UiPath environment variables."""
        return cls(
            base_url=overrides.pop("base_url", None) or os.environ.get("UIPATH_URL", ""),
            access_token=overrides.pop("access_token", None) or os.environ.get("UIPATH_ACCESS_TOKEN", ""),
            organization=overrides.pop("organization", None) or os.environ.get("UIPATH_ORGANIZATION_ID", ""),
            tenant=overrides.pop("tenant", None) or os.environ.get("UIPATH_TENANT_ID", ""),
            folder_id=overrides.pop("folder_id", None) or os.environ.get("UIPATH_FOLDER_KEY", ""),
            folder_path=overrides.pop("folder_path", None) or os.environ.get("UIPATH_FOLDER_PATH", ""),
            **overrides,
        )

    @property
    def orchestrator_url(self) -> str:
        base = self.base_url.rstrip("/")
        if not base:
            raise AgentError(
                "no UiPath base URL; set UIPATH_URL or pass base_url=... "
                "(e.g. https://cloud.uipath.com/acme/DefaultTenant)"
            )
        if self.organization and self.tenant and "/" not in base.split("://", 1)[-1].strip("/"):
            base = f"{base}/{self.organization}/{self.tenant}"
        return f"{base}/{self.orchestrator_path.strip('/')}"

    def headers(self) -> dict[str, str]:
        if not self.access_token:
            raise AgentError("no UiPath access token; set UIPATH_ACCESS_TOKEN or pass access_token=...")
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.folder_id:
            headers["X-UIPATH-OrganizationUnitId"] = str(self.folder_id)
        elif self.folder_path:
            headers["X-UIPATH-FolderPath"] = self.folder_path
        return headers

    def __repr__(self) -> str:  # pragma: no cover - must not leak the token
        return f"<UiPathConfig {self.base_url} tenant={self.tenant or '-'} token={'set' if self.access_token else 'unset'}>"


class UiPathOrchestrator:
    """A minimal Orchestrator REST client.

    Deliberately stdlib-only. When the official ``uipath`` SDK is installed it
    can be handed in instead (see :class:`UiPathTransport`), but a bridge that
    requires a vendor SDK to *evaluate* is a bridge nobody evaluates.
    """

    def __init__(self, config: UiPathConfig) -> None:
        self.config = config

    # -- HTTP ------------------------------------------------------------

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        url = f"{self.config.orchestrator_url}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = _request.Request(url, data=data, headers=self.config.headers(), method=method)
        try:
            with _request.urlopen(req, timeout=self.config.timeout_s) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", "replace") if hasattr(exc, "read") else ""
            raise TransportError(f"UiPath {method} {path} failed: HTTP {exc.code} {detail}".strip()) from exc
        except URLError as exc:
            raise TransportError(f"cannot reach UiPath at {url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise TransportError(f"UiPath returned non-JSON for {path}: {exc}") from exc

    # -- processes -------------------------------------------------------

    def releases(self, *, top: int = 100) -> list[dict[str, Any]]:
        """Published processes (releases) visible in the configured folder."""
        data = self._call("GET", f"/odata/Releases?$top={int(top)}")
        return list(data.get("value", []))

    def release(self, name: str) -> dict[str, Any]:
        filtered = quote(f"Name eq '{name}'", safe="")
        data = self._call("GET", f"/odata/Releases?$filter={filtered}")
        items = list(data.get("value", []))
        if not items:
            raise AgentError(f"no UiPath release named {name!r} in this folder")
        return items[0]

    # -- jobs ------------------------------------------------------------

    def start_job(self, release_key: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "startInfo": {
                "ReleaseKey": release_key,
                "Strategy": "ModernJobsCount",
                "JobsCount": 1,
                "InputArguments": json.dumps(dict(arguments or {}), default=str),
            }
        }
        data = self._call(
            "POST", "/odata/Jobs/UiPath.Server.Configuration.OData.StartJobs", payload
        )
        jobs = list(data.get("value", []))
        if not jobs:
            raise AgentError(f"UiPath accepted the request but started no job for {release_key!r}")
        return jobs[0]

    def job(self, job_id: str | int) -> dict[str, Any]:
        return self._call("GET", f"/odata/Jobs({job_id})")

    def stop_job(self, job_id: str | int, *, strategy: str = "SoftStop") -> dict[str, Any]:
        return self._call(
            "POST", f"/odata/Jobs({job_id})/UiPath.Server.Configuration.OData.StopJob",
            {"strategy": strategy},
        )

    def wait(self, job_id: str | int, *, timeout_s: float = 0.0) -> dict[str, Any]:
        """Poll a job until it leaves the running states."""
        deadline = time.monotonic() + (timeout_s or self.config.job_timeout_s)
        while True:
            job = self.job(job_id)
            state = JOB_STATES.get(str(job.get("State", "")), TaskState.UNKNOWN)
            if state.terminal or state.waiting_on_caller:
                return job
            if time.monotonic() >= deadline:
                raise AgentError(
                    f"UiPath job {job_id} still {job.get('State')} after "
                    f"{timeout_s or self.config.job_timeout_s}s"
                )
            time.sleep(self.config.poll_interval_s)


def job_to_task(job: Mapping[str, Any], *, context_id: str = "") -> Task:
    """Map a UiPath job onto an A2A task, output arguments included."""
    state = JOB_STATES.get(str(job.get("State", "")), TaskState.UNKNOWN)
    task = Task(
        id=f"uipath-{job.get('Id', new_id('job'))}",
        context_id=context_id or new_id("ctx"),
        metadata={
            "source": "uipath",
            "job_id": job.get("Id"),
            "job_key": job.get("Key"),
            "release": (job.get("ReleaseName") or ""),
            "state": job.get("State"),
            "started": job.get("StartTime"),
            "ended": job.get("EndTime"),
        },
    )

    parts: list[Any] = []
    if info := job.get("Info"):
        parts.append(TextPart(text=str(info)))
    outputs = job.get("OutputArguments")
    if outputs:
        try:
            decoded = json.loads(outputs) if isinstance(outputs, str) else outputs
        except json.JSONDecodeError:
            decoded = {"raw": outputs}
        if decoded:
            parts.append(DataPart(data=decoded, metadata={"source": "uipath.OutputArguments"}))
            task.add_artifact(Artifact(name="output_arguments", parts=(parts[-1],)))
    if state is TaskState.FAILED and (reason := job.get("JobError") or job.get("Info")):
        parts.append(TextPart(text=f"job faulted: {reason}"))

    message = Message(role=Role.AGENT, parts=tuple(parts) or (TextPart(text=f"job {state.value}"),),
                      task_id=task.id, context_id=task.context_id)
    task.advance(state, message)
    return task


class UiPathTransport:
    """A2A transport backed by UiPath Orchestrator jobs."""

    name = "uipath"

    def __init__(
        self,
        config: UiPathConfig,
        process: str,
        *,
        orchestrator: UiPathOrchestrator | None = None,
        card: AgentCard | None = None,
        blocking: bool = True,
    ) -> None:
        self.config = config
        self.process = process
        self.orchestrator = orchestrator if orchestrator is not None else UiPathOrchestrator(config)
        self.blocking = blocking
        self._card = card
        self._release: dict[str, Any] | None = None

    # -- transport contract ----------------------------------------------

    def call(self, request: RpcRequest) -> RpcResponse:
        try:
            if request.method in (Method.SEND.value, Method.STREAM.value):
                return RpcResponse(id=request.id, result=self._send(request.params).to_dict())
            if request.method == Method.GET.value:
                job_id = _job_id(str(request.params.get("id", "")))
                return RpcResponse(id=request.id, result=job_to_task(self.orchestrator.job(job_id)).to_dict())
            if request.method == Method.CANCEL.value:
                job_id = _job_id(str(request.params.get("id", "")))
                self.orchestrator.stop_job(job_id)
                return RpcResponse(id=request.id, result=job_to_task(self.orchestrator.job(job_id)).to_dict())
            if request.method == Method.CARD.value:
                return RpcResponse(id=request.id, result=self.agent_card().to_dict())
            return RpcResponse(
                id=request.id,
                error=RpcError(int(ErrorCode.METHOD_NOT_FOUND),
                               f"{request.method!r} has no UiPath equivalent"),
            )
        except (AgentError, TransportError) as exc:
            return RpcResponse(id=request.id, error=RpcError(int(ErrorCode.INTERNAL_ERROR), str(exc)))

    def stream(self, request: RpcRequest) -> Iterator[Mapping[str, Any]]:
        """Poll the job and yield a status update per observed transition.

        UiPath does not push job events, so this is polling wearing a streaming
        interface — honest, and enough for a caller that wants progress.
        """
        if request.method not in (Method.SEND.value, Method.STREAM.value):
            yield self.call(request).to_dict()
            return
        message = Message.from_dict(request.params.get("message", {}))
        job = self.orchestrator.start_job(self._release_key(), self._arguments(message))
        job_id = job.get("Id")
        seen = ""
        deadline = time.monotonic() + self.config.job_timeout_s
        while True:
            current = self.orchestrator.job(job_id)
            state = str(current.get("State", ""))
            if state != seen:
                seen = state
                task = job_to_task(current, context_id=message.context_id)
                yield {
                    "kind": "status-update", "taskId": task.id, "contextId": task.context_id,
                    "status": task.status.to_dict(), "final": task.state.terminal,
                }
                if task.state.terminal or task.state.waiting_on_caller:
                    return
            if time.monotonic() >= deadline:
                raise AgentError(f"UiPath job {job_id} did not settle within {self.config.job_timeout_s}s")
            time.sleep(self.config.poll_interval_s)

    def agent_card(self) -> AgentCard:
        """Describe the UiPath process as an agent card.

        Input and output arguments become the skill's description, so an A2A
        caller can see what the process expects without opening Orchestrator.
        """
        if self._card is not None:
            return self._card
        release = self._release_info()
        arguments = _arguments_of(release)
        detail = ", ".join(f"{name}: {kind}" for name, kind in arguments.items())
        self._card = AgentCard(
            name=f"uipath:{release.get('Name', self.process)}",
            description=(
                release.get("Description")
                or f"UiPath process {release.get('ProcessKey', self.process)!r}"
            ),
            url=self.config.orchestrator_url,
            version=str(release.get("ProcessVersion", "0")),
            capabilities=AgentCapabilities(streaming=True, state_transition_history=True),
            skills=(
                AgentSkill(
                    id="run",
                    name=str(release.get("Name", self.process)),
                    description=(
                        f"Start this process as an Orchestrator job."
                        + (f" Input arguments: {detail}." if detail else "")
                    ),
                    tags=("uipath", "rpa", "orchestrator"),
                    input_modes=("application/json",),
                    output_modes=("application/json",),
                ),
            ),
            provider={"organization": "UiPath", "url": self.config.base_url},
            extra={"folder": self.config.folder_path or self.config.folder_id},
        )
        return self._card

    # -- internals -------------------------------------------------------

    def _release_info(self) -> dict[str, Any]:
        if self._release is None:
            self._release = self.orchestrator.release(self.process)
        return self._release

    def _release_key(self) -> str:
        key = self._release_info().get("Key")
        if not key:
            raise AgentError(f"UiPath release {self.process!r} has no Key to start")
        return str(key)

    def _send(self, params: Mapping[str, Any]) -> Task:
        message = Message.from_dict(params.get("message", {}))
        job = self.orchestrator.start_job(self._release_key(), self._arguments(message))
        if not self.blocking:
            return job_to_task(job, context_id=message.context_id)
        settled = self.orchestrator.wait(job.get("Id"))
        return job_to_task(settled, context_id=message.context_id)

    @staticmethod
    def _arguments(message: Message) -> dict[str, Any]:
        """Map A2A parts onto UiPath input arguments.

        Data parts win: a workflow's arguments are structured, so structured
        input maps directly. Text is passed as `input` so a process that takes a
        single string still works.
        """
        arguments: dict[str, Any] = {}
        for payload in message.data_content():
            if isinstance(payload, Mapping):
                arguments.update({str(k): v for k, v in payload.items()})
            else:
                arguments.setdefault("records", payload)
        if text := message.text_content():
            arguments.setdefault("input", text)
        arguments.setdefault("a2a_context_id", message.context_id)
        return arguments


def _arguments_of(release: Mapping[str, Any]) -> dict[str, str]:
    """Pull the declared input arguments out of a release definition."""
    raw = release.get("Arguments") or {}
    inputs = raw.get("Input") if isinstance(raw, Mapping) else None
    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs)
        except json.JSONDecodeError:
            return {}
    if not isinstance(inputs, list):
        return {}
    return {str(a.get("name", "")): str(a.get("type", "Any")) for a in inputs if isinstance(a, Mapping)}


def _job_id(task_id: str) -> str:
    return task_id[len("uipath-") :] if task_id.startswith("uipath-") else task_id


class UiPathConnection:
    """Entry point: connect to UiPath as an A2A peer, however it is exposed."""

    def __init__(self, config: UiPathConfig | None = None, *, flow: Any = None) -> None:
        self.config = config if config is not None else UiPathConfig.from_env()
        self.flow = flow

    def native(self, url: str, **kwargs: Any) -> A2AClient:
        """Connect to a UiPath agent that speaks A2A directly."""
        auth = HttpAuth(scheme="Bearer", token=self.config.access_token)
        return A2AClient(
            HttpTransport(url, auth=auth, timeout=self.config.timeout_s),
            flow=self.flow, **kwargs,
        )

    def process(self, name: str, *, blocking: bool = True, **kwargs: Any) -> A2AClient:
        """Connect to an Orchestrator process through the job bridge."""
        return A2AClient(
            UiPathTransport(self.config, name, blocking=blocking), flow=self.flow, **kwargs
        )

    def connect(self, target: str, **kwargs: Any) -> A2AClient:
        """Native A2A when ``target`` is a URL, the job bridge otherwise."""
        if "://" in target:
            return self.native(target, **kwargs)
        return self.process(target, **kwargs)

    def catalog(self) -> list[dict[str, Any]]:
        """Every process reachable in the configured folder."""
        orchestrator = UiPathOrchestrator(self.config)
        return [
            {
                "name": release.get("Name"),
                "process_key": release.get("ProcessKey"),
                "version": release.get("ProcessVersion"),
                "description": release.get("Description", ""),
                "arguments": _arguments_of(release),
            }
            for release in orchestrator.releases()
        ]

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<UiPathConnection {self.config.base_url or '<unconfigured>'}>"
