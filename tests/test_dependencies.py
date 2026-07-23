"""Keep the role-specific dependency profiles pinned and separated."""

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_FILES = (
    "requirements.txt",
    "requirements-node.txt",
    "requirements-ai.txt",
    "requirements-dev.txt",
)


def package_lines(file_name: str) -> set[str]:
    lines = set()
    for raw_line in (PROJECT_ROOT / file_name).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith(("#", "-r ")):
            lines.add(line)
    return lines


class TestDependencyProfiles(unittest.TestCase):
    def test_every_direct_dependency_is_pinned(self):
        for file_name in PROFILE_FILES:
            with self.subTest(file=file_name):
                self.assertTrue(all("==" in line for line in package_lines(file_name)))

    def test_heavy_roles_are_not_in_server_core(self):
        core = package_lines("requirements.txt")
        self.assertFalse({"opencv-python", "pygame", "gTTS", "google-genai", "ollama"} & {
            line.split("==", 1)[0] for line in core
        })

    def test_legacy_packages_are_removed(self):
        combined = "\n".join(
            (PROJECT_ROOT / file_name).read_text(encoding="utf-8")
            for file_name in PROFILE_FILES
        ).lower()
        self.assertNotIn("piper-tts", combined)
        self.assertNotIn("httpx2", combined)

    def test_profiles_contain_their_runtime_packages(self):
        core = {line.split("==", 1)[0] for line in package_lines("requirements.txt")}
        node = {line.split("==", 1)[0] for line in package_lines("requirements-node.txt")}
        ai = {line.split("==", 1)[0] for line in package_lines("requirements-ai.txt")}
        dev = {line.split("==", 1)[0] for line in package_lines("requirements-dev.txt")}
        self.assertIn("psycopg[binary]", core)
        self.assertTrue({"opencv-python", "pygame", "gTTS", "requests"}.issubset(node))
        self.assertTrue({"google-genai", "protobuf", "ollama"}.issubset(ai))
        self.assertTrue({"httpx", "requests"}.issubset(dev))


if __name__ == "__main__":
    unittest.main()
