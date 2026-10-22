import os
import json
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import MultiDict

from app import (
    CAMPUS_YEARS,
    DAILY_CHALLENGE_SUBJECTS,
    ELEMENTARY_GRADES,
    ELEMENTARY_SUBJECTS,
    app as flask_app,
    CLASS_SUBJECTS_BY_LEVEL,
    certificate_photo_mime,
    challenging_subjects_for_user,
    default_study_schedule,
    describe_level,
    elementary_stage_for_grade,
    has_unlimited_access,
    has_completed_highest_course,
    normalize_certificate_photo,
    next_promotion_level,
    onboarding_subject_choices,
    render_certificate_pdf,
    save_study_schedule,
    summarize_study_sessions,
    summarize_learning_topics,
    study_schedule_for_user,
    study_timetable_for_user,
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

    def test_grade_ten_subject_choices_do_not_include_other_classes(self):
        learner = type("Learner", (), {
            "student_track": "highschool",
            "elementary_grade": "Grade 10",
        })()
        self.assertEqual(onboarding_subject_choices(learner), ELEMENTARY_SUBJECTS["Senior School"])
        self.assertIn("Physics", onboarding_subject_choices(learner))
        self.assertNotIn("Pre-Technical Studies", onboarding_subject_choices(learner))

    def test_timetable_includes_all_subjects_and_splits_three_hours_equally(self):
        learner = type("Learner", (), {
            "student_track": "highschool",
            "elementary_grade": "Grade 10",
            "favorite_subject": "Mathematics",
            "challenging_subject": "Physics",
            "challenging_subjects_json": '["Physics", "Chemistry"]',
            "study_schedule_json": None,
            "daily_goal_minutes": None,
        })()
        plan = default_study_schedule(learner)
        learner.study_schedule_json = json.dumps({
            "version": 2,
            "mode": "all",
            "subjects": [],
            "daily_hours": 3,
        })

        schedule = study_schedule_for_user(learner)
        timetable = study_timetable_for_user(learner)

        self.assertEqual(schedule["subjects"], onboarding_subject_choices(learner))
        self.assertEqual(timetable["daily_hours"], 3)
        self.assertEqual(timetable["total_subjects"], len(onboarding_subject_choices(learner)))
        self.assertAlmostEqual(
            schedule["minutes_per_subject"] * timetable["total_subjects"],
            180,
        )
        self.assertEqual(plan["daily_hours"], 1)
        self.assertEqual(challenging_subjects_for_user(learner), ["Physics", "Chemistry"])

    def test_timetable_can_include_only_selected_subjects(self):
        learner = type("Learner", (), {
            "student_track": "highschool",
            "elementary_grade": "Grade 10",
            "study_schedule_json": None,
            "daily_goal_minutes": None,
        })()
        save_study_schedule(learner, MultiDict([
            ("timetable_mode", "selected"),
            ("timetable_subjects", "Mathematics"),
            ("timetable_subjects", "Physics"),
            ("daily_hours", "3"),
        ]))

        schedule = study_schedule_for_user(learner)

        self.assertEqual(schedule["subjects"], ["Mathematics", "Physics"])
        self.assertEqual(schedule["minutes_per_subject"], 90)
        self.assertEqual(learner.daily_goal_minutes, 180)

    def test_timetable_rejects_subjects_outside_the_selected_class(self):
        learner = type("Learner", (), {
            "student_track": "highschool",
            "elementary_grade": "Grade 10",
            "study_schedule_json": None,
            "daily_goal_minutes": None,
        })()
        with self.assertRaises(ValueError):
            save_study_schedule(learner, MultiDict([
                ("timetable_mode", "selected"),
                ("timetable_subjects", "Pre-Technical Studies"),
                ("daily_hours", "3"),
            ]))

    def test_learning_progress_summary_counts_mastery_and_unstarted_subjects(self):
        progress = summarize_learning_topics(
            ["Mathematics", "Physics"],
            [
                SimpleNamespace(
                    subject="Mathematics",
                    taught=True,
                    score=30,
                    total_marks=30,
                    updated_at=None,
                ),
                SimpleNamespace(
                    subject="Mathematics",
                    taught=True,
                    score=24,
                    total_marks=30,
                    updated_at=None,
                ),
            ],
        )

        self.assertEqual(progress[0]["topicsStarted"], 2)
        self.assertEqual(progress[0]["topicsTaught"], 2)
        self.assertEqual(progress[0]["topicsMastered"], 1)
        self.assertEqual(progress[0]["latestScore"], "30/30")
        self.assertEqual(progress[1]["topicsStarted"], 0)
        self.assertIsNone(progress[1]["latestScore"])

    def test_study_session_summary_totals_by_subject_and_caps_goal_progress(self):
        rows = [
            SimpleNamespace(
                subject="Mathematics",
                duration_seconds=900,
                created_at=None,
            ),
            SimpleNamespace(
                subject="Physics",
                duration_seconds=600,
                created_at=None,
            ),
            SimpleNamespace(
                subject="Mathematics",
                duration_seconds=300,
                created_at=None,
            ),
        ]

        summary = summarize_study_sessions(rows, 20)

        self.assertEqual(summary["todaySeconds"], 1800)
        self.assertEqual(summary["goalSeconds"], 1200)
        self.assertEqual(summary["goalPercent"], 100)
        self.assertEqual(
            summary["subjects"],
            [
                {"subject": "Mathematics", "seconds": 1200},
                {"subject": "Physics", "seconds": 600},
            ],
        )
        self.assertEqual(len(summary["recent"]), 3)

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

    def test_certificate_photo_checks_image_signatures(self):
        self.assertEqual(
            certificate_photo_mime(b"\x89PNG\r\n\x1a\n" + b"content"),
            "image/png",
        )
        self.assertEqual(
            certificate_photo_mime(b"\xff\xd8\xff" + b"content" + b"\xff\xd9"),
            "image/jpeg",
        )
        self.assertEqual(
            certificate_photo_mime(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"data"),
            "image/webp",
        )
        self.assertIsNone(certificate_photo_mime(b"not really an image"))

    def test_certificate_photo_is_decoded_and_normalized(self):
        image_buffer = BytesIO()
        Image.new("RGB", (24, 18), color=(34, 90, 55)).save(image_buffer, format="PNG")

        normalized = normalize_certificate_photo(image_buffer.getvalue())

        self.assertEqual(certificate_photo_mime(normalized), "image/jpeg")
        with Image.open(BytesIO(normalized)) as image:
            self.assertEqual(image.size, (24, 18))

    def test_certificate_eligibility_is_campus_only(self):
        class Learner:
            student_track = "highschool"
            campus_year = None
            elementary_grade = "Grade 12"

        self.assertFalse(has_completed_highest_course(Learner()))

        learner = Learner()
        learner.student_track = "campus"
        learner.campus_year = "Year 4"
        self.assertFalse(has_completed_highest_course(learner))

    def test_certificate_pdf_is_generated(self):
        from datetime import datetime
        from types import SimpleNamespace

        photo_buffer = BytesIO()
        Image.new("RGB", (24, 18), color=(34, 90, 55)).save(photo_buffer, format="JPEG")
        pdf = render_certificate_pdf(SimpleNamespace(
            certificate_number="CLV-2026-AB12CD34",
            learner_name="Amina Njeri",
            course_level="Year 5 — Computer Science",
            reviewed_at=datetime(2026, 10, 21),
            photo_data=photo_buffer.getvalue(),
        ))
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)

    def test_mpesa_tier_prices_are_day_and_month_only(self):
        from app import MPESA_TIER_PRICES
        self.assertEqual(set(MPESA_TIER_PRICES.keys()), {"day", "month"})

    def test_pay_mpesa_requires_login(self):
        with flask_app.test_client() as client:
            response = client.get("/pay-mpesa")
            self.assertEqual(response.status_code, 302)

    def test_certificate_routes_require_login(self):
        with flask_app.test_client() as client:
            self.assertEqual(client.get("/study-timetable").status_code, 302)
            self.assertEqual(client.get("/api/learning-progress").status_code, 302)
            self.assertEqual(client.get("/api/study-sessions").status_code, 302)
            self.assertEqual(client.post("/api/study-sessions").status_code, 302)
            self.assertEqual(client.get("/certificate/application").status_code, 302)
            self.assertEqual(client.get("/admin/certificates").status_code, 302)
            self.assertEqual(client.get("/admin/certificates/preview").status_code, 302)
            self.assertEqual(client.get("/admin/certificates/1/photo").status_code, 302)
            self.assertEqual(client.get("/certificate/1/download").status_code, 302)
            self.assertEqual(
                client.post("/admin/certificates/1/issue").status_code,
                302,
            )
            self.assertEqual(
                client.post("/admin/certificates/1/reject").status_code,
                302,
            )

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
