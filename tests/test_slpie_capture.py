"""Format intelligence and the capture firewall.

Four properties carry §26:

1. **Content beats the filename.** A YAML document named `.json` is identified as
   YAML *and* the disagreement is reported — that mislabelling is the exact case
   the module exists to catch.
2. **Addressing reaches the cell.** `inventory.xlsx#Sheet1!B12`, not
   `inventory.xlsx`. A file-level citation does not let anybody check a claim.
3. **Unread is never rendered as empty.** A scanned PDF, an untranscribed video
   and a format with no reader all produce a block that says so. Silence there
   would read as "it legitimately contained nothing".
4. **The firewall defaults to deny, and explicit deny wins.** A capture matching
   nothing is held; a narrow ACCEPT cannot punch through an earlier DROP; and a
   DROP tells the caller nothing while recording everything.
"""

from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

from slpie.capture import (
    MAX_CAPTURE,
    Block,
    BlockKind,
    Candidate,
    Chain,
    Depth,
    Rule,
    Verdict,
    column_index,
    column_name,
    default_chain,
    digest_of,
    identify,
    parse,
    parse_a1,
    readable_formats,
    strategies_for,
)
from slpie.capture.locator import at_pointer, escape_token, unescape_token

KUBERNETES = b"apiVersion: v1\nkind: Service\nmetadata:\n  name: payments\n"
PACKAGE = b'{"name":"demo","dependencies":{"lodash":"^3.0.0"}}'


def check(uri: str, payload: bytes, chain: Chain | None = None):
    found = identify(payload, uri, depth=Depth.STRUCTURE)
    return (chain if chain is not None else default_chain()).evaluate(Candidate(
        uri=uri, size=len(payload), format=found, digest=digest_of(payload),
    ))


def xlsx(cells: dict[str, str]) -> bytes:
    """A real XLSX. Real artifacts beat mocks, per the suite's own rule."""
    shared = list(dict.fromkeys(cells.values()))
    rows: dict[int, list[str]] = {}
    for address, value in cells.items():
        _column, row = parse_a1(address)
        rows.setdefault(row, []).append(
            f'<c r="{address}" t="s"><v>{shared.index(value)}</v></c>'
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook/>')
        archive.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0"?><sst>'
            + "".join(f"<si><t>{item}</t></si>" for item in shared)
            + "</sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet><sheetData>'
            + "".join(
                f'<row r="{row}">{"".join(items)}</row>'
                for row, items in sorted(rows.items())
            )
            + "</sheetData></worksheet>",
        )
    return buf.getvalue()


def wav(seconds: float = 1.0) -> bytes:
    data = b"\x00" * int(44100 * 2 * 2 * seconds)
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


# --- content beats the filename -----------------------------------------


def test_a_yaml_document_named_json_is_identified_as_yaml_and_reported():
    """The headline case. An over-broad alias group once made JSON and YAML
    interchangeable, so this exact mislabelling reported no contradiction."""
    found = identify(KUBERNETES, "file:///r/config.json", depth=Depth.SEMANTIC)

    assert found.name == "yaml"
    assert found.contradicts_extension
    assert found.claimed == "json"


def test_the_same_content_correctly_named_is_not_a_contradiction():
    for uri in ("file:///r/svc.yaml", "file:///r/svc.yml"):
        found = identify(KUBERNETES, uri, depth=Depth.SEMANTIC)
        assert not found.contradicts_extension, uri


def test_a_lockfile_may_legitimately_hold_json_or_yaml():
    assert not identify(PACKAGE, "file:///r/yarn.lock").contradicts_extension


def test_magic_bytes_beat_both_content_and_name():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("xl/workbook.xml", "<w/>")

    found = identify(buf.getvalue(), "file:///r/notes.txt")
    assert found.name == "xlsx"
    assert found.confidence > 0.9


def test_an_extension_alone_is_a_weak_signal_rated_as_one():
    """A name heuristic sits at 0.25 on the same ladder every other conclusion
    uses, and the firewall holds anything identified only that way."""
    found = identify(b"\x01\x02 not really anything", "file:///r/thing.sql")

    assert found.confidence <= 0.6
    assert "weak" in found.reason or "not decodable" in found.reason


def test_python_is_identified_by_its_content_not_its_extension():
    """Without a structural signature every .py file scored 0.25 and the firewall
    quarantined the entire source tree for being unidentifiable."""
    source = Path("slpie/capture/format.py").read_bytes()
    found = identify(source, "file:///r/format.py")

    assert found.name == "python"
    assert found.confidence >= 0.9


def test_prose_mentioning_imports_is_not_python():
    prose = b"This document explains how to import a package and use a class.\n"
    assert identify(prose, "file:///r/readme.txt").name != "python"


def test_the_semantic_depth_names_the_dialect_within_a_family():
    """A .yaml may be Kubernetes, a workflow, compose or OpenAPI, and each wants a
    different reader. Deciding by extension routes all four to whichever parser was
    written first."""
    assert identify(
        KUBERNETES, "file:///r/a.yaml", depth=Depth.SEMANTIC,
    ).dialect == "kubernetes"
    assert identify(
        b'{"lockfileVersion":3,"packages":{}}', "file:///r/l.json",
        depth=Depth.SEMANTIC,
    ).dialect == "npm-lockfile"


def test_depth_is_respected_so_a_probe_never_reads_everything():
    probed = identify(PACKAGE, "file:///r/package.json", depth=Depth.PROBE)
    assert probed.depth is Depth.PROBE
    assert probed.dialect == "", "a probe must not do semantic work"


# --- addressing reaches the cell ----------------------------------------


def test_a_spreadsheet_cell_is_addressed_the_way_its_user_would_write_it():
    _format, document = parse(
        xlsx({"A12": "lodash", "B12": "4.17.21"}),
        "file:///r/inventory.xlsx", depth=Depth.MODEL,
    )
    references = {cell.locator.reference for cell in document.cells()}

    assert "inventory.xlsx#sheet1!A12" in references
    assert "inventory.xlsx#sheet1!B12" in references


def test_json_is_addressed_by_rfc_6901_pointer():
    _format, document = parse(PACKAGE, "file:///r/package.json", depth=Depth.MODEL)
    references = {cell.locator.reference for cell in document.cells()}

    assert "package.json#/dependencies/lodash" in references


def test_column_names_are_bijective_base_26():
    """Not ordinary base-26: there is no zero digit, so the naive divmod loop
    produces `A@` for column 27."""
    assert column_name(1) == "A"
    assert column_name(26) == "Z"
    assert column_name(27) == "AA"
    assert column_name(52) == "AZ"
    assert column_name(703) == "AAA"
    for index in (1, 26, 27, 52, 703, 1000):
        assert column_index(column_name(index)) == index


def test_a_pointer_token_containing_a_slash_round_trips():
    """Escaping `/` before `~` would mangle `~1` into `~01`."""
    assert escape_token("a/b") == "a~1b"
    assert escape_token("a~b") == "a~0b"
    assert unescape_token(escape_token("a/~b")) == "a/~b"
    assert at_pointer("file:///x", ["deps", "a/b"]).path == "/deps/a~1b"


def test_a_locator_becomes_a_source_location_so_evidence_is_unchanged():
    """Cell precision extends the existing provenance chain rather than forking
    one."""
    from slpie.capture import at_cell

    location = at_cell("file:///r/x.xlsx", "Sheet1", 2, 12).to_location()

    assert location.uri == "file:///r/x.xlsx"
    assert location.span == "Sheet1!B12"


# --- unread is never empty ----------------------------------------------


def test_a_scanned_pdf_reports_that_it_was_unread_rather_than_empty():
    """A reviewer scanning for a licence clause would otherwise conclude it was
    absent rather than unread."""
    scanned = b"%PDF-1.4\n1 0 obj\n<</Type /Page>>\nendobj\n%%EOF"
    _format, document = parse(scanned, "file:///r/scan.pdf", depth=Depth.MODEL)

    assert not document.complete
    assert document.unreadable
    assert "unread, not empty" in document.unreadable[0]


def test_a_pdf_with_extractable_text_is_addressed_by_page_and_span():
    pdf = (
        b"%PDF-1.4\n1 0 obj\n<</Type /Page>>\nendobj\n"
        b"2 0 obj\n<<>>\nstream\nBT (Licence: MIT) Tj ET\nendstream\nendobj\n%%EOF"
    )
    _format, document = parse(pdf, "file:///r/contract.pdf", depth=Depth.MODEL)

    assert "Licence: MIT" in document.text
    assert "page=1" in document.blocks[0].locator.reference


def test_an_encrypted_pdf_says_so_rather_than_returning_nothing():
    encrypted = b"%PDF-1.4\n/Encrypt 5 0 R\ntrailer\n%%EOF"
    _format, document = parse(encrypted, "file:///r/sealed.pdf", depth=Depth.MODEL)

    assert not document.complete
    assert "encrypted" in document.unreadable[0]


def test_a_video_is_identified_and_its_content_stated_as_unheard():
    """A repository holding forty minutes of design discussion, unindexed, is a
    fact worth surfacing. Rendering it as an empty document hides it."""
    _format, document = parse(wav(2.0), "file:///r/standup.wav", depth=Depth.MODEL)

    assert _format.name == "wav"
    assert _format.media
    assert not document.complete
    assert "not transcribed" in document.unreadable[0]
    assert "s" in document.blocks[0].attributes["duration"]


def test_media_metadata_is_read_without_decoding_a_frame():
    from slpie.capture.media import sniff

    found = sniff(wav(3.0))
    assert found is not None
    assert found.sample_rate == 44100
    assert found.channels == 2
    assert 2.5 < found.seconds < 3.5


def test_the_duration_is_what_makes_the_missing_transcript_actionable():
    """"45 minutes unheard" is a decision somebody can make; "one video file" is
    not."""
    from slpie.capture.media import sniff

    assert sniff(wav(90.0)).duration.endswith("s")
    assert "m" in sniff(wav(90.0)).duration


def test_a_riff_container_is_resolved_to_wav_rather_than_left_generic():
    assert identify(wav(0.1), "file:///r/a.wav").name == "wav"


def test_a_format_with_no_reader_says_so_rather_than_modelling_nothing():
    _format, document = parse(
        b"\x7fELF\x02\x01\x01" + b"\x00" * 64, "file:///r/binary",
        depth=Depth.MODEL,
    )

    assert not document.complete
    assert "no reader" in document.unreadable[0]


def test_coverage_is_never_rounded_up_to_complete():
    _format, document = parse(
        b"%PDF-1.4\n1 0 obj\n<</Type /Page>>\nendobj\n%%EOF",
        "file:///r/scan.pdf", depth=Depth.MODEL,
    )
    assert document.coverage < 1.0


# --- office formats share their strategies ------------------------------


def test_xlsx_docx_and_pptx_are_the_same_two_primitives():
    """One xml_tree implementation serving six formats is the reuse this design
    exists for; the alternative was six, and the fifth would have a different bug
    from the first."""
    for name in ("xlsx", "docx", "pptx"):
        assert strategies_for(name) == ("zip_container", "xml_tree")


def test_pptx_slides_are_ordered_numerically_not_lexically():
    """`slide10` sorts before `slide2` as text, which presents a deck out of
    order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<p/>")
        for number, title in ((1, "First"), (2, "Second"), (10, "Tenth")):
            archive.writestr(
                f"ppt/slides/slide{number}.xml",
                f'<sld><a:t xmlns:a="x">{title}</a:t></sld>',
            )

    _format, document = parse(buf.getvalue(), "file:///r/deck.pptx",
                              depth=Depth.MODEL)
    titles = [block.text for block in document.of_kind(BlockKind.PAGE)]

    assert titles == ["First", "Second", "Tenth"]


def test_a_zip_is_told_apart_from_another_zip_by_its_members():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("word/document.xml", "<w/>")
    assert identify(buf.getvalue(), "file:///r/a.bin").name == "docx"


def test_a_zip_that_expands_beyond_the_limit_is_refused():
    """A zip bomb is a few kilobytes on disk and gigabytes expanded."""
    from slpie.capture.strategies import StrategyError, zip_container

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big", b"\x00" * (2 * 1024 * 1024))

    with pytest.raises(StrategyError, match="refusing to decompress"):
        zip_container(buf.getvalue(), max_bytes=1024)


def test_a_csv_dialect_is_sniffed_rather_than_assumed():
    """A European CSV is semicolon separated, and reading it as comma-separated
    yields one column of garbage that looks like data."""
    _format, document = parse(
        b"name;version\nlodash;4.17.21\n", "file:///r/deps.csv",
        depth=Depth.MODEL,
    )
    assert len(document.cells()) == 4


# --- the firewall --------------------------------------------------------


def test_a_capture_matching_no_rule_is_held_not_admitted():
    """The whole point of a default is what happens when nobody thought about
    this case."""
    empty = Chain("bare")
    decision = check("file:///r/x.json", PACKAGE, empty)

    assert decision.verdict is Verdict.QUARANTINE
    assert not decision.projected
    assert "default is to hold" in decision.reason


def test_explicit_deny_wins_over_a_later_accept():
    """"Most specific wins" is how a narrow allow-rule punches a hole through a
    broad deny, and the hole is invisible in the rule list."""
    chain = Chain("ordered", (
        Rule("broad-deny", Verdict.DROP, lambda item: True, reason="everything"),
        Rule("narrow-allow", Verdict.ACCEPT, lambda item: True, reason="this one"),
    ))
    decision = check("file:///r/x.json", PACKAGE, chain)

    assert decision.verdict is Verdict.DROP
    assert decision.rule == "broad-deny"


def test_the_first_deny_wins_so_the_chain_reads_as_written():
    chain = Chain("ordered", (
        Rule("first", Verdict.REJECT, lambda item: True, reason="first"),
        Rule("second", Verdict.DROP, lambda item: True, reason="second"),
    ))
    assert check("file:///r/x.json", PACKAGE, chain).rule == "first"


def test_a_drop_tells_the_caller_nothing_but_records_everything():
    """Where the input is hostile, the reason for refusing IS the leak: naming the
    rule hands the caller the map for finding one that does not fire."""
    decision = check("file:///r/id_rsa", b"-----BEGIN RSA PRIVATE KEY-----\n")

    assert decision.verdict is Verdict.DROP
    caller = decision.caller_view()
    assert "rule" not in caller
    assert "reason" not in caller
    assert decision.reason, "the operator's view keeps the full reason"
    assert decision.to_dict()["rule"] == "private-key-material"


def test_a_reject_explains_itself_because_an_operator_needs_the_reason():
    decision = check(
        "file:///r/huge.json", b"{}", Chain("size", (
            Rule("too-big", Verdict.REJECT, lambda item: True,
                 reason="larger than the limit"),
        )),
    )
    caller = decision.caller_view()

    assert caller["rule"] == "too-big"
    assert "larger than the limit" in caller["reason"]


def test_a_mislabelled_file_is_held_rather_than_routed_on_a_guess():
    decision = check("file:///r/config.json", KUBERNETES)

    assert decision.verdict is Verdict.QUARANTINE
    assert decision.rule == "contradicts-its-name"


def test_a_clean_manifest_is_accepted_and_projected():
    decision = check("file:///r/package.json", PACKAGE)

    assert decision.verdict is Verdict.ACCEPT
    assert decision.projected


def test_a_rule_that_raises_is_loud_rather_than_silently_permissive():
    """Treating a crashing deny-rule as "did not match" is the worst possible
    failure mode for a firewall."""
    def explode(candidate):
        raise RuntimeError("the rule is broken")

    chain = Chain("broken", (
        Rule("broken-deny", Verdict.DROP, explode, reason="would have denied"),
        Rule("permissive", Verdict.ACCEPT, lambda item: True, reason="fine"),
    ))
    decision = check("file:///r/x.json", PACKAGE, chain)

    assert decision.verdict is Verdict.QUARANTINE
    assert decision.errors
    assert "decision is unknown" in decision.reason


def test_the_whole_chain_is_recorded_even_after_a_verdict_is_decided():
    """An operator asking why their file did not appear needs the evaluation, not
    the answer."""
    decision = check("file:///r/config.json", KUBERNETES)

    assert len(decision.hits) == len(default_chain())
    rendered = decision.explain()
    assert "chain:" in rendered
    assert "verdict: QUARANTINE" in rendered


def test_a_quarantined_capture_becomes_a_gap_on_the_answer():
    gap = check("file:///r/config.json", KUBERNETES).gap()

    assert gap is not None
    assert "quarantine" in gap.remediation or "held" in gap.remediation


def test_an_accepted_capture_raises_no_gap():
    assert check("file:///r/package.json", PACKAGE).gap() is None


def test_the_chain_reports_the_deepest_parse_any_rule_needs():
    """So a chain never asks for a MODEL parse of every file when its rules only
    test the format name."""
    assert default_chain().depth <= Depth.STRUCTURE


def test_something_too_large_is_rejected_rather_than_read():
    found = identify(PACKAGE, "file:///r/x.json")
    decision = default_chain().evaluate(Candidate(
        uri="file:///r/x.json", size=MAX_CAPTURE + 1, format=found,
    ))

    assert decision.verdict is Verdict.REJECT
    assert "manifest" in decision.reason


# --- as verbs ------------------------------------------------------------


def test_capture_composes_into_the_same_pipeline_as_everything_else(tmp_path: Path):
    from slpie.compose import Composition, Context, Kind

    (tmp_path / "package.json").write_text(PACKAGE.decode(), encoding="utf-8")
    (tmp_path / "config.json").write_text(KUBERNETES.decode(), encoding="utf-8")

    result = Composition.read(f"capture {tmp_path} | quarantine").run(
        Context(root=str(tmp_path)),
    )

    assert result.ok
    assert result.flow.kind is Kind.REPORT
    held = result.flow.value["quarantined"]
    assert any("config.json" in item["uri"] for item in held)


def test_the_capture_verbs_are_documented_like_every_other_verb():
    from slpie.compose import registry
    from slpie.manual import page_for

    verbs = registry()
    for name in ("capture", "quarantine", "chain"):
        page = page_for(name, verbs=verbs)
        assert page.verb.summary
        assert page.verb.examples


def test_the_readable_format_list_is_honest_about_coverage():
    """The coverage boundary is stated rather than discovered."""
    readable = readable_formats()

    for name in ("json", "xml", "html", "csv", "xlsx", "docx", "pptx", "pdf"):
        assert name in readable
    assert "mp4" in readable, "identified and metadata-modelled, content unread"
