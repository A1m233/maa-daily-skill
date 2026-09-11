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
            "scripts/run_with_evidence.py",
            "scripts/daily_checks.py",
            "scripts/reward_check.py",
            "scripts/infrast_check.py",
            "scripts/drain_sanity.py",
            "assets/drain-sanity/tasks.json",
            "assets/stage-costs.json",
            "references/create-task.md",
            "references/daily-checks.md",
            "references/drain-integration.md",
            "assets/daily-checks/tasks.json",
            "assets/daily-checks/maa-daily-check-sanity.toml",
            "assets/daily-checks/maa-daily-check-rewards.toml",
            "assets/daily-checks/maa-daily-check-screen.toml",
            "assets/daily-checks/maa-daily-reward-scan.toml",
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

        links = re.findall(
            r"\[[^]]+\]\((references/[^)]+|assets/[^)]+|scripts/[^)]+)\)",
            content,
        )
        self.assertGreaterEqual(len(links), 5)
        for relative in links:
            self.assertTrue((SKILL_ROOT / relative.split("#", 1)[0]).is_file(), relative)

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
        self.assertEqual(1, fight["series"])
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
        self.assertIn("两个账号通常是六个进程", reference)
        self.assertIn("startup → bulk → cleanup/Award", reference)
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
        self.assertIn("`FightTimes.times_finished = 0`", native)
        self.assertIn("`series = 0` 定义为 AUTO", native)
        self.assertIn("理智不足以支付最大可用倍率时选择最小可用倍率", native)
        self.assertIn("便捷命令与原生 task 的版本边界", native)
        self.assertIn("N = q × M + k", native)
        self.assertIn("k = floor(R / C)", native)
        self.assertIn("series = 8, times = 8", native)
        self.assertIn("最终剩余 7", native)
        self.assertIn("公开 TOML 模板继续使用 `series = 1`", native)
        self.assertIn("`Fight Completed` 也不证明实际开战过", skill)
        self.assertIn("资源结果与战斗结果必须分开核验", skill)
        self.assertIn("零战斗结束", safety)
        self.assertIn("`FightTimes.times_finished = 0` 不是", safety)
        self.assertIn("`StartButton2`、`PRTS1/2/3`", safety)
        self.assertIn("进程退出不代表游戏中的当前战斗被取消", safety)
        self.assertIn("CLI 更新网络失败，业务任务未开始", safety)
        self.assertIn("Unknown task: FightSeries-OldMethodFlag", safety)

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
        custom_reference = (
            SKILL_ROOT / "references" / "custom-tasks.md"
        ).read_text(encoding="utf-8")
        self.assertIn("当前捆绑资源有一个已确认的待修缺口", custom_reference)
        self.assertIn("templ not found MaaDailyCredit@CrisisPopup.png", custom_reference)
        self.assertIn("`CreditShop-BuyIt` → `CreditShop-Bought`", custom_reference)

    def test_mumu_visibility_lifecycle_is_guarded(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")
        reference = (SKILL_ROOT / "references" / "mumu-windows.md").read_text(encoding="utf-8")
        self.assertIn("默认或既有偏好是前台可见", skill)
        self.assertIn("不要把它当作普通后台 helper", reference)
        self.assertIn("control -v 0 shutdown", reference)
        self.assertIn("control -v 0 launch", reference)
        self.assertIn("`info -v 0`", reference)
        self.assertIn("ADB 端口不变", reference)
        self.assertIn("Windows error 1455", reference)
        self.assertIn("前台壳已打开，但虚拟机启动失败", reference)
        self.assertIn("不自动调整系统页面文件", reference)
        self.assertIn("无可见窗口、再次启动又被旧实例拦截", reference)
        self.assertIn("启动成功至少区分四层证据", reference)
        self.assertIn("后端实例运行但前台壳缺失", reference)

    def test_eval_contract_is_well_formed(self) -> None:
        payload = json.loads((ROOT / "evals" / "maa-daily.json").read_text(encoding="utf-8"))
        self.assertEqual("maa-daily", payload["skill_name"])
        self.assertGreaterEqual(len(payload["evals"]), 8)
        ids = [case["id"] for case in payload["evals"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(set(range(20, 50)).issubset(ids))
        for case in payload["evals"]:
            self.assertTrue(case["prompt"])
            self.assertTrue(case["expected_output"])

    def test_execution_context_guidance_is_host_agnostic(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")
        safety = (SKILL_ROOT / "references" / "safety-and-results.md").read_text(
            encoding="utf-8"
        )
        multi = (SKILL_ROOT / "references" / "multi-account.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("另一个上下文里的只读 ADB 成功不能替代这一门禁", skill)
        self.assertIn("尚未进入 MaaCore 的执行前停滞", safety)
        self.assertIn("原进程不会与新进程重叠", safety)
        self.assertIn("内部重试后切号成功", multi)
        self.assertIn("不得把跨轮次拼接出的零散证据", multi)

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
