"""Tests for notifier.py — Telegram notification dispatcher"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import patch

from engine_simple.notifier import Notifier


def test_init_disabled_without_tokens():
    with patch.dict(os.environ, {}, clear=True):
        n = Notifier()
        assert not n.is_enabled()


def test_init_enabled_with_tokens():
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:ABC",
                                  "TELEGRAM_CHAT_ID": "456"}):
        n = Notifier()
        assert n.is_enabled()


def test_send_when_disabled():
    n = Notifier()
    n._enabled = False
    n.send("test message")
    assert not n._enabled  # still disabled after send


def test_send_when_enabled():
    n = Notifier()
    n._enabled = True
    n.telegram_token = "123:ABC"
    n.telegram_chat_id = "456"
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        n.send("test")
        mock_post.assert_called_once()


def test_send_truncates_long_messages():
    n = Notifier()
    n._enabled = True
    n.telegram_token = "tok"
    n.telegram_chat_id = "id"
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        long_msg = "x" * 5000
        n.send(long_msg)
        sent_text = mock_post.call_args[1]["json"]["text"]
        assert len(sent_text) == 4000


def test_send_handles_api_error():
    n = Notifier()
    n._enabled = True
    n.telegram_token = "tok"
    n.telegram_chat_id = "id"
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 403
        n.send("test")
        mock_post.assert_called_once()  # api was called despite 403 response
