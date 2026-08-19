from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse


class AskAgentCsrfTests(TestCase):
    """Regression coverage for the ask_agent endpoint's CSRF protection.

    This endpoint briefly shipped with @csrf_exempt despite the frontend
    already sending a real CSRF token -- the decorator just discarded that
    protection for no reason. enforce_csrf_checks=True below reproduces
    what a browser actually does (Django's default test Client disables
    CSRF checks, which would hide this).
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse("tracker:ask_agent")

    def test_post_without_csrf_token_is_rejected(self):
        response = self.client.post(
            self.url, data={"question": "test"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)

    @patch("tracker.views.agent_ask", return_value="mocked answer")
    def test_post_with_csrf_token_succeeds(self, mock_ask):
        self.client.get(reverse("tracker:dashboard"))  # sets the csrftoken cookie
        csrf_token = self.client.cookies["csrftoken"].value
        response = self.client.post(
            self.url,
            data={"question": "test"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"answer": "mocked answer"})
        mock_ask.assert_called_once_with("test")


class AskAgentValidationTests(TestCase):
    """CSRF checks are off by default here -- these only exercise the
    view's own input validation, not the CSRF middleware."""

    def setUp(self):
        self.url = reverse("tracker:ask_agent")

    def test_empty_question_rejected(self):
        response = self.client.post(
            self.url, data={"question": "  "}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_question_too_long_rejected(self):
        response = self.client.post(
            self.url, data={"question": "x" * 501}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_json_rejected(self):
        response = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    @patch("tracker.views.agent_ask", side_effect=RuntimeError("no key set"))
    def test_missing_api_key_returns_500_with_message(self, mock_ask):
        response = self.client.post(
            self.url, data={"question": "test"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "no key set"})
