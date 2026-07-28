import os
import unittest
from pathlib import Path


class GroqEnvTests(unittest.TestCase):
    def test_load_environment_reads_project_dotenv_when_cwd_changes(self):
        root = Path(__file__).resolve().parents[1]
        env_file = root / ".env"
        env_file.write_text("GROQ_API_KEY=test-key\n", encoding="utf-8")
        os.environ.pop("GROQ_API_KEY", None)

        try:
            import app

            app.load_environment(Path("/tmp"))
            self.assertEqual(os.environ.get("GROQ_API_KEY"), "test-key")
        finally:
            os.environ.pop("GROQ_API_KEY", None)
            if env_file.exists():
                env_file.unlink()


if __name__ == "__main__":
    unittest.main()
