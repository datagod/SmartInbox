"""Mail provider deep-link tests."""

from __future__ import annotations

from smartinbox.mail_source_url import build_mail_source_url, normalize_rfc822_message_id


def test_normalize_rfc822_message_id_strips_brackets():
    assert normalize_rfc822_message_id("<abc@mail.gmail.com>") == "abc@mail.gmail.com"


def test_gmail_url_uses_rfc822msgid_search():
    url = build_mail_source_url(
        provider="gmail",
        account_email="william.mcevoy@gmail.com",
        rfc822_message_id="<CABcd@mail.gmail.com>",
        subject="Hello",
    )
    assert url is not None
    assert "mail.google.com" in url
    assert "authuser=william.mcevoy%40gmail.com" in url
    assert "rfc822msgid%3ACABcd%40mail.gmail.com" in url


def test_gmail_url_falls_back_to_subject_search():
    url = build_mail_source_url(
        provider="gmail",
        account_email="user@gmail.com",
        subject='Re: "Meeting"',
    )
    assert url is not None
    assert "subject%3A" in url


def test_proton_url_uses_message_id_search():
    url = build_mail_source_url(
        provider="proton",
        account_email="user@proton.me",
        rfc822_message_id="<id@proton.me>",
    )
    assert url == "https://mail.proton.me/u/0/search?keyword=id%40proton.me"


def test_unknown_provider_returns_none():
    assert (
        build_mail_source_url(
            provider="yahoo",
            account_email="a@yahoo.com",
            subject="Hi",
        )
        is None
    )