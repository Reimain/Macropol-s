"""The ring boundary, asserted in both directions.

    ring 0   slpie/               stdlib only, offline, zero dependencies
    ring 1   slpie_enterprise/    kubernetes, boto3 — optional extras

Ring 1 may import ring 0's public API. Ring 0 may not import ring 1 **at all**,
and that is the direction that actually matters: the moment the kernel needs
something from here, `pip install slpie` stops producing a working install and
invariant 4 is gone.

The same `ast` walk `tests/test_slpie_boundaries.py` uses, through the same
shared helper, so a fix to import resolution lands in one place.
"""

from __future__ import annotations

import ast

import pytest

from _walk import REPOSITORY, SLPIE, crossings, imported_roots, modules

ENTERPRISE = REPOSITORY / "slpie_enterprise"


def enterprise_modules():
    return modules(ENTERPRISE)


# --- the direction that matters ---------------------------------------------


def test_the_kernel_never_imports_the_enterprise_ring():
    """The load-bearing one. Ring 0 does not know ring 1 exists."""
    offenders = crossings(SLPIE, "slpie_enterprise")

    assert offenders == [], (
        f"the kernel imports the enterprise ring from {offenders}. "
        f"`pip install slpie` would then need kubernetes and boto3, and "
        f"invariant 4 is gone"
    )


def test_the_kernel_still_installs_with_nothing_but_python():
    """Unchanged by the new ring, and re-asserted here so it cannot drift."""
    import sys

    allowed = set(sys.stdlib_module_names) | {"slpie", "gratimos", "__future__"}
    offenders: dict[str, set[str]] = {}
    for module in modules(SLPIE):
        third_party = imported_roots(module) - allowed
        if third_party:
            offenders[module.relative_to(SLPIE).as_posix()] = third_party

    assert not offenders, f"third-party imports reached the kernel: {offenders}"


# --- what ring 1 is allowed ------------------------------------------------


def test_the_enterprise_ring_imports_only_the_kernels_public_api():
    """No reaching past a package's front door into a private module.

    `slpie.workspace` is the contract; `slpie.workspace.plane._something` is
    not. Importing a private name couples ring 1 to an implementation detail
    that ring 0 is free to change without warning.
    """
    offenders: list[str] = []
    for module in enterprise_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("slpie"):
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    offenders.append(
                        f"{module.relative_to(ENTERPRISE)}: "
                        f"from {node.module} import {alias.name}"
                    )
    assert offenders == [], f"ring 1 reached into a private name: {offenders}"


def test_the_enterprise_ring_may_use_third_party_packages():
    """It is the whole reason this ring exists — asserted so nobody 'fixes' it.

    A well-meaning cleanup that made ring 1 stdlib-only would leave nowhere for
    Kubernetes and S3 to live, and they would drift back into the kernel.
    """
    import sys

    stdlib = set(sys.stdlib_module_names)
    third_party: set[str] = set()
    for module in enterprise_modules():
        third_party |= imported_roots(module) - stdlib - {
            "slpie", "slpie_enterprise", "gratimos", "__future__",
        }

    assert third_party, (
        "ring 1 imports nothing third-party. Either the Kubernetes and S3 "
        "adapters moved, or they were rewritten in stdlib and belong in ring 0"
    )


def test_every_enterprise_module_parses_and_carries_a_docstring():
    for module in enterprise_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        assert ast.get_docstring(tree), f"{module.name} has no module docstring"


# --- the ring implements what the kernel published --------------------------


def test_the_kubernetes_spawner_satisfies_the_kernels_protocol():
    from slpie.workspace import Spawner
    from slpie_enterprise.spawn import KubernetesSpawner

    assert isinstance(KubernetesSpawner(), Spawner)


def test_both_storage_tiers_satisfy_the_kernels_protocol(tmp_path):
    from slpie.workspace import ObjectStore
    from slpie_enterprise.storage import FilesystemStore, S3Store, TieredStore

    work = FilesystemStore(tmp_path / "work")
    shared = S3Store("bucket", client=object())

    assert isinstance(work, ObjectStore)
    assert isinstance(shared, ObjectStore)
    assert isinstance(TieredStore(work=work, shared=shared), ObjectStore)


def test_a_third_party_import_is_deferred_so_one_tier_does_not_take_both_down():
    """`import slpie_enterprise.storage` must work without boto3 installed.

    A module-scope `import boto3` would make the filesystem tier unimportable on
    a machine that has no cloud storage and does not want any.
    """
    import ast as ast_module

    source = (ENTERPRISE / "storage" / "s3.py").read_text(encoding="utf-8")
    tree = ast_module.parse(source)

    module_scope = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast_module.Import)
        for alias in node.names
    }
    assert "boto3" not in module_scope, (
        "boto3 is imported at module scope, so the filesystem tier cannot be "
        "used without it"
    )
