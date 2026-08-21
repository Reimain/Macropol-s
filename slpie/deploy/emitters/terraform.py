"""HCL — the infrastructure under the workloads, per cloud.

This is the emitter that writes what the others assume: the database the
compose file points at, the bucket the chart's environment names, the cluster
the manifests are applied to. It stops at the boundary the other emitters start
from, and the two halves meet at variables rather than at guesses.

── One module per cloud, chosen by the manifest ─────────────────────────

`cloud: aws | gcp | azure | onprem` selects the resources. That is a closed set
in the schema for exactly this reason: an unrecognised cloud would emit a
`main.tf` with no resources in it, which `terraform plan` reports as "no changes"
— a green result for a deployment that does not exist.

`onprem` is not an absence. It emits the variables and outputs the other
emitters consume, with nothing provisioned, so an operator running their own
Postgres still gets a file that states the contract.
"""

from __future__ import annotations

from typing import Mapping

from ..manifest import Cloud, Deployment
from ._common import header

NAME = "terraform"

#: The provider each cloud needs, and the version constraint. Pinned to a major:
#: a provider that upgrades itself between `plan` and `apply` is how a reviewed
#: plan stops matching what runs.
PROVIDERS = {
    Cloud.AWS: ("aws", "hashicorp/aws", "~> 5.0"),
    Cloud.GCP: ("google", "hashicorp/google", "~> 5.0"),
    Cloud.AZURE: ("azurerm", "hashicorp/azurerm", "~> 3.0"),
}


def render(deployment: Deployment) -> Mapping[str, str]:
    return {
        "main.tf": _main(deployment),
        "variables.tf": _variables(deployment),
        "outputs.tf": _outputs(deployment),
    }


def _main(deployment: Deployment) -> str:
    lines = header(deployment) + ["", "terraform {", '  required_version = ">= 1.5"']

    provider = PROVIDERS.get(deployment.cloud)
    if provider:
        alias, source, version = provider
        lines += [
            "  required_providers {",
            f"    {alias} = {{",
            f'      source  = "{source}"',
            f'      version = "{version}"',
            "    }",
            "  }",
        ]
    lines += ["}", ""]

    if provider:
        alias, _, _ = provider
        lines += [f'provider "{alias}" {{', "  region = var.region", "}", ""]
        lines += _resources(deployment)
    else:
        lines += [
            "# cloud: onprem — nothing is provisioned here.",
            "#",
            "# The variables and outputs are still emitted, because the compose,",
            "# systemd and kubernetes renders read them. An operator running",
            "# their own Postgres fills these in; the contract is the same one",
            "# the managed clouds satisfy.",
        ]
    return "\n".join(lines) + "\n"


def _resources(deployment: Deployment) -> list[str]:
    lines: list[str] = []
    graph = deployment.persistence.get("graph")
    objects = deployment.persistence.get("objects")

    if graph and graph.engine == "postgres":
        lines += _database(deployment, graph)
    if objects and objects.engine in ("s3", "gcs", "azure-blob"):
        lines += _bucket(deployment, objects)
    return lines


def _database(deployment: Deployment, store) -> list[str]:
    size = _gigabytes(store.size) or 100
    if deployment.cloud is Cloud.AWS:
        return [
            'resource "aws_db_instance" "graph" {',
            f'  identifier        = "slpie-{deployment.environment}-graph"',
            '  engine            = "postgres"',
            f"  allocated_storage = {size}",
            "  instance_class    = var.database_instance_class",
            "  username          = var.database_username",
            "  password          = var.database_password",
            # A snapshot on the way out. The ledger is the source of truth and
            # the graph rebuilds from it, but "rebuildable" and "gone" are not
            # the same afternoon.
            "  skip_final_snapshot = false",
            f'  final_snapshot_identifier = "slpie-{deployment.environment}-final"',
            f"  multi_az          = {str(bool(store.replicas)).lower()}",
            "}",
            "",
        ]
    if deployment.cloud is Cloud.GCP:
        return [
            'resource "google_sql_database_instance" "graph" {',
            f'  name             = "slpie-{deployment.environment}-graph"',
            '  database_version = "POSTGRES_16"',
            "  region           = var.region",
            "  settings {",
            "    tier = var.database_instance_class",
            f"    disk_size = {size}",
            "  }",
            "  deletion_protection = true",
            "}",
            "",
        ]
    return [
        'resource "azurerm_postgresql_flexible_server" "graph" {',
        f'  name                = "slpie-{deployment.environment}-graph"',
        "  resource_group_name = var.resource_group",
        "  location            = var.region",
        f"  storage_mb          = {size * 1024}",
        "  administrator_login = var.database_username",
        "  administrator_password = var.database_password",
        "}",
        "",
    ]


def _bucket(deployment: Deployment, store) -> list[str]:
    name = store.bucket or f"slpie-{deployment.environment}-artifacts"
    if deployment.cloud is Cloud.AWS:
        return [
            'resource "aws_s3_bucket" "objects" {',
            f'  bucket = "{name}"',
            "}",
            "",
            'resource "aws_s3_bucket_public_access_block" "objects" {',
            "  bucket                  = aws_s3_bucket.objects.id",
            "  block_public_acls       = true",
            "  block_public_policy     = true",
            "  ignore_public_acls      = true",
            "  restrict_public_buckets = true",
            "}",
            "",
        ]
    if deployment.cloud is Cloud.GCP:
        return [
            'resource "google_storage_bucket" "objects" {',
            f'  name                        = "{name}"',
            "  location                    = var.region",
            "  uniform_bucket_level_access = true",
            "}",
            "",
        ]
    return [
        'resource "azurerm_storage_container" "objects" {',
        f'  name                 = "{name}"',
        "  storage_account_name = var.storage_account",
        '  container_access_type = "private"',
        "}",
        "",
    ]


def _variables(deployment: Deployment) -> str:
    lines = header(deployment) + [""]
    lines += _variable("region", "string", deployment.regions.primary or "eu-west-1",
                       "where the primary — the ledger's single writer — lives")

    graph = deployment.persistence.get("graph")
    if graph and graph.engine == "postgres":
        lines += _variable("database_instance_class", "string", "db.t3.medium",
                           "the managed database's size")
        lines += _variable("database_username", "string", "slpie", "")
        lines += _variable("database_password", "string", None,
                           "no default, deliberately: a password with a default "
                           "is a password somebody shipped", sensitive=True)
    if deployment.cloud is Cloud.AZURE:
        lines += _variable("resource_group", "string", None, "")
        lines += _variable("storage_account", "string", None, "")
    return "\n".join(lines) + "\n"


def _variable(name: str, kind: str, default, description: str,
              *, sensitive: bool = False) -> list[str]:
    lines = [f'variable "{name}" {{', f"  type = {kind}"]
    if description:
        lines.append(f'  description = "{description}"')
    if default is not None:
        lines.append(f'  default = "{default}"')
    if sensitive:
        lines.append("  sensitive = true")
    lines += ["}", ""]
    return lines


def _outputs(deployment: Deployment) -> str:
    """What the other emitters need, named as outputs rather than assumed."""
    lines = header(deployment) + [""]
    graph = deployment.persistence.get("graph")
    if graph and graph.engine == "postgres" and deployment.cloud is not Cloud.ONPREM:
        lines += [
            'output "database_host" {',
            "  description = \"goes into SLPIE_DATABASE_URL\"",
            "  value = " + _host_expression(deployment),
            "}",
            "",
        ]
    objects = deployment.persistence.get("objects")
    if objects and deployment.cloud is not Cloud.ONPREM:
        lines += [
            'output "object_bucket" {',
            "  description = \"goes into SLPIE_OBJECT_BUCKET\"",
            "  value = " + _bucket_expression(deployment),
            "}",
            "",
        ]
    if not graph and not objects:
        lines.append("# Nothing is provisioned, so nothing is output.")
    return "\n".join(lines) + "\n"


def _host_expression(deployment: Deployment) -> str:
    return {
        Cloud.AWS: "aws_db_instance.graph.address",
        Cloud.GCP: "google_sql_database_instance.graph.private_ip_address",
        Cloud.AZURE: "azurerm_postgresql_flexible_server.graph.fqdn",
    }.get(deployment.cloud, '""')


def _bucket_expression(deployment: Deployment) -> str:
    return {
        Cloud.AWS: "aws_s3_bucket.objects.id",
        Cloud.GCP: "google_storage_bucket.objects.name",
        Cloud.AZURE: "azurerm_storage_container.objects.name",
    }.get(deployment.cloud, '""')


def _gigabytes(size: str) -> int:
    """`200Gi` → 200. Zero when unstated or unreadable."""
    text = size.strip().rstrip("Bb").rstrip("i")
    if text.endswith(("G", "g")):
        text = text[:-1]
    elif text.endswith(("T", "t")):
        try:
            return int(float(text[:-1]) * 1024)
        except ValueError:
            return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def gaps(deployment: Deployment) -> tuple[str, ...]:
    found = []
    if deployment.cloud is Cloud.ONPREM:
        found.append(
            "cloud: onprem — nothing is provisioned. The variables and outputs "
            "state the contract the other renders read; you satisfy it."
        )
    found.append(
        "no compute is provisioned: the cluster or host these workloads run on "
        "is not created here, because a Terraform module that owned both the "
        "cluster and the workloads would destroy the estate to move a replica."
    )
    if deployment.regions.replicas:
        found.append(
            f"the {len(deployment.regions.replicas)} declared replica region(s) "
            f"need their own apply; this module provisions the primary."
        )
    return tuple(found)
