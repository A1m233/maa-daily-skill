import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import run_with_evidence as runner
import infrast_check as infrast
import drain_sanity as drain
import reward_check as rewards
sys.path.pop(0)


def event(kind, chain="Infrast", taskid=3, **extra):
    return "[INF][P1][T1] " + runner.CALLBACK_MARKER + kind + " " + json.dumps({
        "taskchain": chain, "taskid": taskid, **extra})


def scope(nodes=("UnknownProbe",), terminal="TaskChainCompleted", chain="Infrast"):
    return [event("TaskChainStart", chain),
            *[event("SubTaskError", chain, subtask="ProcessTask", first=[node]) for node in nodes],
            event(terminal, chain)]


class ExecutionResultTests(unittest.TestCase):
    def test_default_child_id_is_attributed_without_erasing_errors(self):
        # Independently constructed children may retain zero; no node-name exemptions.
        for chain in ('StartUp', 'OtherChain'):
            rows = [event('TaskChainStart', chain, 1, uuid='device-example'),
                    event('SubTaskError', chain, 0, uuid='device-example', first=['UnseenNode']),
                    event('TaskChainCompleted', chain, 1, uuid='device-example')]
            with self.subTest(chain=chain), tempfile.TemporaryDirectory() as temp:
                code, report = self.run_report(temp, rows)
                self.assertEqual(code, 0)
                self.assertEqual(report['evidence']['subtask_error_lines'], [2])
                execution = report['evidence']['execution']
                self.assertEqual(execution['policy'], 'execution-boundary-v2')
                self.assertEqual(execution['status'], 'completed')
                self.assertEqual(execution['subtask_error_attributions'], [
                    {'line':2, 'taskchain':chain, 'reported_taskid':0, 'parent_taskid':1,
                     'basis':'default_id_unique_active_chain'}])
                self.assertEqual(report['business_result'], 'not_evaluated')

    def test_default_id_requires_unique_complete_context(self):
        start = event('TaskChainStart', 'StartUp', 1, uuid='device-example')
        error = event('SubTaskError', 'StartUp', 0, uuid='device-example')
        end = event('TaskChainCompleted', 'StartUp', 1, uuid='device-example')
        alternatives = [error.replace('[P1]', '[P2]'), error.replace('[T1]', '[T2]'),
                        event('SubTaskError', 'StartUp', 0, uuid='other-device'),
                        event('SubTaskError', 'Different', 0, uuid='device-example'),
                        event('SubTaskError', 'StartUp', 9, uuid='device-example'),
                        event('SubTaskError', 'StartUp', 0),
                        error.replace('[P1][T1]', '')]
        cases = [[start, other, end] for other in alternatives]
        cases += [[error, start, end], [start, end, error], [start, error],
                  [event('TaskChainStart', 'StartUp', 1), error, end],
                  [start, event('TaskChainStart', 'StartUp', 2, uuid='device-example'), error,
                   event('TaskChainCompleted', 'StartUp', 2, uuid='device-example'), end],
                  [start, error, end.replace('[P1]', '[P2]')]]
        for rows in cases:
            with self.subTest(rows=rows):
                self.assertEqual(runner.classify_execution(rows)['status'], 'unknown')

    def test_positive_id_and_terminal_identity_remain_strict(self):
        start = event('TaskChainStart', 'StartUp', 1, uuid='device-example')
        end = event('TaskChainCompleted', 'StartUp', 1, uuid='device-example')
        for child in (event('SubTaskError', 'StartUp', 1, uuid='other-device'),
                      event('SubTaskError', 'StartUp', 1, uuid='device-example').replace('[T1]', '[T2]')):
            self.assertEqual(runner.classify_execution([start, child, end])['status'], 'unknown')
        wrong_end = event('TaskChainCompleted', 'StartUp', 0, uuid='device-example')
        self.assertEqual(runner.classify_execution([start, wrong_end])['status'], 'unknown')
        for terminal in ('TaskChainError', 'TaskChainStopped'):
            rows = [start, event('SubTaskError', 'StartUp', 0, uuid='device-example'),
                    event(terminal, 'StartUp', 1, uuid='device-example')]
            with tempfile.TemporaryDirectory() as temp:
                self.assertEqual(self.run_report(temp, rows)[0], 75)

    def test_inspection_reassesses_only_verified_execution_without_writes(self):
        rows = [event('TaskChainStart', 'StartUp', 1, uuid='device-example'),
                event('SubTaskError', 'StartUp', 0, uuid='device-example'),
                event('TaskChainCompleted', 'StartUp', 1, uuid='device-example')]
        with tempfile.TemporaryDirectory() as temp:
            _, report = self.run_report(temp, rows)
            report['wrapper_exit_code'] = 74
            report['evidence']['execution'] = {'status':'unknown', 'policy':'execution-boundary-v1'}
            path = Path(temp) / 'source.json'
            path.write_text(json.dumps(report), encoding='utf-8')
            before = path.read_bytes()
            log = Path(report['evidence']['log_file'])
            with log.open('ab') as stream:
                stream.write(b'later unrelated log\n')
            log_before = log.read_bytes()
            with patch.object(runner.subprocess, 'run') as process, contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(runner.main(['--inspect-report', str(path)]), 0)
                process.assert_not_called()
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['original_wrapper_exit_code'], 74)
            self.assertEqual(result['reassessed_wrapper_exit_code'], 0)
            self.assertEqual(result['subtask_error_lines'], [2])
            self.assertEqual(result['business_result'], 'not_evaluated')
            self.assertEqual(result['continuation'], 'requires_business_preconditions')
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(log.read_bytes(), log_before)
            for changes, expected in (({'child_exit_code':7}, 7), ({'runner_error':'Interrupted'}, 74)):
                self.assertEqual(runner.inspect_execution_report({**report, **changes})['reassessed_wrapper_exit_code'], expected)
            log.write_bytes(b'X' + log_before[1:])
            self.assertEqual(runner.inspect_execution_report(report)['reason'], 'log_interval_changed_or_unhashed')

    def test_inspection_invalid_input_and_execution_arguments_never_spawn(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.json'
            path.write_text('{}', encoding='utf-8')
            with patch.object(runner.subprocess, 'run') as process, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(['--inspect-report', str(path)]), 74)
                for extra in (['--', 'fake-maa', 'run'], ['--report-file', str(path)], ['--core-log', 'unused']):
                    with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                        runner.main(['--inspect-report', str(path), *extra])
                process.assert_not_called()
            self.assertEqual(path.read_text(encoding='utf-8'), '{}')

    def run_report(self, directory, rows, child_exit=0):
        path = Path(directory) / "asst.log"
        path.write_bytes(b"")
        data = ("\n".join(rows) + "\n").encode()
        def execute(*args, **kwargs):
            path.write_bytes(data)
            return subprocess.CompletedProcess([], child_exit)
        output = io.StringIO()
        with patch.object(runner.subprocess, "run", side_effect=execute), contextlib.redirect_stdout(output):
            code = runner.main(["--core-log", str(path), "--", "fake-maa", "run", "test"])
        return code, json.loads(output.getvalue().split(runner.REPORT_PREFIX)[1])

    def test_arbitrary_child_errors_do_not_override_complete_execution(self):
        # Actual five-node shape, and an unseen name: neither requires a whitelist.
        for nodes in [("UnlockClues", "EndOfClueExchange", "InfrastClueSelfFull",
                       "UnlockClues", "EndOfClueExchange"), ("NewUnknownNode",)]:
            with self.subTest(nodes=nodes), tempfile.TemporaryDirectory() as temp:
                code, report = self.run_report(temp, scope(nodes))
                self.assertEqual(code, 0)
                self.assertEqual(report["schema_version"], 2)
                self.assertEqual(report["evidence"]["execution"]["status"], "completed")
                self.assertEqual(len(report["evidence"]["subtask_error_lines"]), len(nodes))
                self.assertEqual(report["business_result"], "not_evaluated")
                result = infrast.inspect_report(report)
                self.assertEqual(result["status"], "evaluated")
                self.assertEqual(result["run_status"], "warnings")
                self.assertEqual(len(result["error_groups"]["infrast"]), len(nodes))
                self.assertEqual(result["all_work_completed"], "unknown")
                self.assertEqual(result["chains"][0]["collection_status"], "unknown")
                self.assertEqual(result["continuation"], "requires_business_preconditions")
                self.assertTrue(result["business_review_required"])
                self.assertTrue(result["reminder_required"])

    def test_execution_failures_and_incomplete_boundaries_still_stop(self):
        good = scope()
        cases = [(scope(terminal="TaskChainError"), 75),
                 (scope(terminal="TaskChainStopped"), 75),
                 (good[:-1], 74), (good[1:], 74), (good + good, 74),
                 ([event("SubTaskError")], 74),
                 (good + [event("SubTaskError")], 74),
                 (good + [event("TaskChainStart", taskid=9)], 74),
                 (good + [runner.CALLBACK_MARKER + "SubTaskError []"], 74),
                 (good + [runner.CALLBACK_MARKER + "SubTaskError malformed"], 74),
                 (good + [event("InternalError")], 75),
                 (good + [event("InitFailed")], 75)]
        for rows, expected in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as temp:
                code, report = self.run_report(temp, rows)
                self.assertEqual(code, expected)
                self.assertNotEqual(report["evidence"]["execution"]["status"], "completed")
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.run_report(temp, good, child_exit=7)[0], 7)

    def test_legacy_reassessment_is_execution_only_and_never_rewrites_report(self):
        with tempfile.TemporaryDirectory() as temp:
            _, report = self.run_report(temp, scope())
            report.update(schema_version=1, wrapper_exit_code=75)
            result = infrast.inspect_report(report)
            self.assertTrue(result["legacy_exit_reclassified"])
            self.assertEqual(result["original_wrapper_exit_code"], 75)
            self.assertEqual(report["wrapper_exit_code"], 75)
            self.assertEqual(result["all_work_completed"], "unknown")
            for change in ({"schema_version": 2}, {"child_exit_code": 1},
                           {"runner_error": "Interrupted"}, {"wrapper_exit_code": 74}):
                with self.subTest(change=change):
                    failed = infrast.inspect_report({**report, **change})
                    self.assertFalse(failed["legacy_exit_reclassified"])
                    self.assertEqual(failed["run_status"], "failed")
            report["evidence"]["interval_sha256"] = "invalid"
            self.assertEqual(infrast.inspect_report(report)["reason"], "log_interval_changed_or_unhashed")

    def test_strict_business_checks_reject_child_error_even_with_runner_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            code, report = self.run_report(temp, scope(("RecognizeDrops",), chain="Fight"))
            self.assertEqual(code, 0)
            with self.assertRaisesRegex(ValueError, "invalid_or_failed_callback"):
                drain.callbacks(report)
            code, report = self.run_report(temp, scope(("ScanFailed",), chain="Custom"))
            self.assertEqual(code, 0)
            self.assertEqual(rewards.evaluate(report)["status"], "unknown")
        for chain in ('Fight', 'Custom'):
            rows = [event('TaskChainStart', chain, 1, uuid='device-example'),
                    event('SubTaskError', chain, 0, uuid='device-example'),
                    event('TaskChainCompleted', chain, 1, uuid='device-example')]
            with tempfile.TemporaryDirectory() as temp:
                code, report = self.run_report(temp, rows)
                self.assertEqual(code, 0)
                if chain == 'Fight':
                    with self.assertRaises(ValueError):
                        drain.callbacks(report)
                else:
                    self.assertEqual(rewards.evaluate(report)['status'], 'unknown')


if __name__ == "__main__":
    unittest.main()
