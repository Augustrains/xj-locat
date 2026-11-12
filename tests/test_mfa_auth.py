import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import mfa_auth as mfa
import sport_bot as bot
from scripts import github_auth


def response(body):
    result = Mock()
    result.json.return_value = body
    return result


class MfaProtocolTests(unittest.TestCase):
    def session(self, server="https://login.xjtu.edu.cn/attest", contact="138****0000"):
        session = Mock()
        session.get.return_value = response({"code": 0, "data": {
            "attestServerUrl": server, "gid": "private-gid", "securePhone": contact,
        }})
        session.post.return_value = response({"code": 0, "data": None})
        return session

    def test_sends_once_using_school_protocol(self):
        session = self.session()
        challenge = mfa.start_challenge(session, "private-state", "securephone")
        self.assertEqual(challenge["gid"], "private-gid")
        session.get.assert_called_once_with(
            "https://login.xjtu.edu.cn/cas/mfa/initByType/securephone",
            params={"state": "private-state"}, timeout=30,
        )
        session.post.assert_called_once_with(
            "https://login.xjtu.edu.cn/attest/api/guard/securephone/send",
            json={"gid": "private-gid"}, timeout=30,
        )

    def test_unexpected_verification_server_never_receives_data(self):
        for server in ("http://login.xjtu.edu.cn", "https://login.xjtu.edu.cn.example.com", "https://user:password@login.xjtu.edu.cn"):
            with self.subTest(server=server):
                session = self.session(server)
                with self.assertRaises(mfa.MfaError):
                    mfa.start_challenge(session, "state", "securephone")
                session.post.assert_not_called()

    def test_unbound_phone_never_triggers_send(self):
        session = self.session(contact=None)
        with self.assertRaises(mfa.MfaError):
            mfa.start_challenge(session, "state", "securephone")
        session.post.assert_not_called()

    def test_only_verified_status_is_accepted(self):
        session = Mock()
        challenge = {"server": "https://login.xjtu.edu.cn/attest", "gid": "gid", "channel": "securephone"}
        for status in (0, 1, 3, 9):
            session.post.return_value = response({"code": 0, "data": {"status": status}})
            with self.subTest(status=status), self.assertRaises(mfa.MfaError):
                mfa.validate_challenge(session, challenge, "123456")
        session.post.return_value = response({"code": 0, "data": {"status": 2}})
        mfa.validate_challenge(session, challenge, "123456")

    def test_invalid_code_is_not_submitted(self):
        session = Mock()
        for code in ("123", "１２３４５６", "12x456", 123456):
            with self.subTest(code=code), self.assertRaises(mfa.MfaError):
                mfa.validate_challenge(session, {}, code)
        session.post.assert_not_called()


class ReplyEncryptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key, cls.public_key = mfa.new_reply_key()

    def test_reply_round_trip_preserves_explicit_trust_choice(self):
        encrypted = mfa.encrypt_reply(self.public_key, "43137583320", "c" * 32, "123456", True)
        self.assertEqual(len(encrypted), 344)
        self.assertNotIn("123456", encrypted)
        reply = mfa.decrypt_reply(self.private_key, encrypted, "43137583320", "c" * 32)
        self.assertEqual(reply["code"], "123456")
        self.assertIs(reply["trust_client"], True)

    def test_default_does_not_register_trusted_client(self):
        encrypted = mfa.encrypt_reply(self.public_key, "1", "c" * 32, "123456")
        self.assertIs(mfa.decrypt_reply(self.private_key, encrypted, "1", "c" * 32)["trust_client"], False)

    def test_reply_from_other_run_or_challenge_is_rejected(self):
        encrypted = mfa.encrypt_reply(self.public_key, "1", "c" * 32, "123456")
        for run_id, challenge_id in (("2", "c" * 32), ("1", "d" * 32)):
            with self.subTest(run_id=run_id), self.assertRaises(mfa.MfaError):
                mfa.decrypt_reply(self.private_key, encrypted, run_id, challenge_id)

    def test_plaintext_is_never_accepted_as_encrypted_reply(self):
        with self.assertRaises(mfa.MfaError):
            mfa.decrypt_reply(self.private_key, "123456", "1", "c" * 32)


class SameJobSessionTests(unittest.TestCase):
    def login_context(self, form):
        session = Mock()
        session.post.return_value.status_code = 302
        session.post.return_value.headers = {"Location": "https://org.xjtu.edu.cn/callback"}
        callback = Mock(url="https://ipahw.xjtu.edu.cn/sso/callback?code=temporary-code")
        token = response({"data": {"token": "verified-token"}})
        session.get.side_effect = [callback, token]
        return {"session": session, "login_post_url": "https://login.xjtu.edu.cn/cas/login",
                "cas_login_url": "https://login.xjtu.edu.cn/cas/login", "form_data": form}

    def test_trusted_client_uses_saved_explicit_consent(self):
        context = self.login_context({"trustAgent": ""})
        with patch.object(bot.Config, "TRUST_CLIENT", True):
            self.assertEqual(bot.finish_login(context), "verified-token")
        self.assertEqual(context["session"].post.call_args.kwargs["data"]["trustAgent"], "true")

    def test_one_time_no_trust_choice_overrides_saved_preference(self):
        context = self.login_context({"trustAgent": "false"})
        with patch.object(bot.Config, "TRUST_CLIENT", True):
            bot.finish_login(context)
        self.assertEqual(context["session"].post.call_args.kwargs["data"]["trustAgent"], "false")

    def test_private_session_restores_cookie_domains_and_form(self):
        session = requests.Session()
        session.cookies.set("CAS", "secret-cookie", domain="login.xjtu.edu.cn", path="/cas", secure=True)
        context = {"session": session, "form_data": {"execution": "private-form-state"},
                   "cas_login_url": "https://login.xjtu.edu.cn/cas/login", "mfa_required": True}
        temp_root = Path(__file__).resolve().parents[1] / ".generated/test-temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder, patch.object(github_auth, "PRIVATE", Path(folder) / "private.json"):
            github_auth.save_context(context, {"gid": "gid"}, "private-key", "1", "c" * 32, "login")
            restored = github_auth.load_context()
        self.assertEqual(restored["form_data"]["execution"], "private-form-state")
        cookie = next(iter(restored["session"].cookies))
        self.assertEqual((cookie.domain, cookie.path, cookie.value, cookie.secure),
                         ("login.xjtu.edu.cn", "/cas", "secret-cookie", True))
        restored["session"].close()
        session.close()

    def test_unattended_run_never_sends_a_code(self):
        session = Mock()
        with patch.object(bot, "prepare_login", return_value={"session": session, "mfa_required": True}), patch.object(github_auth, "start_challenge") as send:
            with self.assertRaises(mfa.MfaError):
                github_auth.prepare("login", False, "securephone")
        send.assert_not_called()
        session.close.assert_called_once()

    def test_verified_token_is_reused_for_sport(self):
        with patch.object(bot, "finish_login", return_value="verified-token"), patch.object(bot, "main", return_value=0) as main:
            self.assertEqual(github_auth.execute({}, "run"), 0)
        main.assert_called_once_with(token="verified-token")


if __name__ == "__main__":
    unittest.main()
