import os
import unittest
from unittest.mock import patch

from app import describe_level, app as flask_app, has_unlimited_access


class CurriculumLevelTests(unittest.TestCase):
    def test_upper_primary_label(self):
        self.assertIn("Upper Primary", describe_level("upper_primary", "Grade 4"))

    def test_junior_school_label(self):
        self.assertIn("Junior School", describe_level("junior_school", "Grade 7"))

    def test_senior_school_label(self):
        self.assertIn("Senior School", describe_level("senior_school", "Grade 10"))

    def test_mpesa_tier_prices_are_day_and_month_only(self):
        from app import MPESA_TIER_PRICES
        self.assertEqual(set(MPESA_TIER_PRICES.keys()), {"day", "month"})

    def test_pay_mpesa_requires_login(self):
        with flask_app.test_client() as client:
            response = client.get("/pay-mpesa")
            self.assertEqual(response.status_code, 302)

    def test_admin_emails_bypass_payment_gate(self):
        class DummyUser:
            def __init__(self, email):
                self.email = email
                self.is_premium = False
                self.expiry_date = None

        with patch.dict(os.environ, {"ADMIN_EMAILS": "owner@example.com"}, clear=False):
            self.assertTrue(has_unlimited_access(DummyUser("owner@example.com")))
            self.assertFalse(has_unlimited_access(DummyUser("other@example.com")))


if __name__ == "__main__":
    unittest.main()
