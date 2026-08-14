"""The connectors shipped in the box.

Fifteen declarations, no implementations. That split is the point: a
`ConnectorSpec` is data describing what a connector needs and grants, so the
catalogue is complete and browsable before a single integration is written, and
writing one later adds no entry here.

It also makes the shop window real. `slpie connectors` lists these whether or
not anyone has signed in, and `Keyring.offer()` turns a sign-in into "here is
what Google unlocks for you" without asking a network anything.

Every entry states its scopes in consent language rather than vendor language —
`"read repository contents and metadata"`, not `"repo"` — because the string
here is what a human is asked to agree to.
"""

from __future__ import annotations

from ..identity.principal import Assurance, AuthMethod
from .spec import ConnectorCatalogue, ConnectorKind, ConnectorSpec, Scope

FEDERATED = (
    AuthMethod.GOOGLE, AuthMethod.APPLE, AuthMethod.MICROSOFT,
    AuthMethod.GITHUB, AuthMethod.OIDC,
)
MACHINE = (AuthMethod.API_KEY, AuthMethod.MTLS)


def builtin_catalogue() -> ConnectorCatalogue:
    """Everything SLPIE knows how to be connected to, as declarations."""
    return ConnectorCatalogue(
        # --- source control ------------------------------------------------
        ConnectorSpec(
            id="github",
            kind=ConnectorKind.SOURCE_CONTROL,
            title="GitHub",
            description="Repositories, pull requests, workflow runs and advisories.",
            methods=(AuthMethod.GITHUB, AuthMethod.API_KEY, AuthMethod.OIDC),
            assurance=Assurance.STANDARD,
            scopes=(
                Scope("repo.read", "read repository contents and metadata"),
                Scope("pr.read", "read pull requests and reviews"),
                Scope("actions.read", "read workflow runs and their logs", required=False),
                Scope("advisories.read", "read security advisories", required=False),
            ),
            capabilities=("scm-read", "lockfile-read", "static-analysis", "pr-read"),
            vendor="GitHub, Inc.",
        ),
        ConnectorSpec(
            id="gitlab",
            kind=ConnectorKind.SOURCE_CONTROL,
            title="GitLab",
            methods=(AuthMethod.OIDC, AuthMethod.API_KEY),
            scopes=(
                Scope("repo.read", "read repository contents and metadata"),
                Scope("mr.read", "read merge requests", required=False),
            ),
            capabilities=("scm-read", "lockfile-read", "static-analysis"),
        ),

        # --- registries ----------------------------------------------------
        ConnectorSpec(
            id="npm",
            kind=ConnectorKind.REGISTRY,
            title="npm registry",
            description="Package metadata and lockfile resolution for JavaScript.",
            methods=(AuthMethod.API_KEY, AuthMethod.OIDC),
            scopes=(Scope("packages.read", "read package metadata and versions"),),
            capabilities=("registry-read", "advisory-match"),
        ),
        ConnectorSpec(
            id="pypi",
            kind=ConnectorKind.REGISTRY,
            title="PyPI",
            methods=(AuthMethod.API_KEY, AuthMethod.OIDC),
            scopes=(Scope("packages.read", "read package metadata and versions"),),
            capabilities=("registry-read", "advisory-match"),
        ),
        ConnectorSpec(
            id="artifactory",
            kind=ConnectorKind.REGISTRY,
            title="JFrog Artifactory",
            description="An internal registry — usually the one that actually matters.",
            methods=(AuthMethod.OIDC, AuthMethod.API_KEY, AuthMethod.MTLS),
            scopes=(
                Scope("repos.read", "read artifact repositories"),
                Scope("builds.read", "read build information", required=False),
            ),
            capabilities=("registry-read", "internal-registry-read"),
        ),

        # --- cloud ---------------------------------------------------------
        ConnectorSpec(
            id="aws",
            kind=ConnectorKind.CLOUD,
            title="Amazon Web Services",
            description="Account inventory, IAM, and deployed infrastructure.",
            methods=(AuthMethod.OIDC, AuthMethod.API_KEY, AuthMethod.MTLS),
            assurance=Assurance.STRONG,
            scopes=(
                Scope("inventory.read", "list deployed resources and their tags"),
                Scope("iam.read", "read roles and policies", required=False),
                Scope("cost.read", "read spend by resource", required=False),
            ),
            capabilities=("cloud-inventory", "iam-read", "cost-read"),
        ),
        ConnectorSpec(
            id="gcp",
            kind=ConnectorKind.CLOUD,
            title="Google Cloud",
            methods=(AuthMethod.GOOGLE, AuthMethod.OIDC, AuthMethod.API_KEY),
            assurance=Assurance.STRONG,
            scopes=(
                Scope("inventory.read", "list deployed resources and their labels"),
                Scope("cost.read", "read spend by resource", required=False),
            ),
            capabilities=("cloud-inventory", "cost-read"),
        ),
        ConnectorSpec(
            id="azure",
            kind=ConnectorKind.CLOUD,
            title="Microsoft Azure",
            methods=(AuthMethod.MICROSOFT, AuthMethod.OIDC, AuthMethod.API_KEY),
            assurance=Assurance.STRONG,
            scopes=(Scope("inventory.read", "list deployed resources"),),
            capabilities=("cloud-inventory",),
        ),

        # --- data ----------------------------------------------------------
        ConnectorSpec(
            id="postgres",
            kind=ConnectorKind.DATABASE,
            title="PostgreSQL",
            description="Schema introspection for data lineage. Never row data.",
            methods=(AuthMethod.API_KEY, AuthMethod.MTLS, AuthMethod.OIDC),
            assurance=Assurance.STRONG,
            scopes=(
                Scope("schema.read", "read table and column definitions"),
                Scope("stats.read", "read table sizes and row estimates", required=False),
            ),
            capabilities=("schema-read", "lineage-read"),
        ),
        ConnectorSpec(
            id="snowflake",
            kind=ConnectorKind.DATABASE,
            title="Snowflake",
            methods=(AuthMethod.OIDC, AuthMethod.API_KEY),
            assurance=Assurance.STRONG,
            scopes=(Scope("schema.read", "read database and schema definitions"),),
            capabilities=("schema-read", "lineage-read"),
        ),

        # --- storage -------------------------------------------------------
        ConnectorSpec(
            id="google-drive",
            kind=ConnectorKind.STORAGE,
            title="Google Drive",
            description="Design documents and specifications, as architecture evidence.",
            methods=(AuthMethod.GOOGLE,),
            scopes=(
                Scope("files.read", "read documents you have shared with SLPIE"),
                Scope("metadata.read", "read file names and folders", required=False),
            ),
            capabilities=("document-read",),
            issuer="https://accounts.google.com",
            vendor="Google LLC",
        ),
        ConnectorSpec(
            id="s3",
            kind=ConnectorKind.STORAGE,
            title="Amazon S3",
            methods=MACHINE + (AuthMethod.OIDC,),
            scopes=(Scope("objects.read", "read objects in the named buckets"),),
            capabilities=("object-read",),
        ),

        # --- delivery and operations ---------------------------------------
        ConnectorSpec(
            id="jira",
            kind=ConnectorKind.ISSUE_TRACKER,
            title="Jira",
            methods=(AuthMethod.OIDC, AuthMethod.API_KEY, AuthMethod.MICROSOFT),
            scopes=(
                Scope("issues.read", "read issues and their links"),
                Scope("projects.read", "read project and component structure", required=False),
            ),
            capabilities=("issue-read", "ownership-read"),
        ),
        ConnectorSpec(
            id="slack",
            kind=ConnectorKind.COMMUNICATION,
            title="Slack",
            description="Delivers findings where people already are.",
            methods=(AuthMethod.OIDC, AuthMethod.API_KEY),
            scopes=(
                Scope("channels.read", "read the channels SLPIE is invited to"),
                Scope("chat.write", "post findings to those channels",
                      required=False, writes=True),
            ),
            capabilities=("notify",),
            read_only=False,
        ),
        ConnectorSpec(
            id="datadog",
            kind=ConnectorKind.OBSERVABILITY,
            title="Datadog",
            description="Runtime traces — the evidence that outranks static analysis.",
            methods=(AuthMethod.API_KEY, AuthMethod.OIDC),
            scopes=(
                Scope("metrics.read", "read service metrics"),
                Scope("traces.read", "read distributed traces", required=False),
            ),
            capabilities=("runtime-trace", "metrics-read"),
        ),

        # --- models --------------------------------------------------------
        ConnectorSpec(
            id="anthropic",
            kind=ConnectorKind.MODEL,
            title="Claude",
            description=(
                "Natural-language console and the validating agent behind "
                "distillation. Optional: the deterministic console works without it."
            ),
            methods=(AuthMethod.API_KEY, AuthMethod.OIDC),
            scopes=(Scope("messages.write", "send prompts and receive completions",
                          writes=True),),
            capabilities=("agent-validate", "conversational-console"),
            vendor="Anthropic",
            read_only=False,
        ),
        ConnectorSpec(
            id="openai",
            kind=ConnectorKind.MODEL,
            title="OpenAI",
            methods=(AuthMethod.API_KEY, AuthMethod.OIDC),
            scopes=(Scope("completions.write", "send prompts and receive completions",
                          writes=True),),
            capabilities=("agent-validate", "conversational-console"),
            read_only=False,
        ),
    )
