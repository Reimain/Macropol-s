"""Configuration provenance and the plug/deplug plane.

The tests that carry weight are the containment ones: an extension that fails to
import, one that raises on activation, and one that raises mid-fan-out must all
leave the kernel running with the failure named. A plugin system that is only
tested on the happy path is a plugin system that takes production down the first
time a customer ships a typo.

Extensions are loaded by dotted path, so this module defines real classes at
module scope and points the host at them — no mocking, and the actual
`importlib` path is exercised.
"""

from __future__ import annotations

import pytest

from gratimos.runtime import (
    Config,
    ConfigError,
    Extension,
    ExtensionError,
    Host,
    Layer,
    Point,
    State,
    extensions_from_config,
    load,
)

FACTORY = "tests.test_runtime"


# --- extensions the host will actually import ----------------------------


class Linker:
    """A well-behaved extension."""

    def __init__(self, project: str = "", **rest) -> None:
        self.project = project
        self.started = False
        self.stopped = False
        self.seen: list[str] = []

    def activate(self) -> None:
        self.started = True

    def deactivate(self) -> None:
        self.stopped = True

    def link(self, subject: str) -> str:
        self.seen.append(subject)
        return f"{self.project}:{subject}"


class Incomplete:
    """Declares the point but does not provide its method."""


class ExplodesOnConstruction:
    def __init__(self, **rest) -> None:
        raise RuntimeError("no credentials configured")


class ExplodesOnActivate:
    def activate(self) -> None:
        raise RuntimeError("the upstream service refused")

    def link(self, subject: str) -> str:  # pragma: no cover - never reached
        return subject


class ExplodesOnUse:
    def link(self, subject: str) -> str:
        raise ValueError("malformed subject")


LINKER = Point(name="linker", methods=("link",), description="links subjects")
REPORTER = Point(name="reporter", methods=("report",), singular=True)


class Reporter:
    def report(self) -> str:
        return "ok"


# --- configuration --------------------------------------------------------


def test_a_later_layer_wins():
    config = Config({"crawl": {"interval": 15}})
    config.load({"crawl": {"interval": 30}}, layer=Layer.FILE, origin="gratimos.toml")
    config.load_env({"GRATIMOS_CRAWL__INTERVAL": "5"})
    assert config.get("crawl.interval") == 5


def test_environment_values_are_coerced_conservatively():
    config = Config().load_env({
        "GRATIMOS_A": "true", "GRATIMOS_B": "12", "GRATIMOS_C": "1.5",
        "GRATIMOS_D": "1.0.0", "GRATIMOS_E": "hello",
    })
    assert config.get("a") is True
    assert config.get("b") == 12
    assert config.get("c") == 1.5
    assert config.get("d") == "1.0.0"      # a version is not a float
    assert config.get("e") == "hello"


def test_a_nested_environment_variable_maps_to_a_section():
    config = Config().load_env({"GRATIMOS_CRAWL__INTERVAL": "5"})
    assert config.get("crawl.interval") == 5


def test_explain_shows_every_layer_and_which_one_won():
    config = Config({"crawl": {"interval": 15}})
    config.load({"crawl": {"interval": 30}}, layer=Layer.FILE, origin="gratimos.toml")
    config.load_env({"GRATIMOS_CRAWL__INTERVAL": "5"})

    text = config.explain("crawl.interval")
    assert "GRATIMOS_CRAWL__INTERVAL" in text
    assert "gratimos.toml" in text
    assert "overridden" in text


def test_explaining_an_unset_key_says_so():
    assert "not set in any layer" in Config().explain("nothing.here")


def test_a_missing_required_setting_names_both_ways_to_set_it():
    with pytest.raises(ConfigError, match="GRATIMOS_CRAWL__INTERVAL"):
        Config().require("crawl.interval")


def test_a_secret_is_redacted_everywhere_it_is_rendered():
    config = Config({"api": {"token": "sk-live-1234"}}, secrets=["api.token"])
    assert "sk-live-1234" not in config.explain("api.token")
    assert "sk-live-1234" not in str(config.to_dict())
    assert config.get("api.token") == "sk-live-1234"      # still usable in code


def test_a_secret_declared_later_redacts_what_was_already_loaded():
    config = Config({"api": {"token": "sk-live-1234"}})
    config.mark_secret("api.token")
    assert "sk-live-1234" not in str(config.to_dict())


def test_revealing_is_explicit_and_therefore_greppable():
    config = Config({"api": {"token": "sk"}}, secrets=["api.token"])
    assert config.to_dict(reveal=True)["api.token"] == "sk"


def test_a_frozen_config_refuses_writes():
    config = Config({"a": 1}).freeze()
    with pytest.raises(ConfigError, match="frozen"):
        config.set("a", 2)


def test_a_setting_supplied_but_never_read_is_reported():
    """The commonest configuration bug: a right value under a wrong key."""
    config = Config({"crawl": {"interval": 15}})
    config.load_env({"GRATIMOS_CRAWL__INTREVAL": "5"})
    config.get("crawl.interval")
    assert config.unused() == ("crawl.intreval",)


def test_a_section_is_returned_with_its_prefix_stripped():
    config = Config({"crawl": {"interval": 5, "timeout": 10}, "other": {"x": 1}})
    assert config.section("crawl") == {"interval": 5, "timeout": 10}


def test_a_toml_file_is_read(tmp_path):
    path = tmp_path / "gratimos.toml"
    path.write_text('[crawl]\ninterval = 7\n', encoding="utf-8")
    assert Config().load_file(path).get("crawl.interval") == 7


def test_a_missing_optional_file_is_not_an_error(tmp_path):
    assert len(Config().load_file(tmp_path / "absent.toml")) == 0


def test_a_missing_required_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        Config().load_file(tmp_path / "absent.toml", required=True)


def test_malformed_toml_names_the_file(tmp_path):
    path = tmp_path / "gratimos.toml"
    path.write_text("this is not = = toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        Config().load_file(path)


def test_the_one_call_a_deployment_makes(tmp_path):
    path = tmp_path / "gratimos.toml"
    path.write_text('[crawl]\ninterval = 7\n', encoding="utf-8")
    config = load(path, defaults={"crawl": {"timeout": 30}},
                  environ={"GRATIMOS_CRAWL__INTERVAL": "2"})
    assert config.get("crawl.interval") == 2
    assert config.get("crawl.timeout") == 30
    assert config.frozen


# --- declaring extensions -------------------------------------------------


@pytest.fixture
def host():
    return Host([LINKER, REPORTER])


def test_a_factory_without_a_module_path_is_refused():
    with pytest.raises(ExtensionError, match="package.module:Attribute"):
        Extension(id="x", point="linker", factory="JustAClass")


def test_an_extension_cannot_require_itself():
    with pytest.raises(ExtensionError, match="cannot require itself"):
        Extension(id="x", point="linker", factory="m:C", requires=("x",))


def test_an_unknown_extension_point_is_refused_at_declaration(host):
    with pytest.raises(ExtensionError, match="unknown point"):
        host.declare(Extension(id="x", point="nowhere", factory="m:C"))


def test_a_dependency_cycle_is_refused_with_the_cycle_printed(host):
    host.declare(Extension(id="a", point="linker", factory="m:C", requires=("b",)))
    with pytest.raises(ExtensionError, match="dependency cycle"):
        host.declare(Extension(id="b", point="linker", factory="m:C", requires=("a",)))


def test_activation_order_puts_dependencies_first(host):
    host.declare(Extension(id="late", point="linker", factory=f"{FACTORY}:Linker",
                           requires=("early",)))
    host.declare(Extension(id="early", point="linker", factory=f"{FACTORY}:Linker"))
    assert host.order().index("early") < host.order().index("late")


# --- the happy path -------------------------------------------------------


def test_an_extension_is_imported_constructed_and_activated(host):
    host.declare(Extension(id="acme.linker", point="linker",
                           factory=f"{FACTORY}:Linker", settings={"project": "PAY"}))
    slot = host.plug("acme.linker")
    assert slot.state is State.ACTIVE
    assert slot.instance.started
    assert slot.instance.project == "PAY"


def test_the_kernel_calls_every_extension_at_a_point(host):
    for index in range(3):
        host.declare(Extension(id=f"linker-{index}", point="linker",
                               factory=f"{FACTORY}:Linker",
                               settings={"project": f"P{index}"}))
    host.start()
    assert host.call("linker", "link", "issue-1") == ("P0:issue-1", "P1:issue-1",
                                                      "P2:issue-1")


def test_deplugging_runs_teardown_and_can_be_reversed(host):
    host.declare(Extension(id="acme.linker", point="linker",
                           factory=f"{FACTORY}:Linker"))
    instance = host.plug("acme.linker").instance
    host.deplug("acme.linker")
    assert instance.stopped
    assert host.get("acme.linker") is None

    assert host.plug("acme.linker").state is State.ACTIVE
    assert host.get("acme.linker") is not None


def test_deplugging_takes_its_dependents_with_it(host):
    host.declare(Extension(id="base", point="linker", factory=f"{FACTORY}:Linker"))
    host.declare(Extension(id="built-on", point="linker",
                           factory=f"{FACTORY}:Linker", requires=("base",)))
    host.start()
    host.deplug("base")
    assert host.get("built-on") is None


def test_a_disabled_extension_is_never_imported(host):
    host.declare(Extension(id="off", point="linker",
                           factory="module.that.does.not.exist:Nothing", enabled=False))
    slot = host.plug("off")
    assert slot.state is State.INACTIVE
    assert "disabled" in slot.error


# --- containment: the tests that matter -----------------------------------


def test_an_extension_that_cannot_be_imported_is_quarantined_not_fatal(host):
    host.declare(Extension(id="broken", point="linker",
                           factory="acme.nonexistent.module:Thing"))
    host.declare(Extension(id="fine", point="linker", factory=f"{FACTORY}:Linker"))
    host.start()

    assert host.health()["quarantined"]["broken"]
    assert host.get("fine") is not None
    assert host.health()["degraded"]


def test_a_missing_attribute_names_the_module_and_the_attribute(host):
    host.declare(Extension(id="typo", point="linker",
                           factory=f"{FACTORY}:Lnker"))
    slot = host.plug("typo")
    assert slot.state is State.QUARANTINED
    assert "Lnker" in slot.error


def test_an_extension_that_raises_on_construction_is_quarantined(host):
    host.declare(Extension(id="noauth", point="linker",
                           factory=f"{FACTORY}:ExplodesOnConstruction"))
    slot = host.plug("noauth")
    assert slot.state is State.QUARANTINED
    assert "no credentials configured" in slot.error
    assert slot.traceback


def test_an_extension_that_raises_on_activate_is_quarantined(host):
    host.declare(Extension(id="upstream", point="linker",
                           factory=f"{FACTORY}:ExplodesOnActivate"))
    slot = host.plug("upstream")
    assert slot.state is State.QUARANTINED
    assert "activate() failed" in slot.error


def test_an_extension_missing_a_required_method_is_refused_at_activation(host):
    """Named at start-up rather than as an AttributeError mid-request."""
    host.declare(Extension(id="partial", point="linker",
                           factory=f"{FACTORY}:Incomplete"))
    slot = host.plug("partial")
    assert slot.state is State.QUARANTINED
    assert "does not provide link" in slot.error


def test_one_extension_raising_mid_fan_out_does_not_stop_the_others(host):
    host.declare(Extension(id="good-1", point="linker", factory=f"{FACTORY}:Linker",
                           settings={"project": "A"}))
    host.declare(Extension(id="bad", point="linker", factory=f"{FACTORY}:ExplodesOnUse"))
    host.declare(Extension(id="good-2", point="linker", factory=f"{FACTORY}:Linker",
                           settings={"project": "B"}))
    host.start()

    results = host.call("linker", "link", "issue-1")
    assert results == ("A:issue-1", "B:issue-1")
    assert host.health()["quarantined"]["bad"]


def test_an_extension_that_failed_in_use_stays_out_until_replugged(host):
    host.declare(Extension(id="bad", point="linker", factory=f"{FACTORY}:ExplodesOnUse"))
    host.start()
    host.call("linker", "link", "x")
    assert host.get("bad") is None
    assert host.slots[0].failures == 1


def test_an_extension_whose_dependency_failed_does_not_activate(host):
    host.declare(Extension(id="base", point="linker",
                           factory="acme.nonexistent:Thing"))
    host.declare(Extension(id="dependent", point="linker",
                           factory=f"{FACTORY}:Linker", requires=("base",)))
    host.start()
    assert host.get("dependent") is None
    assert "not active" in host.slots[1].error


def test_a_singular_point_admits_only_one_active_extension(host):
    host.declare(Extension(id="first", point="reporter", factory=f"{FACTORY}:Reporter"))
    host.declare(Extension(id="second", point="reporter", factory=f"{FACTORY}:Reporter"))
    host.start()
    assert host.one("reporter") is not None
    assert "admits one active extension" in host.slots[1].error


# --- configuration drives the whole thing ---------------------------------


def test_extensions_are_declared_from_a_config_file(tmp_path):
    path = tmp_path / "gratimos.toml"
    path.write_text(
        "[[extension]]\n"
        'id = "acme.jira"\n'
        'point = "linker"\n'
        f'factory = "{FACTORY}:Linker"\n'
        "enabled = true\n"
        "\n"
        "[extension.settings]\n"
        'project = "PAY"\n',
        encoding="utf-8",
    )
    config = Config().load_file(path)
    declared = extensions_from_config(config)
    assert len(declared) == 1
    assert declared[0].id == "acme.jira"
    assert declared[0].settings["project"] == "PAY"


def test_a_customer_extension_reaches_production_without_a_kernel_change(tmp_path):
    """The maintenance story end to end: a file, and behaviour changes.

    Nothing in `gratimos/` imports `Linker`. It is named in a TOML file, found
    by dotted path at activation, checked against the point's contract, and
    called — and removing the file removes the behaviour.
    """
    path = tmp_path / "gratimos.toml"
    path.write_text(
        "[[extension]]\n"
        'id = "acme.jira"\n'
        'point = "linker"\n'
        f'factory = "{FACTORY}:Linker"\n'
        "\n"
        "[extension.settings]\n"
        'project = "PAY"\n',
        encoding="utf-8",
    )
    config = Config().load_file(path)
    host = Host([LINKER], config=config)
    host.declare_all(extensions_from_config(config))
    host.start()

    assert host.call("linker", "link", "issue-9") == ("PAY:issue-9",)
    assert not host.health()["degraded"]

    host.stop()
    assert host.call("linker", "link", "issue-9") == ()


def test_the_health_view_tells_an_operator_what_is_wrong(host):
    host.declare(Extension(id="ok", point="linker", factory=f"{FACTORY}:Linker"))
    host.declare(Extension(id="broken", point="linker", factory="nope.nope:X"))
    host.start()

    health = host.health()
    assert health["active"] == 1
    assert "broken" in health["quarantined"]
    assert "ok" in host.render()
