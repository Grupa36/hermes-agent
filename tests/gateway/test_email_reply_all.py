"""Email Reply-All: audiences keyed by the inbound Message-ID, never by sender (#82543 / withdrawn #91721)."""

import asyncio
import os
import smtplib
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import _thread_metadata_for_source
from plugins.platforms.email.adapter import EmailAdapter, _reply_all_recipients

ENV = {"EMAIL_ADDRESS": "Seba@Grupa36.pl", "EMAIL_PASSWORD": "secret", "EMAIL_IMAP_HOST": "imap.test.com",
       "EMAIL_SMTP_HOST": "smtp.test.com", "EMAIL_ALLOW_ALL_USERS": "true", "EMAIL_TRUST_FROM_HEADER": "true"}


@pytest.fixture
def adapter():
    EmailAdapter._reply_audiences.clear()
    with patch.dict(os.environ, ENV):
        a = EmailAdapter(PlatformConfig(enabled=True))
        a.handle_message = MagicMock(side_effect=lambda event: asyncio.sleep(0))
        yield a
    EmailAdapter._reply_audiences.clear()


def _inbound(a, message_id, to, cc="", sender="alice@test.com", subject="Plans"):
    asyncio.run(a._dispatch_message({
        "uid": b"1", "sender_addr": sender, "sender_name": "Alice", "subject": subject, "message_id": message_id,
        "in_reply_to": "", "to": [to], "cc": [cc] if cc else [], "body": "hi", "attachments": [], "date": "",
        "sender_authenticated": True, "auth_reason": "dmarc=pass"}))


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
