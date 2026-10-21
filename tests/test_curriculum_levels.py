import os
import unittest
from unittest.mock import patch

from app import (
    CAMPUS_YEARS,
    DAILY_CHALLENGE_SUBJECTS,
    ELEMENTARY_GRADES,
    app as flask_app,
    CLASS_SUBJECTS_BY_LEVEL,
    describe_level,
    elementary_stage_for_grade,
    has_unlimited_access,
    next_promotion_level,
    validate_class_promotion_test,
    validate_topic_mastery_test,
)


class CurriculumLevelTests(unittest.TestCase):
    def test_upper_primary_label(self):
        self.assertIn("Upper Primary", describe_level("upper_primary", "Grade 4"))

    def test_junior_school_label(self):
        self.assertIn("Junior School", describe_level("junior_school", "Grade 7"))

    def test_senior_school_label(self):
        self.assertIn("Senior School", describe_level("senior_school", "Grade 10"))

    def test_elementary_onboarding_grade_stages(self):
        self.assertEqual(elementary_stage_for_grade("Grade 3"), "Lower classes")
        self.assertEqual(elementary_stage_for_grade("Grade 6"), "Lower classes")
        self.assertEqual(elementary_stage_for_grade("Grade 7"), "Junior School")
        self.assertEqual(elementary_stage_for_grade("Grade 9"), "Junior School")
        self.assertEqual(elementary_stage_for_grade("Grade 10"), "Senior School")
        self.assertEqual(elementary_stage_for_grade("Grade 12"), "Senior School")
        self.assertIsNone(elementary_stage_for_grade("Form 4"))
        self.assertEqual(
            sum(len(grades) for grades in ELEMENTARY_GRADES.values()),
            10,
        )

    def test_campus_onboarding_uses_existing_subjects_and_years(self):
        self.assertIn("Accounting", DAILY_CHALLENGE_SUBJECTS["campus"])
        self.assertIn("Computer Science", DAILY_CHALLENGE_SUBJECTS["campus"])
        self.assertEqual(CAMPUS_YEARS, ["Year 1", "Year 2", "Year 3", "Year 4", "Year 5"])

    def test_topic_mastery_test_requires_thirty_one_mark_mcqs(self):
        test_data = {
            "total_marks": 30,
            "questions": [
                {
                    "id": index,
                    "type": "mcq",
                    "question": f"Question {index}?",
                    "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
                    "correct_option": "A",
                    "marks": 1,
                    "explanation": "Because A is correct.",
                }
                for index in range(1, 31)
            ],
        }
        self.assertIs(validate_topic_mastery_test(test_data), test_data)

        test_data["questions"][0]["marks"] = 2
        with self.assertRaises(ValueError):
            validate_topic_mastery_test(test_data)

    def test_topic_mastery_test_rejects_wrong_question_count(self):
        with self.assertRaises(ValueError):
            validate_topic_mastery_test({"total_marks": 30, "questions": []})

    def test_promotion_exam_covers_every_subject(self):
        subjects = CLASS_SUBJECTS_BY_LEVEL["upper_primary"]
        questions = [
            {
                "id": index,
                "subject": subjects[(index - 1) % len(subjects)],
                "type": "mcq",
                "question": f"Question {index}?",
                "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
                "correct_option": "A",
                "marks": 1,
                "explanation": "Because A is correct.",
            }
            for index in range(1, 31)
        ]
        exam = {"total_marks": 30, "questions": questions}
        self.assertIs(validate_class_promotion_test(exam, subjects), exam)

        questions[-1]["subject"] = "Unlisted subject"
        with self.assertRaises(ValueError):
            validate_class_promotion_test(exam, subjects)

    def test_promotion_completion_at_highest_school_and_campus_level(self):
        class Learner:
            def __init__(self, track, grade):
                self.student_track = track
                self.elementary_grade = grade if track != "campus" else None
                self.campus_year = grade if track == "campus" else None

        school = Learner("highschool", "Grade 12")
        self.assertEqual(next_promotion_level(school), ("Grade 13", True))
        campus = Learner("campus", "Year 5")
        self.assertEqual(next_promotion_level(campus), ("Year 6", True))

    def test_promotion_advancement_stops_at_highest_defined_levels(self):
        class Learner:
            student_track = "highschool"
            elementary_grade = "Grade 9"
            campus_year = None

        school = Learner()
        self.assertEqual(next_promotion_level(school), ("Grade 10", False))
        school.student_track = "campus"
        school.campus_year = "Year 2"
        self.assertEqual(next_promotion_level(school), ("Year 3", False))

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
