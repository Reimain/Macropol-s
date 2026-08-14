# Clients — ring 2

Three shells over **one generated API client**. The client is not written here: it
is emitted from the verb registry by `slpie contract --typescript`, so a
capability added on the server becomes a compile error in every shell that has
not handled it, rather than a runtime 404 somebody reports from production.

```
clients/
  web/       the enterprise console — React + TypeScript
  desktop/   Tauri. The portal: many environments at once, each its own kernel
  mobile/    React Native. Read-and-approve rather than full administration
```

## Status, stated plainly

These are **scaffolded, not built**. The generated client and the type
definitions are real code; the shells are structured and their entry points are
written, but no `npm install`, no `cargo build` and no bundling has been run —
this repository's development environment has neither a Node nor a Rust
toolchain, so building them here would be a claim nobody had verified.

What *is* verified, by `tests/test_slpie_contract.py`:

- the generated TypeScript covers every verb, and is byte-identical across runs;
- the contract's route set matches the stdlib server's exactly;
- the type graph the client checks against is the server's own.

So the seam these shells attach to is proven even though the shells are not
compiled. That is the honest division: the contract is tested, the bundling is
not.

## The stdlib UI is not one of these

`slpie/ui/` stays exactly as it is — responsive, installable, and dependent on
nothing. Inside an air-gapped network it is the only one that runs, and it is the
one the tests drive. These three are additive.

## Regenerating the client

```bash
slpie contract --typescript > clients/web/src/slpie-client.ts
cp clients/web/src/slpie-client.ts clients/mobile/src/
cp clients/web/src/slpie-client.ts clients/desktop/src/
slpie contract --openapi > clients/openapi.json
```

Committing the generated file rather than generating at build time is deliberate:
a reviewer can see in the diff that a route changed, which is the whole point of
having a contract.
