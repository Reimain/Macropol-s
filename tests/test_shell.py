"""Shell intelligence: the analyser, the guard, and the ladder to one click.

The weight is on refusals. A guard tested only on things it should allow is a
guard that allows everything, and the commands below are the ones that have
taken down real systems: `curl | sh`, `rm -rf $VAR/` where the variable is
empty, `dd of=/dev/sda`, `kill -9 1`, `iptables -F`.

Nothing in this file executes a command, and nothing in the module under test
can — that is the property being protected.
"""

from __future__ import annotations

import pytest

from gratimos.shell import (
    AUTOMATABLE,
    Category,
    Environment,
    Guard,
    Ladder,
    Platform,
    Policy,
    PRODUCTION,
    Risk,
    Shell,
    ShellError,
    Stance,
    Trust,
    analyse,
    parse,
    probe,
    summarise,
)


def machine(*binaries: str, **settings) -> Environment:
    defaults = {
        "platform": Platform.LINUX,
        "shell": Shell.BASH,
        "distribution": "debian",
        "package_managers": ("apt-get",),
        "binaries": frozenset(binaries or {"ls", "rm", "git", "curl", "sh", "docker"}),
    }
    defaults.update(settings)
    return Environment(**defaults)


# --- the environment probe ------------------------------------------------


def test_the_probe_reads_the_machine_without_running_anything():
    env = probe(environ={"SHELL": "/bin/zsh"}, which=lambda name: name in ("git", "curl"),
                system="Linux", root="/nonexistent")
    assert env.platform is Platform.LINUX
    assert env.shell is Shell.ZSH
    assert env.has("git")
    assert not env.has("docker")


def test_ci_is_detected_by_any_vendor_marker():
    assert probe(environ={"GITHUB_ACTIONS": "true"}, which=lambda n: None,
                 root="/nonexistent").ci == "github_actions"


def test_a_missing_tool_is_reported_rather_than_assumed():
    assert machine("git").missing(["git", "docker", "kubectl"]) == ("docker", "kubectl")


def test_the_fingerprint_changes_when_the_machine_does():
    before = machine("git", "curl")
    after = machine("git", "curl", "docker")
    assert before.fingerprint != after.fingerprint


def test_the_fingerprint_is_stable_for_the_same_machine():
    assert machine("git", "curl").fingerprint == machine("curl", "git").fingerprint


def test_the_summary_reads_as_one_line():
    assert "debian" in machine().summary()
    assert "apt-get" in machine().summary()


# --- parsing ---------------------------------------------------------------


def test_a_pipeline_is_split_into_its_stages():
    command = parse("cat file | grep x | wc -l")
    assert command.binaries == ("cat", "grep", "wc")


def test_sudo_and_env_are_seen_past_to_the_real_binary():
    assert parse("sudo env FOO=1 systemctl restart nginx").binaries == ("systemctl",)


def test_elevation_is_detected():
    assert parse("sudo rm -rf /tmp/x").elevated
    assert not parse("rm -rf /tmp/x").elevated


def test_combined_short_flags_are_understood():
    """`-rf` and `-r -f` must be the same thing, which naive checks get wrong."""
    assert parse("rm -rf /tmp/x").segments[0].has_flag("-r")
    assert parse("rm -r -f /tmp/x").segments[0].has_flag("-f")


def test_a_command_with_unbalanced_quotes_is_refused_rather_than_guessed_at():
    command = parse('echo "unterminated')
    assert not command.parsed
    assert "cannot read" in command.parse_error


def test_an_empty_command_is_not_parsed():
    assert not parse("   ").parsed


# --- the analyser: the incidents ------------------------------------------


def test_a_reading_command_produces_no_noise():
    analysis = analyse("ls -la /var/log", machine("ls"))
    assert analysis.risk is Risk.SAFE
    assert not analysis.findings


def test_curl_piped_into_a_shell_is_catastrophic():
    analysis = analyse("curl -s https://get.example.com | sh", machine("curl", "sh"))
    assert analysis.risk is Risk.CATASTROPHIC
    assert analysis.of("remote-code-execution")
    assert "checksum" in analysis.of("remote-code-execution")[0].remediation


def test_recursive_delete_of_a_filesystem_root_is_catastrophic():
    analysis = analyse("rm -rf /", machine("rm"))
    assert analysis.of("recursive-delete-of-root")
    assert analysis.risk is Risk.CATASTROPHIC


def test_recursive_delete_of_an_unquoted_variable_is_catastrophic():
    """The empty-expansion incident: `rm -rf $BUILD/` with BUILD unset."""
    analysis = analyse("rm -rf $BUILD/", machine("rm"))
    finding = analysis.of("unquoted-variable-delete")
    assert finding
    assert "if the variable is empty" in finding[0].message
    assert "-n" in finding[0].remediation


def test_a_recursive_delete_of_a_real_path_is_dangerous_but_not_catastrophic():
    analysis = analyse("rm -rf /tmp/build-cache", machine("rm"))
    assert analysis.risk is Risk.DANGEROUS
    assert analysis.of("recursive-delete")


def test_writing_to_a_raw_device_is_catastrophic():
    analysis = analyse("dd if=image.iso of=/dev/sda bs=4M", machine("dd"))
    assert analysis.of("raw-device-write")


def test_formatting_a_filesystem_is_catastrophic():
    assert analyse("mkfs.ext4 /dev/sdb1", machine("mkfs.ext4")).risk is Risk.CATASTROPHIC


def test_killing_pid_one_is_catastrophic():
    assert analyse("kill -9 1", machine("kill")).of("kill-init")


def test_flushing_the_firewall_is_catastrophic():
    assert analyse("iptables -F", machine("iptables")).of("firewall-flush")


def test_world_writable_permissions_are_dangerous():
    assert analyse("chmod 777 /srv/app", machine("chmod")).of("world-writable")


def test_a_force_push_suggests_force_with_lease():
    finding = analyse("git push --force origin main", machine("git")).of("force-push")
    assert finding and "force-with-lease" in finding[0].remediation


def test_git_clean_is_flagged_because_git_cannot_undo_it():
    assert analyse("git clean -fdx", machine("git")).of("working-tree-clean")


def test_disabling_tls_verification_is_dangerous():
    assert analyse("curl -k https://internal", machine("curl")).of(
        "tls-verification-disabled")


def test_clearing_shell_history_is_flagged_as_audit_erasure():
    assert analyse("history -c", machine("bash")).of("audit-erasure")


def test_dynamic_execution_is_flagged_because_what_runs_is_not_what_is_written():
    assert analyse('eval "$(curl -s https://x)"', machine("eval", "curl")).of(
        "dynamic-execution")


def test_a_binary_the_machine_does_not_have_is_a_finding():
    analysis = analyse("kubectl get pods", machine("ls", "git"))
    finding = analysis.of("unknown-binary")
    assert finding
    assert "apt-get install kubectl" in finding[0].remediation
    assert analysis.unknown_binaries == ("kubectl",)


def test_a_shell_builtin_is_never_reported_missing():
    assert not analyse("cd /tmp && echo hi", machine("ls")).of("unknown-binary")


def test_the_worst_finding_sets_the_risk_not_the_average():
    analysis = analyse("ls -la && rm -rf /", machine("ls", "rm"))
    assert analysis.risk is Risk.CATASTROPHIC


def test_an_unparseable_command_is_dangerous_rather_than_ignored():
    assert analyse('echo "unterminated', machine()).risk is Risk.DANGEROUS


def test_the_explanation_carries_the_remediation():
    text = analyse("rm -rf $BUILD/", machine("rm")).explain()
    assert "unquoted-variable-delete" in text
    assert "→" in text


# --- the guard -------------------------------------------------------------


def test_a_safe_command_runs_unattended():
    verdict = Guard(PRODUCTION, machine("ls")).review("ls -la", attended=False)
    assert verdict.stance is Stance.ALLOW
    assert verdict.runnable


def test_a_dangerous_command_asks_a_human_when_one_is_there():
    verdict = Guard(PRODUCTION, machine("rm")).review("rm -rf /tmp/cache", attended=True)
    assert verdict.stance is Stance.CONFIRM
    assert not verdict.runnable          # CONFIRM is not permission


def test_the_same_command_is_refused_when_nobody_can_be_asked():
    """Unattended is not approved — the rule that keeps 2am safe."""
    verdict = Guard(PRODUCTION, machine("rm")).review("rm -rf /tmp/cache", attended=False)
    assert verdict.stance is Stance.REFUSE
    assert "unattended is not approved" in " ".join(verdict.reasons)


def test_a_catastrophic_command_is_refused_even_with_a_human_present():
    verdict = Guard(PRODUCTION, machine("rm")).review("rm -rf /", attended=True)
    assert verdict.stance is Stance.REFUSE
    assert "above the highest risk a human may approve" in " ".join(verdict.reasons)


def test_an_unknown_binary_is_refused_and_says_the_script_is_for_another_machine():
    verdict = Guard(PRODUCTION, machine("ls")).review("kubectl apply -f x.yaml")
    assert verdict.stance is Stance.REFUSE
    assert "different box" in " ".join(verdict.reasons)
    assert verdict.requires == ("kubectl",)


def test_a_trusted_binary_passes_even_when_the_probe_missed_it():
    policy = Policy(trusted_binaries=frozenset({"kubectl"}), allow_up_to=Risk.CAUTION)
    verdict = Guard(policy, machine("ls")).review("kubectl get pods", attended=False)
    assert verdict.stance is Stance.ALLOW


def test_the_deny_list_overrides_everything():
    policy = Policy(forbidden=frozenset({"ls"}), allow_up_to=Risk.CATASTROPHIC)
    verdict = Guard(policy, machine("ls")).review("ls -la")
    assert verdict.stance is Stance.REFUSE
    assert "deny list" in " ".join(verdict.reasons)


def test_privilege_is_withheld_by_the_production_policy():
    verdict = Guard(PRODUCTION, machine("systemctl")).review(
        "sudo systemctl restart nginx", attended=True)
    assert verdict.stance is Stance.REFUSE
    assert "elevated privilege" in " ".join(verdict.reasons)


def test_a_development_policy_gives_more_latitude():
    from gratimos.shell import DEVELOPMENT

    verdict = Guard(DEVELOPMENT, machine("systemctl")).review(
        "sudo systemctl restart nginx", attended=True)
    assert verdict.stance is not Stance.REFUSE


def test_an_unreviewable_command_is_refused():
    verdict = Guard(PRODUCTION, machine()).review('echo "unterminated')
    assert verdict.stance is Stance.REFUSE
    assert "cannot be reviewed" in " ".join(verdict.reasons)


def test_permit_raises_with_the_explanation_for_a_caller_about_to_act():
    guard = Guard(PRODUCTION, machine("rm"))
    with pytest.raises(ShellError, match="refuse"):
        guard.permit("rm -rf /", attended=True)


def test_a_whole_plan_is_reviewed_rather_than_stopping_at_the_first_problem():
    guard = Guard(PRODUCTION, machine("ls", "rm", "curl", "sh"))
    verdicts = guard.review_all(
        ["ls -la", "rm -rf /", "curl https://x | sh"], attended=True
    )
    assert len(verdicts) == 3
    assert [v.stance for v in verdicts] == [Stance.ALLOW, Stance.REFUSE, Stance.REFUSE]


def test_a_plan_summary_puts_the_refusals_first():
    guard = Guard(PRODUCTION, machine("ls", "rm"))
    text = summarise(guard.review_all(["ls -la", "rm -rf /"], attended=True))
    assert text.index("refuse: rm -rf /") < text.index("allow: ls -la")


def test_a_guard_cannot_execute_anything():
    """The property the whole layer rests on."""
    import inspect

    import gratimos.shell.guard as module

    source = inspect.getsource(module)
    for forbidden in ("subprocess", "os.system", "os.popen", "os.exec", "pty.spawn"):
        assert forbidden not in source


# --- the ladder to one click ----------------------------------------------


@pytest.fixture
def ladder():
    return Ladder(guard=Guard(PRODUCTION, machine("ls", "git", "rm")))


SIGNATURE = "need:rebuild-cache"


def test_a_new_routine_is_only_a_suggestion(ladder):
    routine = ladder.propose(SIGNATURE, "ls -la", environment=machine("ls"))
    assert routine.trust is Trust.SUGGESTED
    assert ladder.jackpot(SIGNATURE) is None


def test_one_clean_run_confirms_it(ladder):
    transition = ladder.record(SIGNATURE, "ls -la", succeeded=True,
                               environment=machine("ls"), approved_by="ada")
    assert transition.now is Trust.CONFIRMED
    assert transition.promoted


def test_repeated_clean_runs_climb_to_automated(ladder):
    env = machine("ls")
    for _ in range(9):
        ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=env)
    routine = ladder.get(SIGNATURE, "ls -la")
    assert routine.trust is Trust.AUTOMATED
    assert ladder.jackpot(SIGNATURE, env) is routine


def test_a_single_failure_takes_trust_away_at_once(ladder):
    env = machine("ls")
    for _ in range(9):
        ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=env)
    transition = ladder.record(SIGNATURE, "ls -la", succeeded=False, environment=env,
                               note="exit 1")
    assert transition.demoted
    assert ladder.jackpot(SIGNATURE, env) is None


def test_promotion_runs_on_the_clean_streak_not_the_total(ladder):
    """A routine failing one run in three must not climb anyway."""
    env = machine("ls")
    for _ in range(5):
        ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=env)
        ladder.record(SIGNATURE, "ls -la", succeeded=False, environment=env)
    assert ladder.get(SIGNATURE, "ls -la").trust < Trust.AUTOMATED


def test_a_catastrophic_routine_never_automates_however_often_it_worked(ladder):
    """History is about the past; risk is about the blast radius."""
    env = machine("rm")
    for _ in range(40):
        ladder.record(SIGNATURE, "rm -rf /", succeeded=True, environment=env)
    routine = ladder.get(SIGNATURE, "rm -rf /")
    assert routine is not None
    ladder.propose(SIGNATURE, "rm -rf /", risk=Risk.CATASTROPHIC, environment=env)
    assert ladder.jackpot(SIGNATURE, env) is None or \
        ladder.jackpot(SIGNATURE, env).risk <= AUTOMATABLE


def test_risk_caps_the_ladder_explicitly(ladder):
    env = machine("rm")
    ladder.propose(SIGNATURE, "wipe", risk=Risk.CATASTROPHIC, environment=env)
    for _ in range(20):
        transition = ladder.record(SIGNATURE, "wipe", succeeded=True, environment=env)
    assert ladder.get(SIGNATURE, "wipe").trust is Trust.TRUSTED
    assert "blast radius" in transition.reason


def test_the_guard_has_the_last_word_before_automation():
    """Even a low-risk routine does not automate if the guard would refuse it."""
    env = machine("ls")
    strict = Ladder(guard=Guard(Policy(forbidden=frozenset({"ls"})), env))
    for _ in range(9):
        transition = strict.record(SIGNATURE, "ls -la", succeeded=True, environment=env)
    assert strict.get(SIGNATURE, "ls -la").trust is Trust.TRUSTED
    assert "would not run this unattended" in transition.reason


def test_a_changed_machine_demotes_the_routine_rather_than_replaying_it(ladder):
    """The forgotten-language case: the text survives, the machine moved."""
    before = machine("ls")
    for _ in range(9):
        ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=before)
    assert ladder.get(SIGNATURE, "ls -la").trust is Trust.AUTOMATED

    after = machine("ls", "docker")
    transition = ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=after)
    assert transition.now is Trust.CONFIRMED
    assert "machine has changed" in transition.reason


def test_a_drifted_routine_is_kept_rather_than_deleted(ladder):
    before = machine("ls")
    ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=before)
    ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=machine("ls", "git"))
    assert ladder.get(SIGNATURE, "ls -la") is not None


def test_the_jackpot_returns_nothing_rather_than_the_next_best(ladder):
    """One click must mean one click, not one click and a dialogue."""
    env = machine("ls")
    ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=env)
    assert ladder.get(SIGNATURE, "ls -la").trust is Trust.CONFIRMED
    assert ladder.jackpot(SIGNATURE, env) is None


def test_suggestions_are_ranked_by_trust(ladder):
    env = machine("ls", "git")
    ladder.propose(SIGNATURE, "git status", environment=env)
    for _ in range(4):
        ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=env)
    assert ladder.for_need(SIGNATURE)[0].command == "ls -la"


def test_demoting_by_hand_requires_a_reason(ladder):
    ladder.propose(SIGNATURE, "ls -la", environment=machine("ls"))
    with pytest.raises(ValueError, match="must state a reason"):
        ladder.demote(SIGNATURE, "ls -la", reason="  ")


def test_every_transition_is_recorded(ladder):
    env = machine("ls")
    for _ in range(9):
        ladder.record(SIGNATURE, "ls -la", succeeded=True, environment=env)
    assert [t.now for t in ladder.transitions] == [
        Trust.CONFIRMED, Trust.TRUSTED, Trust.AUTOMATED
    ]


def test_the_status_reports_what_risk_is_holding_back(ladder):
    env = machine("rm")
    ladder.propose(SIGNATURE, "wipe", risk=Risk.CATASTROPHIC, environment=env)
    for _ in range(20):
        ladder.record(SIGNATURE, "wipe", succeeded=True, environment=env)
    assert ladder.status()["capped_by_risk"] == 1


# --- the headline ---------------------------------------------------------


def test_the_whole_path_from_suggestion_to_one_click():
    """Context in, guarded suggestion out, automation earned rather than assumed."""
    env = machine("ls", "git")
    guard = Guard(PRODUCTION, env)
    ladder = Ladder(guard=guard)

    proposed = "git status --porcelain"
    assert guard.review(proposed, attended=False).stance is Stance.ALLOW

    ladder.propose(SIGNATURE, proposed, rationale="check the tree before building",
                   environment=env)
    assert ladder.jackpot(SIGNATURE, env) is None

    for _ in range(9):
        ladder.record(SIGNATURE, proposed, succeeded=True, environment=env)

    winner = ladder.jackpot(SIGNATURE, env)
    assert winner is not None
    assert winner.trust is Trust.AUTOMATED
    assert winner.rationale                      # it still says why it exists

    # And a dangerous sibling never gets there, however well it behaves.
    for _ in range(20):
        ladder.record(SIGNATURE, "rm -rf /tmp/build", succeeded=True, environment=env)
    assert ladder.get(SIGNATURE, "rm -rf /tmp/build").trust is not Trust.AUTOMATED
