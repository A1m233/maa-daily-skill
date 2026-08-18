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
            "assets/full-daily.example.toml",
            "references/install-and-discovery.md",
            "references/native-config.md",
            "references/mumu-windows.md",
            "references/multi-account.md",
            "references/safety-and-results.md",
            "references/custom-tasks.md",
            "assets/strict-credit-recruit-permit/tasks/tasks.json",
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

    def test_full_template_is_valid_and_safe_by_default(self) -> None:
        path = SKILL_ROOT / "assets" / "full-daily.example.toml"
        content = path.read_text(encoding="utf-8")
        parsed = tomllib.loads(content)
        tasks = parsed["tasks"]
        self.assertEqual(
            ["StartUp", "Recruit", "Custom", "Infrast", "Fight", "Award"],
            [task["type"] for task in tasks],
        )
        self.assertEqual("Official", tasks[0]["params"]["client_type"])

        recruit = tasks[1]["params"]
        self.assertEqual([4, 5], recruit["select"])
        self.assertEqual([3, 4], recruit["confirm"])
        self.assertFalse(recruit["expedite"])
        self.assertEqual(["MaaDailyCredit@MallBegin"], tasks[2]["params"]["task_names"])

        infrast = tasks[3]["params"]
        self.assertEqual(0, infrast["mode"])
        self.assertEqual([], infrast["facility"])
        self.assertEqual("_NotUse", infrast["drones"])

        fight = tasks[4]["params"]
        self.assertEqual(0, fight["medicine"])
        self.assertEqual(0, fight["stone"])
        self.assertIn('timezone = "Official"', content)
        self.assertNotRegex(content, r"(?i)account_name\s*=")
        self.assertNotRegex(content, r"[A-Z]:\\")

    def test_openai_metadata_invokes_the_skill(self) -> None:
        content = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "MAA 个性化日常"', content)
        self.assertIn("$maa-daily", content)
        self.assertIn("多账号", content)

    def test_multi_account_runs_are_isolated_and_fail_closed(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")
        reference = (SKILL_ROOT / "references" / "multi-account.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`maa startup <client> --account-name <唯一登录名>`", skill)
        self.assertIn("不要用自定义切号 helper、虚构的未匹配账号或视觉点击", skill)
        self.assertIn("一个已确认设备/profile", reference)
        self.assertIn("StartUp(A) → 日常(A) → StartUp(B) → 日常(B)", reference)
        self.assertIn("两个账号通常是四个进程", reference)
        self.assertIn("不使用 Agent 视觉或 GUI 点击", reference)
        self.assertIn("停止剩余账号", reference)
        self.assertIn("登录已过期", reference)
        self.assertIn("完整 A→B 官方切号与共享业务 task 顺序执行", reference)
        self.assertIn("带 `****` 的脱敏显示字符串", reference)
        self.assertIn("身份核验仍是日志证据而非强断言", reference)
        self.assertIn("`maa dir log`", skill)
        self.assertIn("`maa dir log`", reference)
        self.assertIn("account_name", reference)
        self.assertIn("只有完成上述检查后仍无法唯一辨认", reference)
        self.assertIn("账号证据与模拟器实例证据分开核验", reference)

    def test_runtime_semantics_guard_known_false_successes(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")
        native = (SKILL_ROOT / "references" / "native-config.md").read_text(
            encoding="utf-8"
        )
        safety = (SKILL_ROOT / "references" / "safety-and-results.md").read_text(
            encoding="utf-8"
        )
        self.assertIn('timezone = "Official"', native)
        self.assertIn("`Recruit.select` 与 `Recruit.confirm` 不是同一个开关", native)
        self.assertIn("`confirm = [3, 4, 5]`", native)
        self.assertIn("`mode = 20000`", native)
        self.assertIn("不是左下角", native)
        self.assertIn("不能证明能完全表达这一后置条件", native)
        self.assertIn("`StartUp Completed` 不证明目标账号身份", skill)
        self.assertIn("登录过期、重新认证或回退到最近账号", safety)
        self.assertIn('`details.action = "DoNothing"`', safety)

    def test_references_have_verification_metadata(self) -> None:
        for path in (SKILL_ROOT / "references").glob("*.md"):
            content = path.read_text(encoding="utf-8")
            self.assertRegex(content, r"最近核验日期：20\d{2}-\d{2}-\d{2}")
            self.assertIn("官方来源", content, path.name)
            self.assertIn("边界", content, path.name)

    def test_strict_credit_resource_is_scoped_and_parseable(self) -> None:
        path = SKILL_ROOT / "assets" / "strict-credit-recruit-permit" / "tasks" / "tasks.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        scan = payload["MaaDailyCredit@ScanRecruitPermits"]
        slots = [name for name in scan["next"] if "FindRecruitPermit" in name]
        self.assertEqual(10, len(slots))
        for name in slots:
            task = payload[name]
            self.assertEqual(["招聘许可"], task["text"])
            self.assertEqual("ClickSelf", task["action"])
        bought = payload["MaaDailyCredit@CreditShop-Bought"]
        self.assertEqual("CreditShop-Bought.png", bought["template"])

    def test_mumu_visibility_lifecycle_is_guarded(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")
        reference = (SKILL_ROOT / "references" / "mumu-windows.md").read_text(encoding="utf-8")
        self.assertIn("默认使用普通可见模式", skill)
        self.assertIn("不要把它当作普通后台 helper", reference)
        self.assertIn("shutdown_player", reference)
        self.assertIn("无可见窗口、再次启动又被旧实例拦截", reference)

    def test_eval_contract_is_well_formed(self) -> None:
        payload = json.loads((ROOT / "evals" / "maa-daily.json").read_text(encoding="utf-8"))
        self.assertEqual("maa-daily", payload["skill_name"])
        self.assertGreaterEqual(len(payload["evals"]), 8)
        ids = [case["id"] for case in payload["evals"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({20, 21, 22, 23, 24, 25, 26, 27}.issubset(ids))
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
