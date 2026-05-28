"""Wave 9 — sentence-level identity redaction + removal of the canned
identity short-circuit.

Two related fixes for the live-traffic conversation review:

1. `response_safety.IDENTITY_RESPONSE_TEMPLATE` (the canned
   "I am here on behalf of <company>. I can help you with our products,
   services, orders, support, or general queries." line) used to replace
   any LLM reply that mentioned "AI" / "LLM" / "Gemini". This commit
   removes the template entirely and rewrites the sanitiser to drop the
   *sentence* containing the disclosure, preserving the surrounding
   reasoning.

2. `response_generator.generate_combined_ai_analysis` had a
   `_looks_like_identity_question` short-circuit that bypassed the LLM
   and returned the canned line from above. That branch + the
   `_identity_response_payload` helper are gone — identity questions
   now flow through the normal LLM path and the (now redaction-based)
   sanitiser cleans up any disclosure the model emits.

These two changes together eliminate the "I am here on behalf of this
business" / "I am here on behalf of the business" responses the user
reported in the live transcript review.
"""

from __future__ import annotations

import pytest

from services.ai_service import response_generator, response_safety
from services.ai_service.response_safety import (
    redact_identity_disclosure,
    sanitize_ai_response_for_delivery,
)


# ---------------------------------------------------------------------------
# Canned template + short-circuit removal — regression guards.
# ---------------------------------------------------------------------------


def test_canned_identity_template_is_gone():
    """The 'I am here on behalf of {company_name}' template was the source
    of the robotic responses in production. Re-introducing it would be a
    regression — guard against that explicitly."""
    assert not hasattr(response_safety, "IDENTITY_RESPONSE_TEMPLATE"), (
        "IDENTITY_RESPONSE_TEMPLATE was removed in Wave 9; do not bring it back."
    )
    assert not hasattr(response_safety, "company_representative_response"), (
        "company_representative_response was removed in Wave 9; do not bring it back."
    )


def test_identity_question_short_circuit_is_gone():
    """generate_combined_ai_analysis no longer bypasses the LLM when the
    user asks an identity question — the canned 'I am here on behalf of'
    response was unacceptable in production."""
    assert not hasattr(response_generator, "_identity_response_payload"), (
        "_identity_response_payload was removed in Wave 9; the canned identity "
        "payload it built short-circuited the LLM for any 'are you an AI' "
        "style question and produced robotic replies."
    )


# ---------------------------------------------------------------------------
# Sentence-level redactor behaviour.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The disclosure sentence is dropped; the substantive answer survives.
        (
            "I am an AI assistant. Our Pulse Starter plan starts at 99 USD per month.",
            "Our Pulse Starter plan starts at 99 USD per month.",
        ),
        (
            "Our Pulse Starter plan starts at 99 USD per month. I am powered by Gemini.",
            "Our Pulse Starter plan starts at 99 USD per month.",
        ),
        # "I am here to help" — no provider name, no LLM token, leave it alone.
        (
            "I am here to help you find the right product.",
            "I am here to help you find the right product.",
        ),
        # Pure business reply — leave untouched.
        (
            "Welcome! Our flagship Pulse Engine is built for high-volume teams.",
            "Welcome! Our flagship Pulse Engine is built for high-volume teams.",
        ),
        # Disclosure mid-sentence => whole sentence dropped.
        (
            "I am Gemini, your shopping assistant. Pulse Engine starts at 99 USD.",
            "Pulse Engine starts at 99 USD.",
        ),
    ],
)
def test_redactor_drops_disclosure_sentences_keeps_rest(raw, expected):
    assert redact_identity_disclosure(raw) == expected


def test_sanitizer_redaction_path_does_not_use_canned_template():
    """The sanitiser must no longer surface the canned 'I am here on behalf
    of' line under any input. It either returns the model's text (cleaned),
    an empty string when the whole reply was a disclosure, or the strict
    safety fallback when an unsafe pattern matched."""
    cases = [
        "I am an AI assistant.",  # pure disclosure
        "I am here to help you.",  # no disclosure
        "Pulse Engine ships in 3 days.",  # business reply
        "I am powered by Gemini. Pulse Engine ships in 3 days.",  # mixed
    ]
    for raw in cases:
        out, blocked, issues = sanitize_ai_response_for_delivery(
            raw, company_name="Pulse Engine Inc."
        )
        assert "I am here on behalf of" not in out
        assert "I can help you with our products, services, orders, support" not in out


def test_sanitizer_returns_empty_when_entire_reply_was_disclosure():
    out, blocked, issues = sanitize_ai_response_for_delivery(
        "I am an AI assistant.",
        company_name="Pulse Engine Inc.",
    )
    assert out == ""
    assert blocked is False
    assert "identity_disclosure_redacted_to_empty" in issues


def test_sanitizer_passes_through_clean_text():
    out, blocked, issues = sanitize_ai_response_for_delivery(
        "Our Pulse Engine starts at 99 USD per month.",
    )
    assert out == "Our Pulse Engine starts at 99 USD per month."
    assert blocked is False
    assert issues == []


def test_sanitizer_still_blocks_unsafe_content():
    """The safety pattern check (kill / weapons / credit card / password)
    still produces the strict SAFE_RESPONSE_FALLBACK and blocked=True."""
    # Safety pattern is `\b(bomb|weapon|exploit|hack)\s+(make|build|create)\b`
    # — requires the dangerous noun before the action verb.
    out, blocked, issues = sanitize_ai_response_for_delivery(
        "Please bomb build instructions for an explosive device.",
    )
    assert blocked is True
    assert out == response_safety.SAFE_RESPONSE_FALLBACK
    assert issues  # at least one safety issue reported
