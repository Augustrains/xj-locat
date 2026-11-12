import unittest
import logging
from unittest.mock import patch

import sport_bot as bot


class LogRedactionTests(unittest.TestCase):
    def filtered(self, message, *args):
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, message, args, None)
        self.assertTrue(bot.RedactSecretsFilter().filter(record))
        return record.getMessage()

    def test_configured_credentials_are_not_logged(self):
        with (
            patch.object(bot.Config, "USER", "student-example"),
            patch.object(bot.Config, "PASSWORD", "password-example"),
            patch.object(bot.Config, "SMTP_AUTH_CODE", "mail-auth-example"),
        ):
            self.assertEqual(
                self.filtered("user=%s password=password-example smtp=mail-auth-example", "student-example"),
                "user=[REDACTED] password=[REDACTED] smtp=[REDACTED]",
            )

    def test_authentication_query_values_are_redacted(self):
        self.assertEqual(
            self.filtered("callback https://example.com/?ticket=ST-example&code=oauth-example&token=token-example&state=1234"),
            "callback https://example.com/?ticket=[REDACTED]&code=[REDACTED]&token=[REDACTED]&state=1234",
        )


class SignResultTests(unittest.TestCase):
    def request(self, url, body, status=200):
        with patch.object(bot.requests, "Session") as session:
            session.return_value.post.return_value.status_code = status
            session.return_value.post.return_value.json.return_value = body
            result = bot.sign_operation(url, {}, "test-token", "测试")
            session.return_value.close.assert_called_once()
            return result

    def test_completed_response_only_applies_to_sign_in(self):
        body = {"success": False, "msg": "你今天已经获得了锻炼积分，请明天继续"}
        self.assertIs(self.request(bot.Config.SIGN_IN_URL, body), bot.SignResult.ALREADY_COMPLETED)
        self.assertIs(self.request(bot.Config.SIGN_OUT_URL, body), bot.SignResult.FAILED)

    def test_unknown_errors_still_fail(self):
        for message in ("登录已过期", "已经签到，请先签退", "今天尚未获得积分", ""):
            with self.subTest(message=message):
                self.assertIs(self.request(bot.Config.SIGN_IN_URL, {"success": False, "msg": message}), bot.SignResult.FAILED)

    def test_http_error_is_not_completed(self):
        self.assertIs(self.request(bot.Config.SIGN_IN_URL, {"msg": "你今天已经获得了锻炼积分，请明天继续"}, 500), bot.SignResult.FAILED)

    def test_successful_sign_in_remains_successful(self):
        self.assertIs(self.request(bot.Config.SIGN_IN_URL, {"success": True, "msg": "签到成功！"}), bot.SignResult.SUCCESS)

    def run_main(self, results):
        with (
            patch.object(bot, "get_token", return_value="test-token"),
            patch.object(bot, "sign_operation", side_effect=results) as operation,
            patch.object(bot.time, "sleep") as sleep,
            patch.object(bot, "notify_failure") as failure,
            patch.object(bot, "send_email") as email,
        ):
            code = bot.main()
        return code, operation, sleep, failure, email

    def test_completed_day_skips_wait_sign_out_and_failure_notifications(self):
        code, operation, sleep, failure, email = self.run_main([bot.SignResult.ALREADY_COMPLETED])
        self.assertEqual(code, 0)
        operation.assert_called_once()
        sleep.assert_not_called()
        failure.assert_not_called()
        email.assert_not_called()

    def test_normal_run_waits_before_sign_out(self):
        code, operation, sleep, failure, email = self.run_main([bot.SignResult.SUCCESS, bot.SignResult.SUCCESS])
        self.assertEqual(code, 0)
        self.assertEqual(operation.call_args_list[1].args[0], bot.Config.SIGN_OUT_URL)
        sleep.assert_called_once_with(31 * 60)
        failure.assert_not_called()
        email.assert_called_once()

    def test_real_sign_in_failure_still_fails(self):
        code, operation, sleep, failure, email = self.run_main([bot.SignResult.FAILED])
        self.assertEqual(code, 1)
        operation.assert_called_once()
        sleep.assert_not_called()
        failure.assert_called_once()

    def test_sign_out_failure_still_fails(self):
        code, operation, sleep, failure, email = self.run_main([bot.SignResult.SUCCESS, bot.SignResult.FAILED])
        self.assertEqual(code, 1)
        sleep.assert_called_once_with(31 * 60)
        failure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
