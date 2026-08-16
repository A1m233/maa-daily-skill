from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "maa-daily"
SKILL_MD = SKILL_ROOT / "SKILL.md"


class SkillContractTests(unittest.TestCase):
    def test_expected_distribution_files_exist(self) -> None:
        expected = {
            "SKILL.md",
            "agents/openai.yaml",
            "assets/daily.toml",
            "references/install-and-discovery.md",
            "references/native-config.md",
            "references/mumu-windows.md",
            "references/safety-and-results.md",
        }
        actual = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(expected, actual)

    def test_skill_frontmatter_and_reference_links(self) -> None:
        content = SKILL_MD.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\nname: maa-daily\n"))
        self.assertRegex(content, r"(?m)^description: .+MAA.+$")

        links = re.findall(r"\[[^]]+\]\((references/[^)]+|assets/[^)]+)\)", content)
        self.assertGreaterEqual(len(links), 5)
        for relative in links:
            self.assertTrue((SKILL_ROOT / relative).is_file(), relative)

    def test_template_is_valid_toml_and_conservative(self) -> None:
        template = (SKILL_ROOT / "assets" / "daily.toml").read_bytes()
        parsed = tomllib.loads(template.decode("utf-8"))
        self.assertEqual(["StartUp", "Award"], [task["type"] for task in parsed["tasks"]])
        startup = parsed["tasks"][0]["params"]
        self.assertEqual("Official", startup["client_type"])
        self.assertTrue(startup["start_game_enabled"])
        params = parsed["tasks"][1]["params"]
        self.assertNotIn("stone", params)
        self.assertFalse(params["orundum"])

    def test_openai_metadata_invokes_the_skill(self) -> None:
        content = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "MAA 个性化日常"', content)
        self.assertIn("$maa-daily", content)

    def test_references_have_verification_metadata(self) -> None:
        for path in (SKILL_ROOT / "references").glob("*.md"):
            content = path.read_text(encoding="utf-8")
            self.assertIn("最近核验日期：2026-08-16", content, path.name)
            self.assertIn("官方来源", content, path.name)
            self.assertIn("边界", content, path.name)

    def test_eval_contract_is_well_formed(self) -> None:
        payload = json.loads((ROOT / "evals" / "maa-daily.json").read_text(encoding="utf-8"))
        self.assertEqual("maa-daily", payload["skill_name"])
        self.assertGreaterEqual(len(payload["evals"]), 8)
        ids = [case["id"] for case in payload["evals"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in payload["evals"]:
            self.assertTrue(case["prompt"])
            self.assertTrue(case["expected_output"])

    def test_public_files_do_not_contain_private_workflow_or_user_paths(self) -> None:
        forbidden = (
            "C:" + "\\Users\\" + "chens",
            ".feature-" + "delivery",
            "one-shot-" + "personalized-maa-daily",
        )
        for path in ROOT.rglob("*"):
            if (
                not path.is_file()
                or ".git" in path.parts
                or path.suffix not in {".json", ".md", ".py", ".toml", ".yaml", ".yml"}
            ):
                continue
            content = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, content, path.as_posix())


if __name__ == "__main__":
    unittest.main()
