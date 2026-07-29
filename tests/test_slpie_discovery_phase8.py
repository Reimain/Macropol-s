"""Discovery II: JVM, .NET, containers, orchestration, IaC and interface contracts.

Every test reads a real artifact in its real format. The ones that matter are
the format traps — the places where a discoverer that looks correct in review is
wrong on the first real repository:

* a Maven POM inheriting a version from `<properties>`, and namespaced XML
* a multi-stage Dockerfile whose later stage copies from an earlier one, which
  must not become an external image dependency
* a multi-document Kubernetes stream
* AsyncAPI 2.x, where `publish` means the application *receives*
* an element URN never leaking into a package identity — the bug that would
  silently stop two discoverers converging on one node
"""

from __future__ import annotations

import pytest

from slpie.discovery.base import Source, scope_of
from slpie.discovery.ecosystems import gradle, maven, nuget
from slpie.discovery.infrastructure import compose, dockerfile, helm, kubernetes, terraform
from slpie.discovery.interfaces import asyncapi, openapi
from slpie.discovery.registry import builtin_modules, register_builtins
from slpie.plugins.registry import Registry

ELEMENT = "urn:slpie:codebase:payments"


def read(module, text: str, *, name: str, index: int = 0):
    """Run one discoverer over one artifact, the way the scanner would."""
    handler = module.DISCOVERERS[index][1]
    return handler(Source(uri=f"file:///repo/{name}", text=text, digest="",
                          element=ELEMENT))


def objects(found) -> set[str]:
    return {observation.object for observation in found.observations if observation.object}


def subjects(found) -> set[str]:
    return {observation.subject for observation in found.observations}


# --- the identity rule ----------------------------------------------------


def test_an_element_urn_reduces_to_a_name_that_can_sit_inside_an_identity():
    assert scope_of("urn:slpie:codebase:payments") == "payments"
    assert scope_of("urn:slpie:codebase:acme/payments") == "payments"
    assert scope_of("") == "workspace"
    assert scope_of("", "compose") == "compose"


@pytest.mark.parametrize("module,text,name", [
    (gradle, "dependencies {\n  implementation 'a:b:1.0'\n}\n", "build.gradle"),
    (dockerfile, "FROM node:20\n", "Dockerfile"),
    (compose, "services:\n  api:\n    image: acme/api:1\n", "docker-compose.yaml"),
])
def test_no_discoverer_leaks_the_element_urn_into_an_identity(module, text, name):
    """The bug that would stop two discoverers ever converging on one node."""
    found = read(module, text, name=name)
    for observation in found.observations:
        for identity in (observation.subject, observation.object):
            assert "urn%3A" not in (identity or "")
            assert "urn:slpie:codebase" not in (identity or "")


# --- Maven ----------------------------------------------------------------

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.acme</groupId>
  <artifactId>payments</artifactId>
  <version>1.0.0</version>
  <properties><jackson.version>2.15.2</jackson.version></properties>
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
      <version>${jackson.version}</version>
    </dependency>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.0</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""


def test_a_namespaced_pom_is_read_at_all():
    """Maven POMs are namespaced; forgetting that is the classic silent zero."""
    found = read(maven, POM, name="pom.xml")
    assert found.observations
    assert "pkg:maven/com.acme/payments@1.0.0" in subjects(found) | objects(found)


def test_a_version_inherited_from_properties_is_substituted():
    found = read(maven, POM, name="pom.xml")
    databind = [o for o in objects(found) if "jackson-databind" in o]
    assert databind
    assert "${" not in databind[0]


def test_a_test_scoped_dependency_is_kept_and_qualified_not_dropped():
    """Scope is a qualifier. Dropping test dependencies hides real CVEs."""
    found = read(maven, POM, name="pom.xml")
    junit = [o for o in found.observations if "junit-jupiter" in (o.object or "")]
    assert junit
    assert junit[0].qualifier or junit[0].properties


def test_a_truncated_pom_reports_rather_than_crashing():
    found = read(maven, "<project><dependencies>", name="pom.xml")
    assert found.errors or not found.observations


# --- Gradle ---------------------------------------------------------------

GRADLE = """
plugins { id 'java' }
dependencies {
    implementation 'com.squareup.okhttp3:okhttp:4.12.0'
    testImplementation group: 'junit', name: 'junit', version: '4.13.2'
    api "org.slf4j:slf4j-api:2.0.9"
}
"""


def test_the_string_form_of_a_gradle_dependency_is_read():
    found = read(gradle, GRADLE, name="build.gradle")
    assert "pkg:maven/com.squareup.okhttp3/okhttp" in {o.split("@")[0] for o in objects(found)}


def test_the_map_form_of_a_gradle_dependency_is_read():
    """`group:` `name:` `version:` — the form a regex over strings misses."""
    found = read(gradle, GRADLE, name="build.gradle")
    assert any("junit/junit" in o for o in objects(found))


def test_gradle_evidence_is_weaker_than_a_pom_because_a_build_script_is_a_program():
    from slpie.domain.evidence import EvidenceKind

    pom_kinds = maven.DISCOVERERS[0][4]
    gradle_kinds = gradle.DISCOVERERS[0][4]
    assert EvidenceKind.MANIFEST_DECLARED in pom_kinds
    assert EvidenceKind.MANIFEST_DECLARED not in gradle_kinds


# --- NuGet ----------------------------------------------------------------

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
    <PackageReference Include="Serilog"><Version>3.1.1</Version></PackageReference>
  </ItemGroup>
</Project>
"""


def test_both_nuget_version_forms_are_read():
    """Attribute and child-element — the second is easy to miss entirely.

    The version lands on the *edge* as a range, not in the target purl, because
    a declared dependency states a range and only a lockfile states a pin.
    """
    found = read(nuget, CSPROJ, name="app.csproj")
    ranges = {
        observation.object: observation.properties.get("range")
        for observation in found.observations if observation.object
    }
    assert ranges["pkg:nuget/Newtonsoft.Json"] == "13.0.3"    # attribute form
    assert ranges["pkg:nuget/Serilog"] == "3.1.1"             # child-element form


# --- Dockerfile -----------------------------------------------------------

MULTISTAGE = """FROM node:20-alpine AS build
WORKDIR /app
RUN npm ci
FROM build AS runtime
COPY --from=build /app/dist /srv
EXPOSE 8080
"""


def test_a_stage_reference_does_not_become_an_external_image():
    """`FROM build` names an earlier stage, not something on a registry."""
    found = read(dockerfile, MULTISTAGE, name="Dockerfile")
    images = {o for o in objects(found) if o.startswith("pkg:docker/")}
    assert any("node" in image for image in images)
    assert not any(image.endswith("/build") or "docker/build" in image
                   for image in images)


def test_the_base_image_is_cited_at_its_line():
    found = read(dockerfile, MULTISTAGE, name="Dockerfile")
    base = [o for o in found.observations if "node" in (o.object or "")]
    assert base and base[0].evidence.location.line == 1


# --- Compose --------------------------------------------------------------

COMPOSE = """services:
  api:
    image: acme/payments:1.2.3
    ports: ["8080:8080"]
    depends_on: [db]
  db:
    image: postgres:16
"""


def test_compose_services_become_services_and_their_images_dependencies():
    found = read(compose, COMPOSE, name="docker-compose.yaml")
    assert any(s.startswith("urn:slpie:service:") for s in subjects(found))
    assert any("postgres" in o for o in objects(found))


def test_a_service_dependency_between_compose_services_is_recorded():
    found = read(compose, COMPOSE, name="docker-compose.yaml")
    assert any(
        o.kind in ("depends_on", "deploys_to") for o in found.observations
    )


# --- Kubernetes -----------------------------------------------------------

MULTIDOC = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: payments
  namespace: prod
spec:
  template:
    spec:
      containers:
        - name: api
          image: acme/payments:1.2.3
---
apiVersion: v1
kind: Service
metadata:
  name: payments
  namespace: prod
"""


def test_a_multi_document_stream_yields_every_document():
    """One `---` file holding a Deployment and a Service is the normal case."""
    found = read(kubernetes, MULTIDOC, name="deploy.yaml")
    assert any("deployment" in s for s in subjects(found))
    assert any("service" in s for s in subjects(found))


def test_the_scheduled_image_becomes_a_dependency():
    found = read(kubernetes, MULTIDOC, name="deploy.yaml")
    assert any("acme/payments@1.2.3" in o for o in objects(found))


@pytest.mark.parametrize("text", [
    "apiVersion: v2\nname: chart\nversion: 1.0.0\n",     # a Helm chart
    "openapi: 3.0.3\ninfo:\n  title: X\n",               # an OpenAPI document
    "foo: bar\n",                                         # anything at all
])
def test_kubernetes_declines_yaml_that_is_not_kubernetes(text):
    """It claims `*.yaml`, so it sees every YAML in the repository."""
    assert not read(kubernetes, text, name="x.yaml").observations


def test_a_secrets_contents_are_never_read():
    """Recorded as existing, and nothing more."""
    secret = """apiVersion: v1
kind: Secret
metadata:
  name: db-credentials
data:
  password: aHVudGVyMg==
"""
    found = read(kubernetes, secret, name="secret.yaml")
    rendered = str([o.evidence.excerpt for o in found.observations])
    assert "aHVudGVyMg==" not in rendered


# --- Helm -----------------------------------------------------------------

CHART = """apiVersion: v2
name: payments
version: 1.0.0
dependencies:
  - name: postgresql
    version: 12.1.0
    repository: https://charts.bitnami.com/bitnami
"""


def test_a_chart_declares_itself_and_its_subcharts():
    found = read(helm, CHART, name="Chart.yaml")
    assert any("payments" in s for s in subjects(found))
    assert any("postgresql" in o for o in objects(found))


def test_helm_templates_are_not_parsed_as_yaml():
    """They are Go templates and are not valid YAML; pretending otherwise
    produces garbage rather than an error."""
    template = "kind: Deployment\nmetadata:\n  name: {{ .Release.Name }}\n"
    found = read(helm, template, name="templates/deployment.yaml")
    assert not found.observations or found.errors


# --- Terraform ------------------------------------------------------------

TERRAFORM = """
resource "aws_s3_bucket" "artifacts" {
  bucket = "acme-artifacts"
}

module "vpc" {
  source = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"
}

resource "aws_instance" "api" {
  subnet_id = module.vpc.public_subnets[0]
}
"""


def test_hcl_blocks_are_parsed_into_resources_and_modules():
    """HCL is not JSON; it needs a tokeniser rather than a JSON reader."""
    found = read(terraform, TERRAFORM, name="main.tf")
    assert any("aws_s3_bucket" in s for s in subjects(found))
    assert any("vpc" in s for s in subjects(found))


def test_an_interpolation_between_resources_becomes_a_reference():
    found = read(terraform, TERRAFORM, name="main.tf")
    assert any(o.kind == "references" for o in found.observations)


def test_the_json_form_of_terraform_is_read_too():
    document = (
        '{"resource": {"aws_s3_bucket": {"logs": {"bucket": "acme-logs"}}}}'
    )
    found = read(terraform, document, name="main.tf.json")
    assert found.observations or found.errors


# --- OpenAPI --------------------------------------------------------------

OPENAPI = """openapi: 3.0.3
info:
  title: Payments
  version: 1.0.0
servers:
  - url: https://api.acme.com
paths:
  /v1/orders:
    post:
      operationId: createOrder
      responses:
        "201":
          description: created
    get:
      operationId: listOrders
      responses:
        "200":
          description: ok
"""


def test_each_operation_becomes_its_own_api_node():
    found = read(openapi, OPENAPI, name="openapi.yaml")
    apis = {s for s in subjects(found) if "api:rest" in s} | {
        o for o in objects(found) if "api:rest" in o
    }
    assert any("POST" in api for api in apis)
    assert any("GET" in api for api in apis)


def test_the_service_owns_the_operations_it_serves():
    found = read(openapi, OPENAPI, name="openapi.yaml")
    assert any(o.kind == "owns" for o in found.observations)


def test_swagger_two_is_read_as_well_as_openapi_three():
    swagger = """swagger: "2.0"
info:
  title: Legacy
  version: 1.0.0
paths:
  /health:
    get:
      operationId: health
      responses:
        "200":
          description: ok
"""
    assert read(openapi, swagger, name="swagger.yaml").observations


# --- AsyncAPI -------------------------------------------------------------


def test_asyncapi_two_publish_means_the_application_receives():
    """The direction inversion. Getting it wrong reverses every event edge."""
    document = """asyncapi: 2.6.0
info:
  title: Orders
  version: 1.0.0
channels:
  orders/created:
    publish:
      operationId: onOrderCreated
"""
    found = read(asyncapi, document, name="asyncapi.yaml")
    kinds = {o.kind for o in found.observations}
    assert "subscribes" in kinds
    assert "publishes" not in kinds


def test_asyncapi_two_subscribe_means_the_application_sends():
    document = """asyncapi: 2.6.0
info:
  title: Orders
  version: 1.0.0
channels:
  orders/created:
    subscribe:
      operationId: emitOrderCreated
"""
    found = read(asyncapi, document, name="asyncapi.yaml")
    assert "publishes" in {o.kind for o in found.observations}


def test_a_channel_becomes_a_topic():
    document = """asyncapi: 2.6.0
info:
  title: Orders
  version: 1.0.0
channels:
  orders/created:
    publish:
      operationId: onOrderCreated
"""
    found = read(asyncapi, document, name="asyncapi.yaml")
    assert any("topic" in s for s in subjects(found))


# --- registration ---------------------------------------------------------


def test_every_phase_eight_discoverer_is_wired_into_the_scanner():
    """Written but unregistered is the state this phase was frozen in."""
    registry = register_builtins(Registry())
    ids = {plugin.id for plugin in registry}
    for expected in ("slpie.maven.pom", "slpie.gradle.build", "slpie.nuget.project",
                     "slpie.dockerfile", "slpie.compose", "slpie.kubernetes",
                     "slpie.helm.chart", "slpie.terraform",
                     "slpie.openapi", "slpie.asyncapi"):
        assert expected in ids


def test_packaging_is_read_before_the_source_that_uses_it():
    registry = register_builtins(Registry())
    assert (
        registry.get("slpie.maven.pom").manifest.priority
        < registry.get("slpie.javascript").manifest.priority
    )


@pytest.mark.parametrize("module", builtin_modules(), ids=lambda m: m.__name__.split(".")[-1])
def test_every_declared_discoverer_states_the_evidence_it_may_claim(module):
    for entry in module.DISCOVERERS:
        plugin_id, handler, handles, produces, evidence_kinds = entry
        assert plugin_id.startswith("slpie.")
        assert callable(handler)
        assert handles and produces and evidence_kinds


@pytest.mark.parametrize("module", builtin_modules(), ids=lambda m: m.__name__.split(".")[-1])
def test_no_discoverer_crashes_on_an_empty_or_binary_file(module):
    """A scanner meets both, and neither may take the run down."""
    for entry in module.DISCOVERERS:
        for text in ("", "\x00\x01\x02 not text at all", "{"):
            source = Source(uri="file:///repo/whatever", text=text, digest="",
                            element=ELEMENT)
            entry[1](source)      # must not raise
