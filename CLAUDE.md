# CLAUDE.md

Orientation for this repository lives in the **`slpie` skill**, not here:

- `.claude/skills/slpie/SKILL.md` — the invariants, the ring rule, the operating
  policy, and the doctrine that has been paid for. Hand-written and stable.
- `.claude/skills/slpie/INDEX.md` — the generated map: every verb, route, screen,
  component, module, test file and plan section, with what connects to what.
- `.claude/skills/slpie/index.json` — the same map for a program to query. Large;
  do not read it into a context window.

Two documents both trying to be the orientation is how they come to disagree, so
this one only points. Start with `SKILL.md`, then ask the map directly:

```bash
slpie context query verb:findings
slpie context query screen:graph
```

Regenerate the map after any change that adds a verb, a route, a screen or a
module:

```bash
slpie context --skill
```
