"""Email Reply-All: audiences keyed by the inbound Message-ID, never by sender (#82543 / withdrawn #91721)."""

import asyncio
import email
import os
import smtplib
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import _thread_metadata_for_source
from gateway.session import build_session_key
from plugins.platforms.email.adapter import EmailAdapter, _reply_all_recipients

ENV = {"EMAIL_ADDRESS": "Seba@Grupa36.pl", "EMAIL_PASSWORD": "secret", "EMAIL_IMAP_HOST": "imap.test.com",
       "EMAIL_SMTP_HOST": "smtp.test.com", "EMAIL_ALLOW_ALL_USERS": "true", "EMAIL_TRUST_FROM_HEADER": "true"}


@pytest.fixture
def adapter():
    EmailAdapter._reply_audiences.clear()
    EmailAdapter._thread_latest.clear()
    with patch.dict(os.environ, ENV):
        a = EmailAdapter(PlatformConfig(enabled=True))
        a.handle_message = MagicMock(side_effect=lambda event: asyncio.sleep(0))
        yield a
    EmailAdapter._reply_audiences.clear()
    EmailAdapter._thread_latest.clear()


def _inbound(a, message_id, to, cc="", sender="alice@test.com", subject="Plans", references="", in_reply_to="", body="hi"):
    asyncio.run(a._dispatch_message({
        "uid": b"1", "sender_addr": sender, "sender_name": "Alice", "subject": subject, "message_id": message_id,
        "in_reply_to": in_reply_to, "references": references, "to": [to], "cc": [cc] if cc else [], "body": body,
        "attachments": [], "date": "", "sender_authenticated": True, "auth_reason": "dmarc=pass"}))
    return build_session_key(a.handle_message.call_args[0][0].source) if a.handle_message.called else None


def _sent(fn, *args, **kwargs):
    with patch("smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value = server
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return server.send_message.call_args[0][0]


def test_recipients_keep_to_and_cc_drop_self_and_dedupe():
    to, cc = _reply_all_recipients(["Seba <seba@grupa36.pl>, Bob <bob@test.com>, ALICE@test.com"],
                                   ["SEBA@grupa36.pl, Carol <carol@test.com>, bob@TEST.com, undisclosed-recipients:;"],
                                   "alice@test.com", "Seba@Grupa36.pl")
    assert (to, cc) == (["alice@test.com", "bob@test.com"], ["carol@test.com"])


def test_reply_goes_to_sender_plus_original_to_and_cc(adapter):
    _inbound(adapter, "<a1@test.com>", "Bob <bob@test.com>", "Seba <seba@grupa36.pl>, carol@test.com")
    msg = _sent(adapter.send, "alice@test.com", "answer", reply_to="<a1@test.com>")
    assert msg["To"] == "alice@test.com, bob@test.com"
    assert msg["Cc"] == "carol@test.com"
    assert msg["Bcc"] is None
    assert msg["In-Reply-To"] == "<a1@test.com>" and msg["Subject"] == "Re: Plans"


def test_one_to_one_mail_goes_to_sender_only(adapter):
    _inbound(adapter, "<a1@test.com>", "seba@grupa36.pl")
    msg = _sent(adapter.send, "alice@test.com", "answer", reply_to="<a1@test.com>")
    assert msg["To"] == "alice@test.com" and msg["Cc"] is None


def test_two_threads_from_one_sender_never_share_audiences(adapter):
    _inbound(adapter, "<a@test.com>", "x@test.com", "seba@grupa36.pl", subject="Thread A")
    _inbound(adapter, "<b@test.com>", "seba@grupa36.pl", "y@test.com", subject="Thread B")
    a = _sent(adapter.send, "alice@test.com", "to A", reply_to="<a@test.com>")
    b = _sent(adapter.send, "alice@test.com", "to B", reply_to="<b@test.com>")
    assert (a["To"], a["Cc"], a["Subject"]) == ("alice@test.com, x@test.com", None, "Re: Thread A")
    assert (b["To"], b["Cc"], b["Subject"]) == ("alice@test.com", "y@test.com", "Re: Thread B")


@pytest.mark.parametrize("reply_to", [None, "<unknown@test.com>"])
def test_missing_or_unknown_reply_to_fails_closed_to_sender(adapter, reply_to):
    _inbound(adapter, "<a1@test.com>", "bob@test.com", "carol@test.com")
    msg = _sent(adapter.send, "alice@test.com", "answer", reply_to=reply_to)
    assert msg["To"] == "alice@test.com" and msg["Cc"] is None


def test_audience_is_bound_to_its_sender(adapter):
    _inbound(adapter, "<a1@test.com>", "bob@test.com", "carol@test.com")
    msg = _sent(adapter.send, "mallory@test.com", "answer", reply_to="<a1@test.com>")
    assert msg["To"] == "mallory@test.com" and msg["Cc"] is None


def test_audience_survives_adapter_replacement_and_is_capped(adapter):
    _inbound(adapter, "<a1@test.com>", "bob@test.com")
    with patch.dict(os.environ, ENV):
        fresh = EmailAdapter(PlatformConfig(enabled=True))
    assert _sent(fresh.send, "alice@test.com", "x", reply_to="<a1@test.com>")["To"] == "alice@test.com, bob@test.com"
    with patch("plugins.platforms.email.adapter._REPLY_AUDIENCES_MAX", 2):
        for i in range(3):
            _inbound(adapter, f"<n{i}@test.com>", "bob@test.com")
    assert list(adapter._audiences) == ["<n1@test.com>", "<n2@test.com>"]


def test_media_sends_use_the_metadata_anchor_audience(adapter):
    _inbound(adapter, "<a@test.com>", "x@test.com", "seba@grupa36.pl")
    _inbound(adapter, "<b@test.com>", "bob@test.com", "carol@test.com")
    source = SimpleNamespace(platform=Platform.EMAIL, thread_id=None, message_id="<b@test.com>")
    metadata = _thread_metadata_for_source(source, "<b@test.com>")
    assert metadata == {"email_reply_to_message_id": "<b@test.com>"}
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG")
    try:
        doc = _sent(adapter.send_document, "alice@test.com", f.name, metadata=metadata)
        imgs = _sent(adapter.send_multiple_images, "alice@test.com", [(f"file://{f.name}", "")], metadata=metadata)
    finally:
        os.unlink(f.name)
    for msg in (doc, imgs):
        assert (msg["To"], msg["Cc"], msg["In-Reply-To"]) == ("alice@test.com, bob@test.com", "carol@test.com", "<b@test.com>")
    assert _sent(adapter.send_document, "alice@test.com", __file__)["Cc"] is None  # no anchor: sender only


def test_smtp_envelope_includes_cc(adapter):
    _inbound(adapter, "<a1@test.com>", "bob@test.com", "carol@test.com")
    msg = _sent(adapter.send, "alice@test.com", "answer", reply_to="<a1@test.com>")
    conn = MagicMock()
    conn.has_extn.return_value = False
    smtplib.SMTP.send_message(conn, msg)
    assert conn.sendmail.call_args[0][1] == ["alice@test.com", "bob@test.com", "carol@test.com"]


def test_one_session_per_mail_thread(adapter):
    key_a = _inbound(adapter, "<a@test.com>", "x@test.com")
    key_b = _inbound(adapter, "<b@test.com>", "y@test.com")
    assert key_a != key_b and "alice@test.com" not in key_a
    reply = _sent(adapter.send, "alice@test.com", "to A", reply_to="<a@test.com>")
    assert (reply["In-Reply-To"], reply["References"]) == ("<a@test.com>", "<a@test.com>")
    # Alice answers Seba's reply: References carries the root plus Seba's own Message-ID.
    chain = f"<a@test.com> {reply['Message-ID']}"
    assert _inbound(adapter, "<a2@test.com>", "x@test.com", references=chain, in_reply_to=reply["Message-ID"]) == key_a
    second = _sent(adapter.send, "alice@test.com", "again", reply_to="<a2@test.com>")
    assert second["References"] == f"{chain} <a2@test.com>" and second["In-Reply-To"] == "<a2@test.com>"
    assert _inbound(adapter, "<a3@test.com>", "x@test.com", in_reply_to="<a@test.com>") == key_a  # no References


def test_shared_thread_across_senders_and_anchor_routing(adapter):
    root = _inbound(adapter, "<root@test.com>", "seba@grupa36.pl, bob@test.com")
    source = adapter.handle_message.call_args[0][0].source
    assert source.chat_type == "group" and source.user_id == "alice@test.com"
    assert _inbound(adapter, "<bob@test.com>", "seba@grupa36.pl, carol@test.com",
                    sender="bob@test.com", references="<root@test.com>") == root
    assert build_session_key(adapter.handle_message.call_args[0][0].source, group_sessions_per_user=False) == (
        build_session_key(source, group_sessions_per_user=False))
    assert adapter.handle_message.call_args[0][0].source.user_id == "bob@test.com"
    assert _inbound(adapter, "<other@test.com>", "seba@grupa36.pl", sender="bob@test.com") != root
    first = _sent(adapter.send, source.chat_id, "answer", reply_to="<root@test.com>")
    latest = _sent(adapter.send, source.chat_id, "answer")
    assert (first["To"], first["In-Reply-To"]) == ("alice@test.com, bob@test.com", "<root@test.com>")
    assert (latest["To"], latest["Cc"], latest["In-Reply-To"]) == (
        "bob@test.com, carol@test.com", None, "<bob@test.com>")
    wrong_anchor = _sent(adapter.send, source.chat_id, "answer", reply_to="<other@test.com>")
    assert (wrong_anchor["To"], wrong_anchor["In-Reply-To"]) == (
        "bob@test.com, carol@test.com", "<bob@test.com>")
    assert _sent(adapter.send, "alice@test.com", "proactive")["To"] == "alice@test.com"
    failed = asyncio.run(adapter.send("mail-unknown", "x"))
    assert not failed.success and "Unknown email thread" in failed.error
    assert not asyncio.run(adapter.send_document("mail-unknown", __file__)).success


@pytest.mark.parametrize("to,cc,body,enabled,expected", [
    ("seba@grupa36.pl", "", "hello", True, True),
    ("alice@test.com", "seba@grupa36.pl", "hello", True, False),
    ("alice@test.com", "seba@grupa36.pl", "@Seba, please help", True, True),
    ("alice@test.com", "seba@grupa36.pl", "hello\nOn Monday Alice wrote:\nSeba help", True, False),
    ("alice@test.com", "seba@grupa36.pl", "Sebastian is here", True, False),
    ("alice@test.com", "seba@grupa36.pl", "hello", False, True),
])
def test_only_addressed_mail_dispatches(to, cc, body, enabled, expected):
    EmailAdapter._reply_audiences.clear()
    EmailAdapter._thread_latest.clear()
    with patch.dict(os.environ, {**ENV, "EMAIL_MENTION_NAMES": "Seba" if enabled else ""}):
        a = EmailAdapter(PlatformConfig(enabled=True))
        a.handle_message = MagicMock(side_effect=lambda event: asyncio.sleep(0))
        _inbound(a, "<mention@test.com>", to, cc, body=body)
        assert a.handle_message.called is expected


def test_html_gmail_quote_is_not_a_mention():
    with patch.dict(os.environ, {**ENV, "EMAIL_MENTION_NAMES": "Seba"}):
        a = EmailAdapter(PlatformConfig(enabled=True))
        a.handle_message = MagicMock(side_effect=lambda event: asyncio.sleep(0))
        msg = email.message_from_string(
            'From: Alice <alice@test.com>\nTo: alice@test.com\nCc: seba@grupa36.pl\n'
            'Message-ID: <html@test.com>\nContent-Type: text/html; charset=utf-8\n\n'
            '<p>hello</p><div class="gmail_quote"><p>Seba help</p></div>')
        data = a._parse_fetched_message(b"1", msg.as_bytes())
        assert "Seba" in data["body"] and "Seba" not in data["mention_body"]
        asyncio.run(a._dispatch_message(data))
        assert not a.handle_message.called


def test_mail_without_ids_keeps_the_per_sender_session(adapter):
    _inbound(adapter, "", "x@test.com")
    assert adapter.handle_message.call_args[0][0].source.thread_id is None
    assert _sent(adapter.send, "alice@test.com", "x", reply_to="<unknown@test.com>")["References"] == "<unknown@test.com>"


@pytest.mark.parametrize(("body", "new_text"), [
    ("Seba, zerknij\n\nW dniu pt., 25.09.2026 o 12:00 Jan <j@corp.example>\nnapisał(a):\n> Seba cos", "Seba, zerknij"),
    ("hej\nOn Fri, Sep 25, 2026 at 12:00 PM Seba <s@corp.example> wrote:\n> x", "hej"),
    ("On second thought, Seba check it", "On second thought, Seba check it"),
    ("ok\n\nOd: Seba <s@corp.example>\nWysłano: pt.", "ok"),
    ("Jan napisał(a):\n> Seba", ""),
])
def test_new_mail_text_stops_at_quoted_history(body, new_text):
    from plugins.platforms.email.adapter import _new_mail_text
    assert _new_mail_text(body).strip() == new_text
