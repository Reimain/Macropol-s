"""Materialisation — a declaration becomes real files on a real filesystem.

Nothing here is a mock. From `- root: ./services/payments` this writes a genuine
`package-lock.json` in npm's v3 format, a genuine `go.mod`, a genuine
`Dockerfile`, a real git repository with real commits. The discoverers that run
against it are the same objects that run against a production checkout, reading
bytes through the same connector class.

That matters for one reason: if the simulator returned canned answers, a passing
simulated case would only prove that the mock agreed with the test. Because it
writes real artifacts, a green case is evidence about the code path that will
run against the real environment.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..environment.manifest import Declaration, DeclarationKind


@dataclass(frozen=True, slots=True)
class Artifact:
    """One file the simulator wrote, and what it is meant to represent."""

    path: Path
    kind: str
    logical_uri: str = ""

    @property
    def digest(self) -> str:
        return hashlib.blake2b(self.path.read_bytes(), digest_size=16).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "kind": self.kind, "uri": self.logical_uri}


def materialize(
    declaration: Declaration,
    root: Path,
    *,
    packages: Sequence[Mapping[str, Any]] = (),
    ecosystem: str = "npm",
    git: bool = True,
) -> list[Artifact]:
    """Write a declaration out as real artifacts under ``root``.

    ``packages`` lets a scenario decide what the world contains — which is how
    "a CVE lands on lodash 4.17.20" becomes a testable condition rather than a
    hypothetical.
    """
    target = root / declaration.name
    target.mkdir(parents=True, exist_ok=True)
    written: list[Artifact] = []

    if declaration.kind is DeclarationKind.CODEBASE:
        written.extend(_codebase(declaration, target, packages, ecosystem))
        if git:
            written.extend(_git_repository(target))
    elif declaration.kind is DeclarationKind.DATA:
        written.extend(_data(declaration, target))
    elif declaration.kind is DeclarationKind.NETWORK:
        written.extend(_network(declaration, target))
    elif declaration.kind is DeclarationKind.WEB:
        written.extend(_web(declaration, target, packages))
    elif declaration.kind is DeclarationKind.IOT:
        written.extend(_iot(declaration, target))
    elif declaration.kind is DeclarationKind.PROVIDER:
        written.extend(_provider(declaration, target))

    return written


# --- codebases -----------------------------------------------------------


DEFAULT_PACKAGES: tuple[Mapping[str, Any], ...] = (
    {"name": "lodash", "version": "4.17.20", "license": "MIT"},
    {"name": "express", "version": "4.18.2", "license": "MIT"},
    {"name": "semver", "version": "7.5.4", "license": "ISC"},
)


def _codebase(
    declaration: Declaration,
    target: Path,
    packages: Sequence[Mapping[str, Any]],
    ecosystem: str,
) -> list[Artifact]:
    entries = list(packages) if packages else list(DEFAULT_PACKAGES)
    language = str(declaration.attributes.get("language", ecosystem)).lower()
    written: list[Artifact] = []

    if language in ("npm", "javascript", "typescript", "node", "next"):
        written.extend(_npm(declaration, target, entries))
    elif language in ("python", "pip", "poetry", "uv"):
        written.extend(_python(declaration, target, entries))
    elif language in ("go", "golang"):
        written.extend(_go(declaration, target, entries))
    else:
        written.extend(_npm(declaration, target, entries))

    return written


def _npm(
    declaration: Declaration, target: Path, packages: Sequence[Mapping[str, Any]]
) -> list[Artifact]:
    """A real package.json and a real lockfileVersion 3 package-lock.json."""
    manifest = {
        "name": declaration.name,
        "version": "1.0.0",
        "license": "Apache-2.0",
        "dependencies": {
            str(entry["name"]): f"^{entry['version']}" for entry in packages
        },
    }
    lock_packages: dict[str, Any] = {
        "": {
            "name": declaration.name,
            "version": "1.0.0",
            "license": "Apache-2.0",
            "dependencies": manifest["dependencies"],
        }
    }
    for entry in packages:
        lock_packages[f"node_modules/{entry['name']}"] = {
            "version": entry["version"],
            "resolved": (
                f"https://registry.npmjs.org/{entry['name']}/-/"
                f"{entry['name']}-{entry['version']}.tgz"
            ),
            "integrity": _fake_integrity(f"{entry['name']}@{entry['version']}"),
            "license": entry.get("license", "MIT"),
        }
    lock = {
        "name": declaration.name,
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": lock_packages,
    }

    source = "\n".join(
        [f"const {_identifier(entry['name'])} = require('{entry['name']}');" for entry in packages]
        + ["", "module.exports = { start: () => console.log('ok') };", ""]
    )

    return [
        _write(target / "package.json", json.dumps(manifest, indent=2) + "\n", "npm-manifest"),
        _write(target / "package-lock.json", json.dumps(lock, indent=2) + "\n", "npm-lockfile"),
        _write(target / "src" / "index.js", source, "source"),
    ]


def _python(
    declaration: Declaration, target: Path, packages: Sequence[Mapping[str, Any]]
) -> list[Artifact]:
    """A real pyproject.toml, requirements.txt and importable module."""
    dependencies = ",\n  ".join(
        f'"{entry["name"]}>={entry["version"]}"' for entry in packages
    )
    pyproject = (
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        f'name = "{declaration.name}"\n'
        'version = "1.0.0"\n'
        'license = { text = "Apache-2.0" }\n'
        f"dependencies = [\n  {dependencies}\n]\n"
    )
    requirements = "\n".join(
        f"{entry['name']}=={entry['version']}" for entry in packages
    ) + "\n"
    source = "\n".join(
        [f"import {_identifier(entry['name'])}" for entry in packages]
        + ["", "", "def start():", '    return "ok"', ""]
    )

    return [
        _write(target / "pyproject.toml", pyproject, "python-manifest"),
        _write(target / "requirements.txt", requirements, "python-requirements"),
        _write(target / declaration.name.replace("-", "_") / "__init__.py", source, "source"),
    ]


def _go(
    declaration: Declaration, target: Path, packages: Sequence[Mapping[str, Any]]
) -> list[Artifact]:
    module = f"github.com/acme/{declaration.name}"
    requires = "\n".join(
        f"\tgithub.com/vendor/{entry['name']} v{entry['version']}" for entry in packages
    )
    go_mod = f"module {module}\n\ngo 1.21\n\nrequire (\n{requires}\n)\n"
    go_sum = "\n".join(
        f"github.com/vendor/{entry['name']} v{entry['version']}/go.mod "
        f"h1:{_fake_integrity(entry['name'])[7:]}"
        for entry in packages
    ) + "\n"
    imports = "\n".join(
        f'\t"github.com/vendor/{entry["name"]}"' for entry in packages
    )
    source = f'package main\n\nimport (\n{imports}\n)\n\nfunc main() {{}}\n'

    return [
        _write(target / "go.mod", go_mod, "go-manifest"),
        _write(target / "go.sum", go_sum, "go-lockfile"),
        _write(target / "main.go", source, "source"),
    ]


def _git_repository(target: Path) -> list[Artifact]:
    """A genuine git repository, when git is available.

    Real commits, so the SCM discoverer reads actual history rather than a
    fixture shaped like one. Absence of git is a capability gap, not a failure —
    the environment simply has one fewer thing to observe.
    """
    if not _git_available():
        return []
    try:
        _git(target, "init", "--quiet", "--initial-branch=main")
        _git(target, "config", "user.email", "simulator@slpie.local")
        _git(target, "config", "user.name", "SLPIE Simulator")
        _git(target, "add", "-A")
        _git(target, "commit", "--quiet", "-m", "materialised by the SLPIE simulator")
    except (subprocess.CalledProcessError, OSError):
        return []
    return [Artifact(path=target / ".git", kind="git-repository")]


_GIT_AVAILABLE: bool | None = None


def _git_available() -> bool:
    global _GIT_AVAILABLE
    if _GIT_AVAILABLE is None:
        try:
            subprocess.run(
                ["git", "--version"], capture_output=True, check=True, timeout=10
            )
            _GIT_AVAILABLE = True
        except (OSError, subprocess.SubprocessError):
            _GIT_AVAILABLE = False
    return _GIT_AVAILABLE


def _git(target: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(target), *arguments],
        capture_output=True, check=True, timeout=30,
    )


# --- everything else -----------------------------------------------------


def _data(declaration: Declaration, target: Path) -> list[Artifact]:
    """A real SQL schema and a JSON Schema document."""
    kind = str(declaration.attributes.get("kind", "dataset"))
    classification = str(declaration.attributes.get("classification", ""))
    table = declaration.name.replace("-", "_")

    sql = (
        f"-- {declaration.name} ({kind})"
        + (f" classification: {classification}" if classification else "")
        + "\n"
        f"CREATE TABLE {table} (\n"
        "    id          BIGINT PRIMARY KEY,\n"
        "    customer_id BIGINT NOT NULL,\n"
        "    email       TEXT NOT NULL,\n"
        "    total       NUMERIC(12,2) NOT NULL,\n"
        "    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()\n"
        ");\n"
        f"CREATE INDEX {table}_customer ON {table}(customer_id);\n"
    )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": declaration.name,
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "customer_id": {"type": "integer"},
            "email": {"type": "string", "format": "email"},
            "total": {"type": "number"},
            "created_at": {"type": "string", "format": "date-time"},
        },
        "required": ["id", "customer_id"],
    }
    return [
        _write(target / "schema.sql", sql, "sql-schema"),
        _write(target / "schema.json", json.dumps(schema, indent=2) + "\n", "json-schema"),
    ]


def _network(declaration: Declaration, target: Path) -> list[Artifact]:
    """A real OpenAPI or AsyncAPI document, matching the declared kind."""
    kind = str(declaration.attributes.get("kind", "rest"))
    if kind in ("event-stream", "queue"):
        document = {
            "asyncapi": "2.6.0",
            "info": {"title": declaration.name, "version": "1.0.0"},
            "channels": {
                f"{declaration.name}.events": {
                    "subscribe": {
                        "message": {
                            "payload": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}},
                            }
                        }
                    }
                }
            },
        }
        return [_write(
            target / "asyncapi.json", json.dumps(document, indent=2) + "\n", "asyncapi"
        )]

    document = {
        "openapi": "3.1.0",
        "info": {"title": declaration.name, "version": "1.0.0"},
        "servers": [{"url": str(declaration.attributes.get("url", "https://example.invalid"))}],
        "paths": {
            "/orders": {
                "get": {
                    "operationId": "listOrders",
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "operationId": "createOrder",
                    "responses": {"201": {"description": "created"}},
                },
            }
        },
    }
    return [_write(
        target / "openapi.json", json.dumps(document, indent=2) + "\n", "openapi"
    )]


def _web(
    declaration: Declaration, target: Path, packages: Sequence[Mapping[str, Any]]
) -> list[Artifact]:
    written = _npm(declaration, target, list(packages) if packages else list(DEFAULT_PACKAGES))
    framework = str(declaration.attributes.get("framework", "react"))
    written.append(_write(
        target / "app" / "page.jsx",
        f"export default function Page() {{ return <main>{declaration.name}</main>; }}\n",
        "source",
    ))
    written.append(_write(
        target / f"{framework}.config.js",
        f"module.exports = {{ framework: '{framework}' }};\n",
        "build-config",
    ))
    return written


def _iot(declaration: Declaration, target: Path) -> list[Artifact]:
    classes = declaration.attributes.get("device_classes") or ["default-v1"]
    document = {
        "broker": str(declaration.attributes.get("broker", "")),
        "protocol": str(declaration.attributes.get("protocol", "mqtt")),
        "device_classes": list(classes),
        "topics": [f"fleet/{name}/telemetry" for name in classes],
    }
    return [_write(
        target / "devices.json", json.dumps(document, indent=2) + "\n", "iot-registry"
    )]


def _provider(declaration: Declaration, target: Path) -> list[Artifact]:
    document = {
        "name": declaration.name,
        "kind": str(declaration.attributes.get("kind", "external-api")),
        "endpoints": [f"https://api.{declaration.name}.invalid/v1"],
    }
    return [_write(
        target / "provider.json", json.dumps(document, indent=2) + "\n", "provider-manifest"
    )]


def container_artifacts(target: Path, *, base: str = "node:20-alpine") -> list[Artifact]:
    """A real Dockerfile and Kubernetes manifest, for the infra discoverers."""
    dockerfile = (
        f"FROM {base}\n"
        "WORKDIR /app\n"
        "COPY package*.json ./\n"
        "RUN npm ci --omit=dev\n"
        "COPY . .\n"
        "EXPOSE 8080\n"
        'CMD ["node", "src/index.js"]\n'
    )
    deployment = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        f"  name: {target.name}\n"
        "spec:\n"
        "  replicas: 2\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        f"        - name: {target.name}\n"
        f"          image: registry.invalid/{target.name}:1.0.0\n"
        "          ports:\n"
        "            - containerPort: 8080\n"
    )
    return [
        _write(target / "Dockerfile", dockerfile, "dockerfile"),
        _write(target / "k8s" / "deployment.yaml", deployment, "kubernetes"),
    ]


# --- helpers -------------------------------------------------------------


def _write(path: Path, content: str, kind: str) -> Artifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return Artifact(path=path, kind=kind)


def _fake_integrity(seed: str) -> str:
    """A well-formed SRI string. Deterministic, and not a real package hash.

    Well-formed because the lockfile parser validates the shape; deliberately
    not a real hash because nothing here corresponds to a published artifact and
    pretending otherwise would let a simulated integrity check appear to pass.
    """
    digest = hashlib.sha512(f"slpie-simulated:{seed}".encode("utf-8")).digest()
    import base64

    return "sha512-" + base64.b64encode(digest).decode("ascii")


def _identifier(name: str) -> str:
    cleaned = name.replace("@", "").replace("/", "_").replace("-", "_").replace(".", "_")
    return cleaned or "module"
