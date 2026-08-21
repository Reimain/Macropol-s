"""CI workflows — the same model, applied by the CI system rather than a person.

Three systems, one shape: check out, install, render, plan, and gate the apply
behind something a human did. The gate is the point. A pipeline that applied on
every push would be `slpie deploy apply` with the confirmation removed, and the
confirmation is not a formality — it is the same guard that refuses an
unconfirmed live binding.

So: **plan on every push, apply only on a manual dispatch or a protected
environment.** Expressed in each system's own way, because a workflow that
looked uniform across three CI systems would be uniform and wrong in two of them.
"""

from __future__ import annotations

from typing import Mapping

from ..manifest import Deployment
from ._common import header

NAME = "pipelines"


def render(deployment: Deployment) -> Mapping[str, str]:
    return {
        ".github/workflows/deploy.yml": _github(deployment),
        ".gitlab-ci.yml": _gitlab(deployment),
        "azure-pipelines.yml": _azure(deployment),
    }


def _github(deployment: Deployment) -> str:
    lines = header(deployment) + [
        "",
        f"name: deploy — {deployment.environment}",
        "",
        "on:",
        "  push:",
        "    paths: ['slpie.deployment.yaml']",
        "  workflow_dispatch:",
        "    inputs:",
        "      confirm:",
        "        description: 'Type the environment name to apply'",
        "        required: true",
        "",
        "jobs:",
        "  plan:",
        "    name: plan — the diff, touching nothing",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-python@v5",
        "        with: { python-version: '3.11' }",
        "      - run: pip install -e .",
        "      - run: slpie deploy plan",
        "",
        "  apply:",
        "    name: apply — gated",
        "    needs: plan",
        "    # Two gates, and neither is decoration. The dispatch input is the",
        "    # confirmation the guard requires; the environment is where a",
        "    # reviewer approval is configured.",
        "    if: github.event_name == 'workflow_dispatch'",
        "    runs-on: ubuntu-latest",
        f"    environment: {deployment.environment}",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-python@v5",
        "        with: { python-version: '3.11' }",
        "      - run: pip install -e '.[enterprise]'",
        "      - name: The typed name matches the manifest",
        "        run: |",
        f"          test \"${{{{ inputs.confirm }}}}\" = \"{deployment.environment}\" \\",
        "            || (echo 'the typed environment does not match the manifest' && exit 1)",
        "      - run: slpie deploy apply --confirm",
    ]
    return "\n".join(lines) + "\n"


def _gitlab(deployment: Deployment) -> str:
    lines = header(deployment) + [
        "",
        "stages: [plan, apply]",
        "",
        "default:",
        "  image: python:3.11",
        "  before_script:",
        "    - pip install -e .",
        "",
        "deploy:plan:",
        "  stage: plan",
        "  script:",
        "    - slpie deploy plan",
        "",
        "deploy:apply:",
        "  stage: apply",
        "  # Manual, and that is the confirmation. A GitLab job that ran itself",
        "  # would be an apply nobody agreed to.",
        "  when: manual",
        "  allow_failure: false",
        f"  environment: {deployment.environment}",
        "  before_script:",
        "    - pip install -e '.[enterprise]'",
        "  script:",
        "    - slpie deploy apply --confirm",
    ]
    return "\n".join(lines) + "\n"


def _azure(deployment: Deployment) -> str:
    lines = header(deployment) + [
        "",
        "trigger:",
        "  paths:",
        "    include: ['slpie.deployment.yaml']",
        "",
        "stages:",
        "  - stage: plan",
        "    jobs:",
        "      - job: diff",
        "        pool: { vmImage: ubuntu-latest }",
        "        steps:",
        "          - task: UsePythonVersion@0",
        "            inputs: { versionSpec: '3.11' }",
        "          - script: pip install -e .",
        "          - script: slpie deploy plan",
        "",
        "  - stage: apply",
        "    dependsOn: plan",
        "    jobs:",
        "      # A deployment job, not a plain one: the approval is configured",
        "      # on the environment and Azure will not start without it.",
        "      - deployment: apply",
        f"        environment: {deployment.environment}",
        "        pool: { vmImage: ubuntu-latest }",
        "        strategy:",
        "          runOnce:",
        "            deploy:",
        "              steps:",
        "                - task: UsePythonVersion@0",
        "                  inputs: { versionSpec: '3.11' }",
        "                - script: pip install -e '.[enterprise]'",
        "                - script: slpie deploy apply --confirm",
    ]
    return "\n".join(lines) + "\n"


def gaps(deployment: Deployment) -> tuple[str, ...]:
    return (
        "the approval itself is configured in the CI system, not here: a "
        "protected environment or a required reviewer. A workflow file cannot "
        "grant its own permission, and one that claimed to would be a gate in "
        "name only.",
        "credentials are the pipeline's to hold. Nothing rendered here contains "
        "one, and nothing rendered here should.",
    )
