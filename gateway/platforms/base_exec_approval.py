"""Shared wording for the exec-approval prompt every messaging surface renders.

The button card (``BasePlatformAdapter._format_exec_approval``) and the plain-text
``/approve`` fallback (``gateway.run._format_exec_approval_fallback``) must tell the user
the same three things: what Hermes wants to run, why it was flagged, and that silence means
the command does NOT run once ``approvals.timeout`` elapses. Keeping the text here means one
edit changes every platform; adapters only wrap these strings in their own markup.

No imports from ``gateway.platforms.base`` or ``gateway.run`` — both import this module.
"""

from __future__ import annotations

from agent.i18n import t

# Bare strings; adapters add their own bold/HTML around them.
EA_HEADER_TEXT = "Hermes wants to run a command that needs your OK"
EA_REASON_LABEL_TEXT = "Why it was flagged"

# Timeout notice posted when nobody answered the prompt (``{window}`` = "5 minutes").
APPROVAL_TIMED_OUT_NOTICE = (
    "⌛ Approval timed out after {window} — the command was NOT run. "
    "Ask me to try again if you still want it, or raise approvals.timeout in config.yaml.")


def ea_header_text() -> str:
    """``EA_HEADER_TEXT`` in the active language (the constants stay English for import-time users)."""
    return t("g36.platform_base_exec_approval.header_text")


def ea_reason_label_text() -> str:
    return t("g36.platform_base_exec_approval.reason_label_text")


def approval_timeout_seconds() -> int:
    """The configured ``approvals.timeout`` (default 300s); module attribute so tests can pin it."""
    from tools.approval_context import _get_approval_timeout
    return _get_approval_timeout()


def format_approval_window(seconds: int) -> str:
    """Human wording for a timeout (300 → "5 minutes"); one formatter shared with the CLI notice and
    the tool result's ``user_summary`` — see ``tools.approval_context.format_approval_window``."""
    from tools.approval_context import format_approval_window as _shared
    english = _shared(seconds)
    # fork: localized unit words; "few" is the Slavic 2-4 plural (same text as "many" in English).
    count, unit = english.split(" ", 1)
    n = int(count)
    form = "one" if n == 1 else ("few" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "many")
    return t(f"g36.platform_base_exec_approval.window_{unit.rstrip('s')}_{form}", count=n)


def format_approval_deadline_line(timeout_s: int) -> str:
    """The last line of every approval prompt: doing nothing is a safe no."""
    return t("g36.platform_base_exec_approval.deadline_line", window=format_approval_window(timeout_s))


def format_approval_timed_out_notice(timeout_s: int) -> str:
    return t("g36.platform_base_exec_approval.timed_out_notice", window=format_approval_window(timeout_s))
