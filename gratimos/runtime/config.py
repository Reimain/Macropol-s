"""Configuration with provenance — every value knows where it came from.

The question that costs the most time in a production incident is not "what is
this setting?" but "why is it that?". A config system that answers only the
first turns every misconfiguration into an archaeology exercise across a
defaults file, a deployment chart, an environment variable and somebody's shell
profile.

So every resolved value carries its origin, and `Config.explain()` prints the
whole layer stack for one key — what each layer offered, which one won, and why.
That is the difference between "timeout is 5" and "timeout is 5, from
GRATIMOS_TIMEOUT in the environment, overriding 30 from gratimos.toml and the
built-in default of 15".

Four decisions:

* **TOML, not YAML.** `tomllib` is in the standard library from 3.11, so the
  kernel reads its own configuration with nothing installed — and TOML has no
  significant whitespace, no Norway problem, and one obvious way to write a
  string. YAML's flexibility is a liability in a file that decides how software
  behaves.
* **Layers are ordered and named**, and the order is fixed rather than
  configurable. A config system whose precedence is itself configurable cannot
  be reasoned about at all.
* **Secrets are marked, not guessed.** A key declared secret is redacted
  everywhere it is rendered. Guessing from the name catches `password` and
  misses `dsn`, and the miss is the one that ends up in a log.
* **Freezing is available and one-way.** After start-up the configuration stops
  accepting writes, so a plugin cannot mutate the kernel's settings out from
  under code that already read them.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..errors import GratimosError


class ConfigError(GratimosError):
    """A configuration file was unreadable, or a required value is missing."""


class Layer(IntEnum):
    """Where a value came from. Higher wins; the order is not configurable.

    Deliberately coarse. Every additional layer is another place somebody has to
    look, and four is already enough to be confusing without `explain()`.
    """

    DEFAULT = 0      # compiled in
    FILE = 1         # gratimos.toml
    ENV = 2          # GRATIMOS_*
    OVERRIDE = 3     # set explicitly at runtime, e.g. by a CLI flag

    @property
    def label(self) -> str:
        return {
            Layer.DEFAULT: "built-in default",
            Layer.FILE: "configuration file",
            Layer.ENV: "environment",
            Layer.OVERRIDE: "runtime override",
        }[self]


#: Rendered in place of a secret's value, everywhere.
REDACTED = "«redacted»"

#: Environment variables are read from this prefix and lowercased, with `__`
#: marking a section boundary: `GRATIMOS_CRAWL__INTERVAL` → `crawl.interval`.
ENV_PREFIX = "GRATIMOS_"
ENV_SEPARATOR = "__"


@dataclass(frozen=True, slots=True)
class Setting:
    """One value, and the evidence for it."""

    key: str
    value: Any
    layer: Layer
    origin: str = ""
    secret: bool = False

    @property
    def shown(self) -> Any:
        return REDACTED if self.secret else self.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "value": self.shown, "layer": self.layer.name,
            "origin": self.origin or self.layer.label, "secret": self.secret,
        }

    def __str__(self) -> str:
        return f"{self.key} = {self.shown!r} ({self.origin or self.layer.label})"


def _coerce(raw: str) -> Any:
    """Environment variables are strings; configuration is not.

    Conservative on purpose: `true`/`false`, integers and floats convert, and
    everything else stays a string. Parsing `1.0.0` as a float or `on` as a
    boolean is the kind of helpfulness that produces a bug report a year later.
    """
    text = raw.strip()
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "none", ""):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _flatten(document: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """`{"crawl": {"interval": 1}}` → `{"crawl.interval": 1}`.

    Flat keys mean a setting has one name wherever it is referenced — in a file,
    in an environment variable, in an error message and in `explain()` — which
    is what makes a misconfiguration greppable.

    Lists get one rule, and it is the distinction TOML itself draws: a list of
    *tables* is structure and is flattened by index (`extension.0.id`), while a
    list of scalars is a single value and stays whole. Flattening `requires =
    ["a", "b"]` into `requires.0` and `requires.1` would turn every list-valued
    setting into something no caller could read back.
    """
    flat: dict[str, Any] = {}
    for key, value in document.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{path}."))
        elif (
            isinstance(value, (list, tuple))
            and value
            and all(isinstance(item, Mapping) for item in value)
        ):
            for index, item in enumerate(value):
                flat.update(_flatten(item, f"{path}.{index}."))
        else:
            flat[path] = value
    return flat


class Config:
    """Layered settings, resolved once, explainable afterwards."""

    def __init__(
        self,
        defaults: Mapping[str, Any] | None = None,
        *,
        secrets: Sequence[str] = (),
    ) -> None:
        self._layers: dict[Layer, dict[str, Setting]] = {layer: {} for layer in Layer}
        self._secrets = set(secrets)
        self._frozen = False
        self._reads: set[str] = set()
        if defaults:
            self.load(defaults, layer=Layer.DEFAULT, origin="built-in default")

    # -- loading ----------------------------------------------------------

    def load(self, document: Mapping[str, Any], *, layer: Layer = Layer.FILE,
             origin: str = "") -> "Config":
        """Merge a nested mapping into one layer."""
        self._writable()
        for key, value in _flatten(document).items():
            self._layers[layer][key] = Setting(
                key=key, value=value, layer=layer, origin=origin,
                secret=key in self._secrets,
            )
        return self

    def load_file(self, path: Path | str, *, required: bool = False) -> "Config":
        """Read `gratimos.toml`. A missing optional file is not an error."""
        target = Path(path)
        if not target.exists():
            if required:
                raise ConfigError(f"configuration file {target} does not exist")
            return self
        try:
            document = tomllib.loads(target.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{target} is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"cannot read {target}: {exc}") from exc
        return self.load(document, layer=Layer.FILE, origin=str(target))

    def load_env(self, environ: Mapping[str, str] | None = None, *,
                 prefix: str = ENV_PREFIX) -> "Config":
        """Read `GRATIMOS_*`, with `__` marking a section boundary."""
        self._writable()
        source = os.environ if environ is None else environ
        for name, raw in source.items():
            if not name.startswith(prefix):
                continue
            key = name[len(prefix):].replace(ENV_SEPARATOR, ".").lower()
            if not key:
                continue
            self._layers[Layer.ENV][key] = Setting(
                key=key, value=_coerce(raw), layer=Layer.ENV, origin=name,
                secret=key in self._secrets,
            )
        return self

    def set(self, key: str, value: Any, *, origin: str = "runtime override") -> "Config":
        self._writable()
        self._layers[Layer.OVERRIDE][key] = Setting(
            key=key, value=value, layer=Layer.OVERRIDE, origin=origin,
            secret=key in self._secrets,
        )
        return self

    def mark_secret(self, *keys: str) -> "Config":
        """Declare keys whose values must never be rendered."""
        self._writable()
        self._secrets.update(keys)
        for layer in self._layers.values():
            for key, setting in list(layer.items()):
                if key in self._secrets and not setting.secret:
                    layer[key] = Setting(
                        key=setting.key, value=setting.value, layer=setting.layer,
                        origin=setting.origin, secret=True,
                    )
        return self

    def freeze(self) -> "Config":
        """Stop accepting writes. One-way, and deliberately so."""
        self._frozen = True
        return self

    @property
    def frozen(self) -> bool:
        return self._frozen

    def _writable(self) -> None:
        if self._frozen:
            raise ConfigError(
                "this configuration is frozen; it was sealed at start-up so that "
                "code which already read a value cannot have it changed underneath"
            )

    # -- reading ----------------------------------------------------------

    def resolve(self, key: str) -> Setting | None:
        """The winning setting, or None. The single resolution path."""
        for layer in sorted(Layer, reverse=True):
            setting = self._layers[layer].get(key)
            if setting is not None:
                self._reads.add(key)
                return setting
        return None

    def get(self, key: str, default: Any = None) -> Any:
        setting = self.resolve(key)
        return default if setting is None else setting.value

    def require(self, key: str) -> Any:
        """Fetch, or raise naming what would have to be set and where.

        The error lists the environment variable *and* the file key, because at
        the moment somebody hits this they do not know which mechanism their
        deployment uses.
        """
        setting = self.resolve(key)
        if setting is None:
            variable = ENV_PREFIX + key.replace(".", ENV_SEPARATOR).upper()
            raise ConfigError(
                f"required setting {key!r} is not configured; set it in "
                f"gratimos.toml as {key!r}, or export {variable}"
            )
        return setting.value

    def section(self, prefix: str) -> dict[str, Any]:
        """Every resolved key under a prefix, with the prefix stripped."""
        marker = prefix if prefix.endswith(".") else f"{prefix}."
        found: dict[str, Any] = {}
        for key in self.keys():
            if key.startswith(marker):
                found[key[len(marker):]] = self.get(key)
        return found

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted({key for layer in self._layers.values() for key in layer}))

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.resolve(key) is not None

    def __len__(self) -> int:
        return len(self.keys())

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    # -- explanation ------------------------------------------------------

    def explain(self, key: str) -> str:
        """The whole layer stack for one key. The incident-response answer."""
        winner = self.resolve(key)
        if winner is None:
            return f"{key} is not set in any layer"

        lines = [f"{key} = {winner.shown!r} — from {winner.origin or winner.layer.label}"]
        for layer in sorted(Layer, reverse=True):
            setting = self._layers[layer].get(key)
            if setting is None:
                continue
            mark = "→" if layer is winner.layer else " "
            note = "" if layer is winner.layer else " (overridden)"
            lines.append(
                f"  {mark} {layer.label}: {setting.shown!r}"
                f"{f' [{setting.origin}]' if setting.origin else ''}{note}"
            )
        return "\n".join(lines)

    def unused(self) -> tuple[str, ...]:
        """Configured but never read — usually a typo in a key name.

        The most common configuration bug is not a wrong value; it is a right
        value under a slightly wrong key, which fails silently forever. Anything
        set in a file or the environment and never read is worth reporting at
        start-up.
        """
        supplied = {
            key
            for layer in (Layer.FILE, Layer.ENV, Layer.OVERRIDE)
            for key in self._layers[layer]
        }
        return tuple(sorted(supplied - self._reads))

    def to_dict(self, *, reveal: bool = False) -> dict[str, Any]:
        """Every resolved value. Secrets stay redacted unless explicitly revealed."""
        return {
            key: (self.resolve(key).value if reveal else self.resolve(key).shown)
            for key in self.keys()
        }

    def snapshot(self) -> tuple[Setting, ...]:
        return tuple(
            setting for key in self.keys() if (setting := self.resolve(key)) is not None
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Config {len(self)} settings{', frozen' if self._frozen else ''}>"


def load(
    path: Path | str = "gratimos.toml",
    *,
    defaults: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    secrets: Sequence[str] = (),
    freeze: bool = True,
) -> Config:
    """The one call a deployment makes: defaults, then file, then environment."""
    config = Config(defaults, secrets=secrets)
    config.load_file(path)
    config.load_env(environ)
    return config.freeze() if freeze else config
