"""Tests for parse_lc_response — the LLM loot-council response parser.

Regression suite for the bug where model analysis text leaked into the
S1/S2/S3 player-name fields shown in the results card.
"""

from wowlc.services.lc_processor import parse_lc_response

CANDIDATES = ["Akhan", "Ignatize", "Kype", "Mckracken", "Thrall", "Jaina"]


def test_well_formed_response() -> None:
    response = (
        "Suggestion 1: Thrall\n"
        "Suggestion 2: Jaina\n"
        "Suggestion 3: None\n"
        "Rationale: Thrall is highest on the wishlist per Rule 1."
    )
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == "Jaina"
    assert parsed["suggestion_3"] == "None"
    assert parsed["rationale"] == "Thrall is highest on the wishlist per Rule 1."


def test_bug_report_response() -> None:
    """The exact failure mode from the bug report: markdown-bolded labels with
    inline analysis. S2 must rescue the single named candidate; S3 weighs two
    candidates with no reliable conclusion, so it must be blank — never the
    leaked analysis text."""
    response = (
        "Suggestion 1: Akhan\n"
        "**Suggestion 2 (between Ignatize and remaining):** Ignatize is next on "
        "wishlist (#1), ahead of the #13/#14/#20 group.\n"
        "**Suggestion 3:** Remaining candidates with wishlist #13–#14 are Kype and "
        "Mckracken. Both have 0 items won recently. Kype has a slightly longer wait "
        "(112 days vs. Never for Mckracken — \"Never\" beats 112 days per Rule 4). "
        "However, Rule 2 first: both have 0 items won. Rule 3 (parses): Mckracken "
        "median 85.3 vs. Kype median 86.0 — very close, Kype slightly higher median. "
        "Rule 4: Mckracken \"Never\" > Kype 112 days, giving Mckracken the edge.\n"
        "Rationale: Akhan is next per Rule 1."
    )
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Akhan"
    assert parsed["suggestion_2"] == "Ignatize"
    assert parsed["suggestion_3"] == ""
    assert parsed["rationale"] == "Akhan is next per Rule 1."


def test_markdown_wrapped_names() -> None:
    response = (
        "Suggestion 1: **Thrall**\n"
        "Suggestion 2: [Jaina]\n"
        "Suggestion 3: `None`\n"
        "Rationale: wishlist order."
    )
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == "Jaina"
    assert parsed["suggestion_3"] == "None"


def test_bolded_and_bulleted_labels() -> None:
    response = (
        "- **Suggestion 1:** Thrall\n"
        "- **Suggestion 2:** None\n"
        "- **Suggestion 3:** None\n"
        "**Rationale:** Rule 2 applies."
    )
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == "None"
    assert parsed["suggestion_3"] == "None"
    assert parsed["rationale"] == "Rule 2 applies."


def test_missing_colon() -> None:
    response = "Suggestion 1 - Thrall\nSuggestion 2 Jaina\nSuggestion 3 None"
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == "Jaina"
    assert parsed["suggestion_3"] == "None"


def test_name_with_trailing_commentary() -> None:
    response = (
        "Suggestion 1: Thrall (best attendance, mainspec, Rule 2)\n"
        "Suggestion 2: Jaina - longest wait for this slot\n"
        "Suggestion 3: None\n"
        "Rationale: Rule 2."
    )
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == "Jaina"
    assert parsed["suggestion_3"] == "None"


def test_none_variants() -> None:
    for none_value in ["None", "none", "N/A", "n/a", "None (only two eligible candidates)"]:
        response = f"Suggestion 1: Thrall\nSuggestion 2: Jaina\nSuggestion 3: {none_value}"
        parsed = parse_lc_response(response, CANDIDATES)
        assert parsed["suggestion_3"] == "None", f"failed for {none_value!r}"


def test_label_mentioned_in_preamble() -> None:
    """A mid-sentence mention of 'Suggestion 1' must not shadow the real line."""
    response = (
        "Suggestion 1 was difficult to decide between Kype and Mckracken.\n"
        "Suggestion 1: Thrall\n"
        "Suggestion 2: None\n"
        "Suggestion 3: None"
    )
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"


def test_substring_candidate_names() -> None:
    candidates = ["Anna", "Annabel"]
    parsed = parse_lc_response("Suggestion 1: Annabel", candidates)
    assert parsed["suggestion_1"] == "Annabel"
    parsed = parse_lc_response("Suggestion 1: Annabel is ahead of Anna here", candidates)
    assert parsed["suggestion_1"] == "Annabel"
    parsed = parse_lc_response("Suggestion 1: Anna", candidates)
    assert parsed["suggestion_1"] == "Anna"


def test_accented_names() -> None:
    candidates = ["Órenna", "Thrall"]
    parsed = parse_lc_response("Suggestion 1: Órenna", candidates)
    assert parsed["suggestion_1"] == "Órenna"


def test_canonical_casing_returned() -> None:
    parsed = parse_lc_response("Suggestion 1: THRALL\nSuggestion 2: jaina", CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == "Jaina"


def test_name_on_next_line() -> None:
    response = "Suggestion 1:\nThrall\nSuggestion 2: None\nSuggestion 3: None"
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"


def test_run_on_single_line() -> None:
    response = "Suggestion 1: Thrall Suggestion 2: Jaina Suggestion 3: None Rationale: wishlist."
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == "Jaina"
    assert parsed["suggestion_3"] == "None"
    assert parsed["rationale"] == "wishlist."


def test_analysis_only_garbage_yields_blank() -> None:
    """A suggestion line naming no candidate must give '', never leaked text."""
    response = (
        "Suggestion 1: the tank with the best parses this tier\n"
        "Suggestion 2: whoever attended most raids\n"
        "Suggestion 3: None"
    )
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == ""
    assert parsed["suggestion_2"] == ""
    assert parsed["suggestion_3"] == "None"


def test_truncated_response() -> None:
    """max_tokens can cut a response mid-name: partial names must not leak."""
    response = "Suggestion 1: Thrall\nSuggestion 2: Jai"
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["suggestion_1"] == "Thrall"
    assert parsed["suggestion_2"] == ""
    assert parsed["suggestion_3"] == ""
    assert parsed["rationale"] == ""


def test_markdown_rationale() -> None:
    response = "Suggestion 1: Thrall\n**Rationale:** *Rule 1* applies via `wishlist`."
    parsed = parse_lc_response(response, CANDIDATES)
    assert parsed["rationale"] == "Rule 1 applies via wishlist."


def test_empty_candidate_list_best_effort() -> None:
    """Without a roster (not reachable in the normal flow) the parser falls
    back to returning the cleaned line rather than validating."""
    parsed = parse_lc_response("Suggestion 1: Thrall", [])
    assert parsed["suggestion_1"] == "Thrall"


def test_empty_and_none_response() -> None:
    for response in ["", None]:
        parsed = parse_lc_response(response, CANDIDATES)
        assert parsed == {
            "suggestion_1": "",
            "suggestion_2": "",
            "suggestion_3": "",
            "rationale": "",
        }
