// Generated from the SLPIE verb registry. Do not edit.
// Regenerate with: slpie contract --javascript
// contract 1.0.0

export const CONTRACT = "1.0.0";

export const KINDS = Object.freeze([
  "nothing",
  "any",
  "same",
  "manifest",
  "elements",
  "observations",
  "resolution",
  "enrichments",
  "nodes",
  "edges",
  "graph",
  "impact",
  "requirements",
  "solution",
  "findings",
  "gaps",
  "guidance",
  "judgements",
  "plan",
  "report",
  "text"
]);

export const VERBS = Object.freeze({
  "accept": {
    "group": "guidance",
    "summary": "record that a suggested path was worth taking",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "key",
        "type": "str",
        "help": "the suggestion's key",
        "required": true,
        "choices": []
      },
      {
        "name": "about",
        "type": "str",
        "help": "the subject, if it was stated",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | link | findings | accept --key dlfe"
    ]
  },
  "agent-tools": {
    "group": "incremental",
    "summary": "the capabilities an agent can call, as JSON schemas",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "name",
        "type": "str",
        "help": "one tool, instead of every one",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "agent-tools",
      "agent-tools --name impact_analysis"
    ]
  },
  "ask": {
    "group": "intelligence",
    "summary": "the answer, its reasoning, its gaps, and what to ask next",
    "consumes": "enrichments",
    "produces": "guidance",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "question",
        "type": "str",
        "help": "what you were trying to find out",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | reason | ask",
      "discover . | reason | ask | explain"
    ]
  },
  "attach": {
    "group": "environment",
    "summary": "register every declared element and negotiate capabilities",
    "consumes": "nothing",
    "produces": "elements",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "capabilities",
        "type": "list",
        "help": "capabilities to require",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "attach",
      "attach | count"
    ]
  },
  "audit": {
    "group": "audit",
    "summary": "judge a tree against its stated architecture",
    "consumes": "nothing",
    "produces": "judgements",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "path",
        "type": "path",
        "help": "the tree to judge",
        "required": false,
        "choices": []
      },
      {
        "name": "checks",
        "type": "str",
        "help": "auto or self",
        "required": false,
        "choices": [
          "auto",
          "self"
        ]
      },
      {
        "name": "rule",
        "type": "str",
        "help": "run only this rule",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "audit",
      "audit . | verdicts --only violated"
    ]
  },
  "c4": {
    "group": "artifacts",
    "summary": "C4 views of the system, as Mermaid",
    "consumes": "observations",
    "produces": "report",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "level",
        "type": "str",
        "help": "one level, instead of every one",
        "required": false,
        "choices": [
          "context",
          "container",
          "component",
          "code"
        ]
      },
      {
        "name": "container",
        "type": "str",
        "help": "a container node id, to build C3",
        "required": false,
        "choices": []
      },
      {
        "name": "component",
        "type": "str",
        "help": "a component node id, to build C4",
        "required": false,
        "choices": []
      },
      {
        "name": "out",
        "type": "path",
        "help": "write .mmd files into this directory",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | c4",
      "discover . | c4 --level context"
    ]
  },
  "capture": {
    "group": "capture",
    "summary": "identify files by content and run them past the firewall",
    "consumes": "nothing",
    "produces": "observations",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "path",
        "type": "path",
        "help": "what to capture",
        "required": false,
        "choices": []
      },
      {
        "name": "depth",
        "type": "str",
        "help": "how far to look",
        "required": false,
        "choices": [
          "probe",
          "structure",
          "model",
          "semantic"
        ]
      },
      {
        "name": "limit",
        "type": "int",
        "help": "maximum files",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "capture .",
      "capture . --depth semantic | link"
    ]
  },
  "chain": {
    "group": "capture",
    "summary": "the firewall's rules, in the order they are evaluated",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [],
    "examples": [
      "chain"
    ]
  },
  "changed": {
    "group": "incremental",
    "summary": "what has moved since the last scan, and what it would cost",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "path",
        "type": "path",
        "help": "the tree to compare; defaults to the root this session is bound to",
        "required": false,
        "choices": []
      },
      {
        "name": "baseline",
        "type": "path",
        "help": "where the baseline is kept",
        "required": false,
        "choices": []
      },
      {
        "name": "commit",
        "type": "bool",
        "help": "record the current state as the new baseline; do this only after a rescan succeeded",
        "required": false,
        "choices": []
      },
      {
        "name": "strict",
        "type": "bool",
        "help": "refuse to plan over a tree this pass could not read in full (the default; SLPIE_STRICT=0 turns it off)",
        "required": false,
        "choices": []
      },
      {
        "name": "lenient",
        "type": "bool",
        "help": "report the unread files and plan around them instead of refusing \u2014 for development",
        "required": false,
        "choices": []
      },
      {
        "name": "limit",
        "type": "int",
        "help": "how many files the walk may read before it stops",
        "required": false,
        "choices": []
      },
      {
        "name": "max-bytes",
        "type": "int",
        "help": "the largest file the walk will read",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "changed",
      "changed --path .",
      "changed --lenient"
    ]
  },
  "constraints": {
    "group": "analysis",
    "summary": "solve the version constraints, naming any conflict",
    "consumes": "resolution",
    "produces": "solution",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "ecosystem",
        "type": "str",
        "help": "the version dialect",
        "required": false,
        "choices": []
      },
      {
        "name": "max_steps",
        "type": "int",
        "help": "search budget",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | link | constraints"
    ]
  },
  "context": {
    "group": "context",
    "summary": "the product's own map: what exists, and what connects it",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "query",
        "type": "str",
        "help": "a facet id like `verb:findings`, or text to search for",
        "required": false,
        "choices": []
      },
      {
        "name": "depth",
        "type": "int",
        "help": "how many hops to follow",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "context",
      "context --query verb:findings",
      "context | count"
    ]
  },
  "count": {
    "group": "shaping",
    "summary": "count what is flowing, without changing it",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [],
    "examples": [
      "scan | link | count"
    ]
  },
  "declare": {
    "group": "environment",
    "summary": "build the skeleton graph from the manifest, before reading a file",
    "consumes": "nothing",
    "produces": "elements",
    "mutates": false,
    "source": true,
    "params": [],
    "examples": [
      "declare",
      "declare | count"
    ]
  },
  "discover": {
    "group": "analysis",
    "summary": "read a tree and record what every discoverer finds",
    "consumes": "nothing",
    "produces": "observations",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "path",
        "type": "path",
        "help": "the directory or file to read",
        "required": false,
        "choices": []
      },
      {
        "name": "limit",
        "type": "int",
        "help": "maximum files to consider",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover .",
      "discover ./services/payments | reason | explain"
    ]
  },
  "dismiss": {
    "group": "guidance",
    "summary": "record that a suggested path was not useful here",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "key",
        "type": "str",
        "help": "the suggestion's key",
        "required": true,
        "choices": []
      },
      {
        "name": "reason",
        "type": "str",
        "help": "why, if you want it on the record",
        "required": false,
        "choices": []
      },
      {
        "name": "about",
        "type": "str",
        "help": "the subject, if it was stated",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | link | findings | dismiss --key si"
    ]
  },
  "enterprise": {
    "group": "artifacts",
    "summary": "TOGAF views and the deployment topology",
    "consumes": "observations",
    "produces": "report",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "view",
        "type": "str",
        "help": "one view, instead of every one",
        "required": false,
        "choices": [
          "application",
          "data",
          "technology",
          "standards",
          "topology"
        ]
      },
      {
        "name": "write",
        "type": "bool",
        "help": "generate into ./architecture/",
        "required": false,
        "choices": []
      },
      {
        "name": "out",
        "type": "path",
        "help": "generate here instead",
        "required": false,
        "choices": []
      },
      {
        "name": "policy",
        "type": "str",
        "help": "what to do on a merge conflict",
        "required": false,
        "choices": [
          "raise",
          "mark",
          "local",
          "generated"
        ]
      }
    ],
    "examples": [
      "discover . | enterprise",
      "discover . | enterprise --view application"
    ]
  },
  "explain": {
    "group": "shaping",
    "summary": "render the reasoning, the sources and the gaps behind this",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "remediation",
        "type": "bool",
        "help": "include how to close each gap",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "scan | link | explain",
      "reconcile | findings | explain"
    ]
  },
  "filter": {
    "group": "shaping",
    "summary": "keep only what matches",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "field",
        "type": "str",
        "help": "the field to test",
        "required": false,
        "choices": []
      },
      {
        "name": "contains",
        "type": "str",
        "help": "substring the field must contain",
        "required": false,
        "choices": []
      },
      {
        "name": "equals",
        "type": "str",
        "help": "value the field must equal exactly",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "scan | filter --field kind --equals depends_on"
    ]
  },
  "findings": {
    "group": "analysis",
    "summary": "rank everything wrong that is known so far",
    "consumes": "any",
    "produces": "findings",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "severity",
        "type": "str",
        "help": "keep only this severity",
        "required": false,
        "choices": [
          "critical",
          "high",
          "medium",
          "low",
          "info"
        ]
      }
    ],
    "examples": [
      "discover . | link | findings",
      "discover . | link | constraints | findings --severity high"
    ]
  },
  "fire": {
    "group": "environment",
    "summary": "fire a scripted condition at the simulated world",
    "consumes": "nothing",
    "produces": "report",
    "mutates": true,
    "source": true,
    "params": [
      {
        "name": "scenario",
        "type": "str",
        "help": "which condition to fire",
        "required": true,
        "choices": [
          "boundary-breach",
          "capability-refused",
          "contract-broken",
          "cve",
          "declaration-drift",
          "duplicate-versions",
          "license-change",
          "major-bump",
          "partial-scan",
          "service-dies",
          "shadow-dependency",
          "unmaintained"
        ]
      },
      {
        "name": "package",
        "type": "str",
        "help": "the package the scenario acts on, where it takes one",
        "required": false,
        "choices": []
      },
      {
        "name": "version",
        "type": "str",
        "help": "the version the scenario acts on, where it takes one",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "fire cve --package lodash",
      "fire boundary-breach"
    ]
  },
  "gaps": {
    "group": "environment",
    "summary": "everything the platform currently cannot see",
    "consumes": "nothing",
    "produces": "gaps",
    "mutates": false,
    "source": true,
    "params": [],
    "examples": [
      "gaps",
      "gaps | explain"
    ]
  },
  "govern": {
    "group": "governance",
    "summary": "run every governance rule over what was scanned",
    "consumes": "observations",
    "produces": "findings",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "path",
        "type": "path",
        "help": "where to read source for secret scanning",
        "required": false,
        "choices": []
      },
      {
        "name": "severity",
        "type": "str",
        "help": "keep only this severity",
        "required": false,
        "choices": [
          "critical",
          "high",
          "medium",
          "low",
          "info"
        ]
      },
      {
        "name": "family",
        "type": "str",
        "help": "comma-separated families, instead of all",
        "required": false,
        "choices": []
      },
      {
        "name": "advisories",
        "type": "path",
        "help": "an OSV document or directory of them",
        "required": false,
        "choices": []
      },
      {
        "name": "popular",
        "type": "path",
        "help": "a JSON popularity list, for typosquats",
        "required": false,
        "choices": []
      },
      {
        "name": "distribution",
        "type": "str",
        "help": "how this software reaches users",
        "required": false,
        "choices": [
          "internal_only",
          "network_service",
          "distributed_binary",
          "distributed_source",
          "embedded"
        ]
      },
      {
        "name": "linkage",
        "type": "str",
        "help": "how dependencies are incorporated",
        "required": false,
        "choices": [
          "dynamic",
          "static",
          "separate_process",
          "unmodified"
        ]
      },
      {
        "name": "project_license",
        "type": "str",
        "help": "this project's own licence",
        "required": false,
        "choices": []
      },
      {
        "name": "no_sources",
        "type": "bool",
        "help": "skip secret scanning",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | govern",
      "discover . | govern --severity critical | explain"
    ]
  },
  "graph": {
    "group": "environment",
    "summary": "read nodes from the graph",
    "consumes": "nothing",
    "produces": "nodes",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "limit",
        "type": "int",
        "help": "maximum nodes",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "graph | count",
      "graph --limit 20 | impact"
    ]
  },
  "head": {
    "group": "shaping",
    "summary": "keep the first N",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "count",
        "type": "int",
        "help": "how many to keep",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "scan | head --count 5"
    ]
  },
  "history": {
    "group": "dispatch",
    "summary": "recent commits, from git rather than from a reimplementation",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "count",
        "type": "int",
        "help": "how many commits",
        "required": false,
        "choices": []
      },
      {
        "name": "path",
        "type": "path",
        "help": "the repository",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "history --count 5",
      "history | json"
    ]
  },
  "impact": {
    "group": "environment",
    "summary": "what depends on this, and how confidently",
    "consumes": "nodes",
    "produces": "impact",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "id",
        "type": "str",
        "help": "a node id, instead of piping nodes in",
        "required": false,
        "choices": []
      },
      {
        "name": "depth",
        "type": "int",
        "help": "how far to walk",
        "required": false,
        "choices": []
      },
      {
        "name": "min_confidence",
        "type": "float",
        "help": "ignore weaker edges",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "search lodash | impact",
      "graph | impact --min_confidence 0.8"
    ]
  },
  "json": {
    "group": "shaping",
    "summary": "serialise the flow, provenance included",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "limit",
        "type": "int",
        "help": "maximum items to render",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "scan | link | json"
    ]
  },
  "lexicon": {
    "group": "context",
    "summary": "the words this context uses for the platform's nouns",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "profile",
        "type": "str",
        "help": "a profile under .slpie/lexicon/; omit for the default",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "lexicon",
      "lexicon --profile platform-engineering",
      "lexicon | count"
    ]
  },
  "link": {
    "group": "analysis",
    "summary": "merge observations onto identities and join across files",
    "consumes": "observations",
    "produces": "resolution",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "resolve_only",
        "type": "bool",
        "help": "skip the cross-file linkers",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | link",
      "discover . | link | findings"
    ]
  },
  "options": {
    "group": "intelligence",
    "summary": "every upgrade available, with the cost of each",
    "consumes": "enrichments",
    "produces": "report",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "kind",
        "type": "str",
        "help": "one family of option",
        "required": false,
        "choices": [
          "safe_upgrade",
          "upgrade_option",
          "duplicate_versions",
          "unconstrained_range"
        ]
      },
      {
        "name": "safe",
        "type": "bool",
        "help": "only what breaks nothing",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | reason | options",
      "discover . | reason | options --safe"
    ]
  },
  "quarantine": {
    "group": "capture",
    "summary": "what was held rather than admitted, and why",
    "consumes": "any",
    "produces": "report",
    "mutates": false,
    "source": false,
    "params": [],
    "examples": [
      "capture . | quarantine"
    ]
  },
  "radius": {
    "group": "intelligence",
    "summary": "what depends on what, without needing a database",
    "consumes": "enrichments",
    "produces": "impact",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "package",
        "type": "str",
        "help": "keep only radii whose subject matches",
        "required": false,
        "choices": []
      },
      {
        "name": "min_size",
        "type": "int",
        "help": "ignore anything smaller",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | reason | radius",
      "discover . | reason | radius --min_size 3 | head --count 5"
    ]
  },
  "reason": {
    "group": "analysis",
    "summary": "run the reasoning layers over what was observed",
    "consumes": "observations",
    "produces": "enrichments",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "element",
        "type": "str",
        "help": "the element these belong to",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | reason",
      "discover . | reason | explain"
    ]
  },
  "reconcile": {
    "group": "environment",
    "summary": "compare what was declared against what was observed",
    "consumes": "nothing",
    "produces": "findings",
    "mutates": false,
    "source": true,
    "params": [],
    "examples": [
      "reconcile",
      "reconcile | sort --field severity --desc"
    ]
  },
  "risk": {
    "group": "artifacts",
    "summary": "findings aggregated into a ranked risk register",
    "consumes": "findings",
    "produces": "report",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "limit",
        "type": "int",
        "help": "how many to show",
        "required": false,
        "choices": []
      },
      {
        "name": "markdown",
        "type": "bool",
        "help": "render as markdown",
        "required": false,
        "choices": []
      },
      {
        "name": "out",
        "type": "path",
        "help": "write the markdown report here",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | govern | risk",
      "discover . | govern | risk --markdown"
    ]
  },
  "rivals": {
    "group": "rivals",
    "summary": "what else is on the market, and where nobody serves the buyer",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "gaps",
        "type": "bool",
        "help": "the white space, ranked, instead of the comparison table",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "rivals",
      "rivals --gaps",
      "rivals --gaps | json"
    ]
  },
  "routine": {
    "group": "guidance",
    "summary": "claim what you did as a short key, or list what you claimed",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "action",
        "type": "str",
        "help": "list, claim or forget",
        "required": false,
        "choices": [
          "list",
          "claim",
          "forget"
        ]
      },
      {
        "name": "name",
        "type": "str",
        "help": "what to call it",
        "required": false,
        "choices": []
      },
      {
        "name": "pipeline",
        "type": "str",
        "help": "the composition; omit to claim the session",
        "required": false,
        "choices": []
      },
      {
        "name": "key",
        "type": "str",
        "help": "a specific key, instead of a minted one",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "routine",
      "routine --action claim --name release-check --pipeline 'discover . | link | findings'"
    ]
  },
  "rules": {
    "group": "governance",
    "summary": "what this build checks, and each rule's fingerprint",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "tag",
        "type": "str",
        "help": "only rules carrying this tag",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "rules",
      "rules --tag license"
    ]
  },
  "sbom": {
    "group": "artifacts",
    "summary": "a bill of materials, in CycloneDX or SPDX",
    "consumes": "observations",
    "produces": "report",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "format",
        "type": "str",
        "help": "which standard",
        "required": false,
        "choices": [
          "cyclonedx",
          "spdx"
        ]
      },
      {
        "name": "out",
        "type": "path",
        "help": "write the document here",
        "required": false,
        "choices": []
      },
      {
        "name": "subject",
        "type": "str",
        "help": "what this SBOM is about",
        "required": false,
        "choices": []
      },
      {
        "name": "subject_version",
        "type": "str",
        "help": "its version",
        "required": false,
        "choices": []
      },
      {
        "name": "timestamp",
        "type": "int",
        "help": "document time, epoch seconds",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | sbom",
      "discover . | sbom --format spdx"
    ]
  },
  "scan": {
    "group": "environment",
    "summary": "read every attached element and record what is found",
    "consumes": "nothing",
    "produces": "observations",
    "mutates": false,
    "source": true,
    "params": [],
    "examples": [
      "scan",
      "scan | link | findings --severity high"
    ]
  },
  "search": {
    "group": "environment",
    "summary": "find nodes by name",
    "consumes": "nothing",
    "produces": "nodes",
    "mutates": false,
    "source": true,
    "params": [
      {
        "name": "query",
        "type": "str",
        "help": "what to look for",
        "required": true,
        "choices": []
      },
      {
        "name": "limit",
        "type": "int",
        "help": "maximum results",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "search redis",
      "search redis | impact | explain"
    ]
  },
  "simulate": {
    "group": "environment",
    "summary": "materialise the declared world as real files on disk",
    "consumes": "nothing",
    "produces": "elements",
    "mutates": true,
    "source": true,
    "params": [
      {
        "name": "at",
        "type": "str",
        "help": "where to materialise it; a temp directory is used when this is omitted",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "simulate",
      "simulate --at ./world"
    ]
  },
  "sort": {
    "group": "shaping",
    "summary": "order by a field",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "field",
        "type": "str",
        "help": "the field to order by",
        "required": false,
        "choices": []
      },
      {
        "name": "desc",
        "type": "bool",
        "help": "highest first",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "findings | sort --field severity --desc"
    ]
  },
  "status": {
    "group": "environment",
    "summary": "the environment's current state",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [],
    "examples": [
      "status"
    ]
  },
  "suggest": {
    "group": "guidance",
    "summary": "what to look at next, and why",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "about",
        "type": "str",
        "help": "the node, finding or term you are stuck on",
        "required": false,
        "choices": []
      },
      {
        "name": "kind",
        "type": "str",
        "help": "the touch kind, when the flow does not say",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | link | findings | suggest",
      "discover . | link | suggest --about lockfile"
    ]
  },
  "target": {
    "group": "environment",
    "summary": "bind the environment to simulated or live",
    "consumes": "nothing",
    "produces": "report",
    "mutates": true,
    "source": true,
    "params": [
      {
        "name": "to",
        "type": "str",
        "help": "simulated or live",
        "required": true,
        "choices": [
          "simulated",
          "live"
        ]
      }
    ],
    "examples": [
      "target --to live"
    ]
  },
  "tool": {
    "group": "dispatch",
    "summary": "pipe the flow through a registered external tool",
    "consumes": "any",
    "produces": "text",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "name",
        "type": "str",
        "help": "the registered tool",
        "required": true,
        "choices": []
      },
      {
        "name": "args",
        "type": "str",
        "help": "arguments, passed as argv",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "discover . | json | tool --name jq --args .kind"
    ]
  },
  "tools": {
    "group": "dispatch",
    "summary": "which external tools are installed, and how reproducible each is",
    "consumes": "nothing",
    "produces": "report",
    "mutates": false,
    "source": true,
    "params": [],
    "examples": [
      "tools",
      "tools | json"
    ]
  },
  "unique": {
    "group": "shaping",
    "summary": "drop duplicates",
    "consumes": "any",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "field",
        "type": "str",
        "help": "the field that identifies a duplicate",
        "required": false,
        "choices": []
      }
    ],
    "examples": [
      "scan | unique --field subject"
    ]
  },
  "verdicts": {
    "group": "audit",
    "summary": "keep only judgements with one verdict",
    "consumes": "judgements",
    "produces": "same",
    "mutates": false,
    "source": false,
    "params": [
      {
        "name": "only",
        "type": "str",
        "help": "which verdict",
        "required": false,
        "choices": [
          "upheld",
          "violated",
          "indeterminate",
          "inapplicable"
        ]
      }
    ],
    "examples": [
      "audit | verdicts --only indeterminate"
    ]
  }
});

export const GROUPS = Object.freeze({
  "analysis": [
    "constraints",
    "discover",
    "findings",
    "link",
    "reason"
  ],
  "artifacts": [
    "c4",
    "enterprise",
    "risk",
    "sbom"
  ],
  "audit": [
    "audit",
    "verdicts"
  ],
  "capture": [
    "capture",
    "chain",
    "quarantine"
  ],
  "context": [
    "context",
    "lexicon"
  ],
  "dispatch": [
    "history",
    "tool",
    "tools"
  ],
  "environment": [
    "attach",
    "declare",
    "fire",
    "gaps",
    "graph",
    "impact",
    "reconcile",
    "scan",
    "search",
    "simulate",
    "status",
    "target"
  ],
  "governance": [
    "govern",
    "rules"
  ],
  "guidance": [
    "accept",
    "dismiss",
    "routine",
    "suggest"
  ],
  "incremental": [
    "agent-tools",
    "changed"
  ],
  "intelligence": [
    "ask",
    "options",
    "radius"
  ],
  "rivals": [
    "rivals"
  ],
  "shaping": [
    "count",
    "explain",
    "filter",
    "head",
    "json",
    "sort",
    "unique"
  ]
});

export const SCREENS = Object.freeze([
  {
    "key": "console",
    "path": "/",
    "title": "Console",
    "section": "console",
    "reads": [
      "GET /api/status",
      "GET /api/manifest",
      "GET /api/stream"
    ],
    "verbs": [
      "ask",
      "options",
      "suggest",
      "accept",
      "dismiss"
    ],
    "events": [
      "*"
    ],
    "action": "intelligence.ask",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Ask about this environment and get the answer with the reasoning that produced it and the gaps that limit it.",
    "authored": true,
    "blocks": []
  },
  {
    "key": "compose",
    "path": "/compose",
    "title": "Compose",
    "section": "operate",
    "reads": [
      "GET /api/verbs"
    ],
    "verbs": [
      "routine"
    ],
    "events": [],
    "action": "platform.discover",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Build a pipeline from typed verbs. Invalid compositions are refused before anything runs.",
    "authored": true,
    "blocks": []
  },
  {
    "key": "findings",
    "path": "/findings/:severity?",
    "title": "Findings",
    "section": "operate",
    "reads": [
      "GET /api/findings"
    ],
    "verbs": [
      "findings",
      "govern",
      "rules"
    ],
    "events": [
      "finding_raised",
      "constraint_violated"
    ],
    "action": "analysis.findings",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Everything the rules raised, ranked by severity, each with its evidence and a remediation.",
    "authored": true,
    "blocks": []
  },
  {
    "key": "graph",
    "path": "/graph",
    "title": "Graph",
    "section": "operate",
    "reads": [
      "GET /api/graph"
    ],
    "verbs": [
      "graph",
      "search"
    ],
    "events": [
      "node_asserted",
      "edge_asserted",
      "node_retired"
    ],
    "action": "environment.graph",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Nodes and edges as the platform observed them, shaded by the confidence of the evidence behind each one.",
    "authored": true,
    "blocks": []
  },
  {
    "key": "cycles",
    "path": "/cycles",
    "title": "Cycles",
    "section": "operate",
    "reads": [
      "GET /api/cycles"
    ],
    "verbs": [],
    "events": [],
    "action": "environment.graph",
    "resource": "*",
    "crumbs": [
      "graph"
    ],
    "parent": "graph",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/cycles",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "impact",
    "path": "/impact/:id",
    "title": "Impact",
    "section": "operate",
    "reads": [
      "GET /api/impact"
    ],
    "verbs": [
      "impact",
      "radius"
    ],
    "events": [],
    "action": "environment.impact",
    "resource": "*",
    "crumbs": [
      "graph",
      "node"
    ],
    "parent": "graph",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/impact",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "node",
    "path": "/node/:id",
    "title": "Node",
    "section": "operate",
    "reads": [
      "GET /api/node"
    ],
    "verbs": [],
    "events": [
      "node_asserted",
      "node_retired"
    ],
    "action": "environment.graph",
    "resource": "*",
    "crumbs": [
      "graph"
    ],
    "parent": "graph",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/node",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "ledger",
    "path": "/ledger",
    "title": "Ledger",
    "section": "operate",
    "reads": [
      "GET /api/integrity",
      "GET /api/projections",
      "GET /api/stream/status"
    ],
    "verbs": [],
    "events": [],
    "action": "platform.discover",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "The append-only record every answer is derived from: chain integrity, projection lag, and the live feed's own health.",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/integrity",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      },
      {
        "component": "auto",
        "source": "GET /api/projections",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      },
      {
        "component": "auto",
        "source": "GET /api/stream/status",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "history",
    "path": "/history/:subject?",
    "title": "History",
    "section": "operate",
    "reads": [
      "GET /api/history",
      "GET /api/causation"
    ],
    "verbs": [
      "history"
    ],
    "events": [],
    "action": "dispatch.history",
    "resource": "*",
    "crumbs": [
      "ledger"
    ],
    "parent": "ledger",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/history",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      },
      {
        "component": "auto",
        "source": "GET /api/causation",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "simulator",
    "path": "/simulator",
    "title": "Simulator",
    "section": "operate",
    "reads": [
      "GET /api/scenarios"
    ],
    "verbs": [
      "simulate",
      "fire",
      "target"
    ],
    "events": [
      "scenario_fired",
      "target_changed"
    ],
    "action": "environment.target",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Materialise the declared world as real files, fire a scenario at it, and watch the platform react.",
    "authored": false,
    "blocks": [
      {
        "component": "grid",
        "source": "GET /api/scenarios",
        "select": "scenarios",
        "title": "Scenarios",
        "columns": [
          {
            "key": "",
            "label": "Scenario",
            "align": "",
            "density": "",
            "format": "mono",
            "link": ""
          }
        ],
        "options": {}
      },
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "Run",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "station",
    "path": "/station",
    "title": "Environment",
    "section": "operate",
    "reads": [
      "GET /api/station"
    ],
    "verbs": [
      "attach",
      "gaps",
      "status"
    ],
    "events": [
      "element_attached",
      "capability_refused"
    ],
    "action": "environment.attach",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "What is attached, which capabilities each element granted or refused, and the gaps those refusals put on every answer.",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/station",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "reconcile",
    "path": "/reconcile",
    "title": "Reconciliation",
    "section": "operate",
    "reads": [
      "GET /api/reconcile"
    ],
    "verbs": [
      "reconcile",
      "declare"
    ],
    "events": [
      "contradiction_found"
    ],
    "action": "environment.reconcile",
    "resource": "*",
    "crumbs": [
      "station"
    ],
    "parent": "station",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/reconcile",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "verbs",
    "path": "/verbs",
    "title": "Verbs",
    "section": "build",
    "reads": [
      "GET /api/verbs"
    ],
    "verbs": [],
    "events": [],
    "action": "platform.discover",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Every capability this build has, as a typed verb. Each one is reachable from the CLI, the API and a pipeline.",
    "authored": true,
    "blocks": []
  },
  {
    "key": "group-analysis",
    "path": "/inspect/analysis",
    "title": "Analysis",
    "section": "build",
    "reads": [],
    "verbs": [
      "constraints",
      "discover",
      "link",
      "reason"
    ],
    "events": [],
    "action": "analysis.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-artifacts",
    "path": "/inspect/artifacts",
    "title": "Artifacts",
    "section": "build",
    "reads": [],
    "verbs": [
      "c4",
      "enterprise",
      "risk",
      "sbom"
    ],
    "events": [],
    "action": "artifacts.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-audit",
    "path": "/inspect/audit",
    "title": "Audit",
    "section": "build",
    "reads": [],
    "verbs": [
      "audit",
      "verdicts"
    ],
    "events": [],
    "action": "audit.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-capture",
    "path": "/inspect/capture",
    "title": "Capture",
    "section": "build",
    "reads": [],
    "verbs": [
      "capture",
      "chain",
      "quarantine"
    ],
    "events": [],
    "action": "capture.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-context",
    "path": "/inspect/context",
    "title": "Context",
    "section": "build",
    "reads": [],
    "verbs": [
      "context",
      "lexicon"
    ],
    "events": [],
    "action": "context.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-dispatch",
    "path": "/inspect/dispatch",
    "title": "Dispatch",
    "section": "build",
    "reads": [],
    "verbs": [
      "tool",
      "tools"
    ],
    "events": [],
    "action": "dispatch.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-incremental",
    "path": "/inspect/incremental",
    "title": "Incremental",
    "section": "build",
    "reads": [],
    "verbs": [
      "agent-tools"
    ],
    "events": [],
    "action": "incremental.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-rivals",
    "path": "/inspect/rivals",
    "title": "Rivals",
    "section": "build",
    "reads": [],
    "verbs": [
      "rivals"
    ],
    "events": [],
    "action": "rivals.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "group-shaping",
    "path": "/inspect/shaping",
    "title": "Shaping",
    "section": "build",
    "reads": [],
    "verbs": [
      "count",
      "explain",
      "filter",
      "head",
      "json",
      "sort",
      "unique"
    ],
    "events": [],
    "action": "shaping.*",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "runner",
        "source": "",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "route-lexicon",
    "path": "/inspect/lexicon",
    "title": "Lexicon",
    "section": "build",
    "reads": [
      "GET /api/lexicon"
    ],
    "verbs": [],
    "events": [],
    "action": "platform.discover",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/lexicon",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "route-screens",
    "path": "/inspect/screens",
    "title": "Screens",
    "section": "build",
    "reads": [
      "GET /api/screens"
    ],
    "verbs": [],
    "events": [],
    "action": "platform.discover",
    "resource": "*",
    "crumbs": [
      "verbs"
    ],
    "parent": "verbs",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/screens",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "catalog",
    "path": "/catalog/:tenant?/:realm?/:dataset?/:object?",
    "title": "Catalog",
    "section": "catalog",
    "reads": [
      "GET /api/search",
      "GET /api/admin/datasets"
    ],
    "verbs": [
      "scan",
      "changed"
    ],
    "events": [
      "node_asserted",
      "node_retired"
    ],
    "action": "dataset.read",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Tenants, realms, datasets and objects \u2014 everything the platform has catalogued, with its lineage.",
    "authored": true,
    "blocks": []
  },
  {
    "key": "gateway",
    "path": "/gateway",
    "title": "Gateway",
    "section": "api",
    "reads": [
      "GET /api/routes",
      "GET /api/apim/gateway"
    ],
    "verbs": [],
    "events": [],
    "action": "apim.gateway.read",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "The live route table and the policy chain in front of it \u2014 which rule admitted or refused each call, and why.",
    "authored": false,
    "blocks": [
      {
        "component": "metrics",
        "source": "GET /api/apim/gateway",
        "select": "",
        "title": "Gateway",
        "columns": [],
        "options": {}
      },
      {
        "component": "grid",
        "source": "GET /api/routes",
        "select": "routes",
        "title": "Route table",
        "columns": [
          {
            "key": "",
            "label": "Route",
            "align": "",
            "density": "",
            "format": "mono",
            "link": ""
          }
        ],
        "options": {}
      }
    ]
  },
  {
    "key": "analytics",
    "path": "/analytics",
    "title": "Analytics",
    "section": "api",
    "reads": [
      "GET /api/apim/analytics"
    ],
    "verbs": [],
    "events": [],
    "action": "apim.analytics.read",
    "resource": "*",
    "crumbs": [
      "gateway"
    ],
    "parent": "gateway",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/apim/analytics",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "throttling",
    "path": "/throttling",
    "title": "Throttling",
    "section": "api",
    "reads": [
      "GET /api/apim/throttles"
    ],
    "verbs": [],
    "events": [],
    "action": "apim.throttles.read",
    "resource": "*",
    "crumbs": [
      "gateway"
    ],
    "parent": "gateway",
    "summary": "",
    "authored": false,
    "blocks": [
      {
        "component": "grid",
        "source": "GET /api/apim/throttles",
        "select": "tiers",
        "title": "Tiers",
        "columns": [
          {
            "key": "name",
            "label": "Tier",
            "align": "",
            "density": "",
            "format": "",
            "link": ""
          },
          {
            "key": "requests",
            "label": "Requests",
            "align": "right",
            "density": "",
            "format": "count",
            "link": ""
          },
          {
            "key": "window_seconds",
            "label": "Window",
            "align": "right",
            "density": "",
            "format": "count",
            "link": ""
          },
          {
            "key": "burst",
            "label": "Burst",
            "align": "right",
            "density": "dense",
            "format": "count",
            "link": ""
          },
          {
            "key": "applies_to",
            "label": "Applies to",
            "align": "",
            "density": "dense",
            "format": "",
            "link": ""
          },
          {
            "key": "description",
            "label": "What it is for",
            "align": "",
            "density": "",
            "format": "",
            "link": ""
          }
        ],
        "options": {}
      },
      {
        "component": "metrics",
        "source": "GET /api/apim/throttles",
        "select": "",
        "title": "Right now",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "keys",
    "path": "/apps/:application?",
    "title": "Applications and keys",
    "section": "api",
    "reads": [
      "GET /api/apim/subscriptions"
    ],
    "verbs": [],
    "events": [],
    "action": "apim.subscriptions.read",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Applications, their subscriptions, and the credentials issued to them.",
    "authored": false,
    "blocks": [
      {
        "component": "auto",
        "source": "GET /api/apim/subscriptions",
        "select": "",
        "title": "",
        "columns": [],
        "options": {}
      }
    ]
  },
  {
    "key": "portal",
    "path": "/portal/:api?",
    "title": "Developer portal",
    "section": "api",
    "reads": [
      "GET /api/manual",
      "GET /api/contract",
      "GET /api/apim/apis"
    ],
    "verbs": [],
    "events": [],
    "action": "platform.discover",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "The APIs this platform publishes, their operations, and a console to try them.",
    "authored": false,
    "blocks": [
      {
        "component": "grid",
        "source": "GET /api/apim/apis",
        "select": "apis",
        "title": "Published APIs",
        "columns": [
          {
            "key": "name",
            "label": "API",
            "align": "",
            "density": "",
            "format": "",
            "link": ""
          },
          {
            "key": "api_id",
            "label": "Id",
            "align": "",
            "density": "dense",
            "format": "mono",
            "link": ""
          },
          {
            "key": "version",
            "label": "Version",
            "align": "",
            "density": "",
            "format": "mono",
            "link": ""
          },
          {
            "key": "visibility",
            "label": "Visibility",
            "align": "",
            "density": "",
            "format": "pill",
            "link": ""
          },
          {
            "key": "default_throttle",
            "label": "Throttle",
            "align": "",
            "density": "dense",
            "format": "",
            "link": ""
          },
          {
            "key": "operations",
            "label": "Operations",
            "align": "right",
            "density": "",
            "format": "count",
            "link": ""
          }
        ],
        "options": {}
      }
    ]
  },
  {
    "key": "workspaces",
    "path": "/admin/workspaces/:id?",
    "title": "Workspaces",
    "section": "admin",
    "reads": [
      "GET /api/admin/workspaces",
      "GET /api/admin/quota"
    ],
    "verbs": [],
    "events": [],
    "action": "workspace.create",
    "resource": "*",
    "crumbs": [],
    "parent": "",
    "summary": "Tenancy, quotas and headroom, and the grants that decide who may read which dataset.",
    "authored": true,
    "blocks": []
  }
]);

/** The platform's own words, baked so the first frame paints in them.
  * `core/lexicon.js` swaps in a context's vocabulary from
  * `GET /api/lexicon` once the caller is known — but a console must
  * render correctly before that round trip, and offline it never
  * happens at all. */
export const LEXICON = Object.freeze({
  "agent": {
    "word": "agent",
    "plural": "agents",
    "gloss": "the platform's capabilities, reachable by a model."
  },
  "apim": {
    "word": "apim",
    "plural": "apims",
    "gloss": "API management, WSO2-shaped, in ring 0."
  },
  "artifacts": {
    "word": "artifacts",
    "plural": "artifacts",
    "gloss": "SBOM, C4 views, and architecture as importable code."
  },
  "audit": {
    "word": "audit",
    "plural": "audits",
    "gloss": "a deterministic, AST-queryable graph knowledge judge."
  },
  "binding": {
    "word": "binding",
    "plural": "bindings",
    "gloss": "the only code that knows simulated from live."
  },
  "capture": {
    "word": "capture",
    "plural": "captures",
    "gloss": "Format intelligence and the capture firewall."
  },
  "compose": {
    "word": "compose",
    "plural": "composes",
    "gloss": "the philosophy, and the one registry every surface reads."
  },
  "connectors": {
    "word": "connectors",
    "plural": "connectors",
    "gloss": "Connectors that activate when authentication arrives."
  },
  "context": {
    "word": "context",
    "plural": "contexts",
    "gloss": "the context spine: metadata that connects the product to itself."
  },
  "core": {
    "word": "core",
    "plural": "cores",
    "gloss": "commands in, events to the ledger, projections out."
  },
  "demo": {
    "word": "demo",
    "plural": "demos",
    "gloss": "one script, projected to a terminal or to screens."
  },
  "discovery": {
    "word": "discovery",
    "plural": "discoveries",
    "gloss": "reading sources and reporting what they say, with citations."
  },
  "dispatch": {
    "word": "dispatch",
    "plural": "dispatches",
    "gloss": "the kernel does not reimplement the device."
  },
  "domain": {
    "word": "domain",
    "plural": "domains",
    "gloss": "the vocabulary every other layer is written in."
  },
  "edge": {
    "word": "edge",
    "plural": "edges",
    "gloss": "the relationships, and the platform's central invariant."
  },
  "enterprise": {
    "word": "enterprise",
    "plural": "enterprises",
    "gloss": "views over the graph, emitted as code."
  },
  "environment": {
    "word": "environment",
    "plural": "environments",
    "gloss": "The declare-first entry point."
  },
  "evidence": {
    "word": "evidence",
    "plural": "evidences",
    "gloss": "Evidence, and the confidence derived from it."
  },
  "finding": {
    "word": "finding",
    "plural": "findings",
    "gloss": "what governance produces, and gaps \u2014 what it admits it cannot see."
  },
  "gap.access_denied": {
    "word": "access denied",
    "plural": "access denieds",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.capability_refused": {
    "word": "capability refused",
    "plural": "capability refuseds",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.low_confidence": {
    "word": "low confidence",
    "plural": "low confidences",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.not_attached": {
    "word": "not attached",
    "plural": "not attacheds",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.not_declared": {
    "word": "not declared",
    "plural": "not declareds",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.not_implemented": {
    "word": "not implemented",
    "plural": "not implementeds",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.parse_failure": {
    "word": "parse failure",
    "plural": "parse failures",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.plugin_quarantined": {
    "word": "plugin quarantined",
    "plural": "plugin quarantineds",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.plugin_unavailable": {
    "word": "plugin unavailable",
    "plural": "plugin unavailables",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.simulated_only": {
    "word": "simulated only",
    "plural": "simulated onlies",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.stale_heartbeat": {
    "word": "stale heartbeat",
    "plural": "stale heartbeats",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "gap.unresolved_dependency": {
    "word": "unresolved dependency",
    "plural": "unresolved dependencies",
    "gloss": "Something the platform could not see, and what it cost."
  },
  "governance": {
    "word": "governance",
    "plural": "governances",
    "gloss": "many findings, never one verdict."
  },
  "graph": {
    "word": "graph",
    "plural": "graphs",
    "gloss": "a bitemporal projection of the ledger, traversed in SQL."
  },
  "identity": {
    "word": "identity",
    "plural": "identities",
    "gloss": "how anything in the ecosystem is named, exactly once."
  },
  "incremental": {
    "word": "incremental",
    "plural": "incrementals",
    "gloss": "read only what moved."
  },
  "ledger": {
    "word": "ledger",
    "plural": "ledgers",
    "gloss": "hash-chained, append-only, replayable."
  },
  "license": {
    "word": "license",
    "plural": "licenses",
    "gloss": "Licences as expressions, not strings."
  },
  "lifecycle": {
    "word": "lifecycle",
    "plural": "lifecycles",
    "gloss": "Classification axes carried by every node."
  },
  "linking": {
    "word": "linking",
    "plural": "linkings",
    "gloss": "Merging identities and joining what spans two files."
  },
  "manual": {
    "word": "manual",
    "plural": "manuals",
    "gloss": "The manual, generated from the verb registry."
  },
  "node": {
    "word": "node",
    "plural": "nodes",
    "gloss": "the things an ecosystem is made of."
  },
  "normalize": {
    "word": "normalize",
    "plural": "normalizes",
    "gloss": "making two dialects for one package land on one node."
  },
  "planner": {
    "word": "planner",
    "plural": "planners",
    "gloss": "it writes a composition, and shows it before running it."
  },
  "plugins": {
    "word": "plugins",
    "plural": "plugins",
    "gloss": "Everything is a plugin, and built-ins register through the identical path."
  },
  "rbac": {
    "word": "rbac",
    "plural": "rbacs",
    "gloss": "embedded, deterministic, and explainable."
  },
  "reasoning": {
    "word": "reasoning",
    "plural": "reasonings",
    "gloss": "how a conclusion traces back to a line in a file."
  },
  "refusal.reason": {
    "word": "reason",
    "plural": "reasons",
    "gloss": "Why it was declined."
  },
  "refusal.refused": {
    "word": "refused",
    "plural": "refuseds",
    "gloss": "A guard declined this, and said why."
  },
  "rivals": {
    "word": "rivals",
    "plural": "rivals",
    "gloss": "What else is on the market, what it cannot do, and what to build next."
  },
  "severity.critical": {
    "word": "critical",
    "plural": "criticals",
    "gloss": "Severity critical."
  },
  "severity.high": {
    "word": "high",
    "plural": "highs",
    "gloss": "Severity high."
  },
  "severity.info": {
    "word": "info",
    "plural": "infos",
    "gloss": "Severity info."
  },
  "severity.low": {
    "word": "low",
    "plural": "lows",
    "gloss": "Severity low."
  },
  "severity.medium": {
    "word": "medium",
    "plural": "mediums",
    "gloss": "Severity medium."
  },
  "simulator": {
    "word": "simulator",
    "plural": "simulators",
    "gloss": "real artifacts, not mocks."
  },
  "spill": {
    "word": "spill",
    "plural": "spills",
    "gloss": "bounded memory, isolated sessions, content-addressed blocks."
  },
  "station": {
    "word": "station",
    "plural": "stations",
    "gloss": "elements attach, negotiate, stay tracked, and hand over."
  },
  "suggest": {
    "word": "suggest",
    "plural": "suggests",
    "gloss": "the non-deterministic block, made accountable."
  },
  "target.hybrid": {
    "word": "hybrid",
    "plural": "hybrids",
    "gloss": "The hybrid binding."
  },
  "target.live": {
    "word": "live",
    "plural": "lives",
    "gloss": "The live binding."
  },
  "target.simulated": {
    "word": "simulated",
    "plural": "simulateds",
    "gloss": "The simulated binding."
  },
  "ui": {
    "word": "ui",
    "plural": "uis",
    "gloss": "stdlib-served, self-contained, offline-capable."
  },
  "verdict.inapplicable": {
    "word": "inapplicable",
    "plural": "inapplicables",
    "gloss": "Verdict inapplicable."
  },
  "verdict.indeterminate": {
    "word": "indeterminate",
    "plural": "indeterminates",
    "gloss": "Verdict indeterminate."
  },
  "verdict.upheld": {
    "word": "upheld",
    "plural": "uphelds",
    "gloss": "Verdict upheld."
  },
  "verdict.violated": {
    "word": "violated",
    "plural": "violateds",
    "gloss": "Verdict violated."
  },
  "version": {
    "word": "version",
    "plural": "versions",
    "gloss": "Versions and ranges across ecosystems that disagree about both."
  },
  "workspace": {
    "word": "workspace",
    "plural": "workspaces",
    "gloss": "One notebook environment per user, and what that user is allowed to see."
  }
});

export const ROUTES = Object.freeze([
  {
    "method": "GET",
    "path": "/api/admin/datasets",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/admin/quota",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/admin/workspaces",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/apim/analytics",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/apim/apis",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/apim/gateway",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/apim/subscriptions",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/apim/throttles",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/causation",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/contract",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/cycles",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/findings",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/graph",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/history",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/impact",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/integrity",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/lexicon",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/manifest",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/manual",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/node",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/projections",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/reconcile",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/routes",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/scenarios",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/screens",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/search",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/station",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/status",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/stream",
    "transport": "sse"
  },
  {
    "method": "GET",
    "path": "/api/stream/status",
    "transport": "json"
  },
  {
    "method": "GET",
    "path": "/api/verbs",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/ask",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/compose/validate",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/plan",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/run",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/scan",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/scenario",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/snapshot",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/target",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/accept",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/agent-tools",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/ask",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/attach",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/audit",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/c4",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/capture",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/chain",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/changed",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/constraints",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/context",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/count",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/declare",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/discover",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/dismiss",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/enterprise",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/explain",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/filter",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/findings",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/fire",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/gaps",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/govern",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/graph",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/head",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/history",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/impact",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/json",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/lexicon",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/link",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/options",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/quarantine",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/radius",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/reason",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/reconcile",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/risk",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/rivals",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/routine",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/rules",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/sbom",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/scan",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/search",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/simulate",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/sort",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/status",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/suggest",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/target",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/tool",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/tools",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/unique",
    "transport": "json"
  },
  {
    "method": "POST",
    "path": "/api/v/verdicts",
    "transport": "json"
  }
]);

/** What is flowing after these stages. `same` passes the kind through. */
export function producedKind(names) {
  let current = "nothing";
  for (const name of names) {
    const verb = VERBS[name];
    if (!verb) return current;
    current = verb.produces === "same" ? current : verb.produces;
  }
  return current;
}

/** The first type error, or null. The server's rule, not a copy of it. */
export function validate(names) {
  let current = "nothing";
  for (let index = 0; index < names.length; index += 1) {
    const verb = VERBS[names[index]];
    if (!verb) return `stage ${index + 1}: unknown verb \`${names[index]}\``;
    if (verb.consumes !== "any" && verb.consumes !== current) {
      if (current === "nothing") {
        return `stage ${index + 1} \`${names[index]}\` needs ` +
          `${verb.consumes.toUpperCase()} piped into it, but it starts ` +
          `the pipeline — a source verb has to come first`;
      }
      return `stage ${index + 1} \`${names[index]}\` consumes ` +
        `${verb.consumes.toUpperCase()}, but it was given ` +
        `${current.toUpperCase()}`;
    }
    current = verb.produces === "same" ? current : verb.produces;
  }
  return null;
}

/** Every verb that could legally follow what is currently flowing. */
export function successors(kind) {
  return Object.keys(VERBS).filter((name) => {
    const verb = VERBS[name];
    return verb.consumes === "any" || verb.consumes === kind;
  });
}
