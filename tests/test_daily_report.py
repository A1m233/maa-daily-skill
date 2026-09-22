import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
import contextlib
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'maa-daily/scripts'))
from recruit_check import inspect_report, MARKER
from daily_report import summarize, summarize_many, main


def event(kind, **extra):
    return MARKER + kind + ' ' + json.dumps({'taskchain': 'Recruit', 'taskid': 1, **extra})


class ReportTests(unittest.TestCase):
    def test_direct_scripts_work_with_safe_path(self):
        root = Path(__file__).resolve().parents[1] / 'maa-daily/scripts'
        for name in ('daily_report.py', 'recruit_check.py'):
            p = subprocess.run([sys.executable, '-P', '-B', str(root/name), '--help'], capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr)

    def inspect(self, lines, change=False):
        with tempfile.TemporaryDirectory() as root:
            data = ('\n'.join(lines) + '\n').encode()
            path = Path(root) / 'log'
            path.write_bytes(data + (b'changed' if change else b''))
            report = {'child_exit_code': 0, 'wrapper_exit_code': 0, 'evidence': {
                'state': 'bounded', 'before_size': 0, 'after_size': len(data),
                'interval_sha256': hashlib.sha256(data).hexdigest(), 'log_file': str(path)}}
            if change:
                path.write_bytes(b'X' + data[1:])
            return inspect_report(report)

    def rounds(self):
        return [event('TaskChainStart'),
                event('SubTaskExtraInfo', what='RecruitTagsDetected', details={'tags': ['tag-a']}),
                event('SubTaskExtraInfo', what='RecruitTagsRefreshed', details={'count': 1}),
                event('SubTaskExtraInfo', what='RecruitTagsDetected', details={'tags': ['preserve']}),
                event('SubTaskExtraInfo', what='RecruitPreservedTag', details={'tags': ['preserve'], 'tag': 'preserve'}),
                event('SubTaskExtraInfo', what='RecruitTagsDetected', details={'tags': ['tag-b']}),
                event('SubTaskExtraInfo', what='RecruitTagsRefreshed', details={'count': 1}),
                event('SubTaskExtraInfo', what='RecruitTagsDetected', details={'tags': ['tag-c']}),
                event('SubTaskCompleted', details={'task': 'RecruitConfirm', 'action': 'ClickSelf'}),
                event('TaskChainCompleted')]

    def test_preserved_tags_and_refresh_rounds_not_slots(self):
        r = self.inspect(self.rounds())
        self.assertEqual(r['status'], 'evaluated')
        self.assertEqual(r['confirmed_actions'], 1)
        self.assertEqual(r['refresh_actions'], 2)
        self.assertEqual(len(r['pending']), 1)
        self.assertEqual(r['pending'][0]['reason'], 'maa_preserved_tag')
        self.assertTrue(r['reminder_required'])

    def test_hash_failure_and_incomplete_are_unknown(self):
        self.assertEqual(self.inspect(self.rounds(), True)['status'], 'unknown')
        self.assertEqual(self.inspect(self.rounds()[:-1])['status'], 'unknown')
        self.assertEqual(self.inspect([event('TaskChainStart'), event('TaskChainCompleted')])['status'], 'unknown')

    def test_no_confirm_does_not_invent_reason_or_confirm_from_detection(self):
        lines = [event('TaskChainStart'), event('SubTaskExtraInfo', what='RecruitTagsDetected', details={'tags':['rare']}),
                 event('SubTaskExtraInfo', what='RecruitResult', details={'level':5}), event('TaskChainCompleted')]
        r = self.inspect(lines)
        self.assertEqual(r['confirmed_actions'], 0)
        self.assertEqual(r['pending'][0]['outcome'], 'unverified')
        self.assertNotIn('reason', r['pending'][0])

    def test_reused_chain_or_conflicting_confirmation_is_unknown(self):
        lines = self.rounds()
        self.assertEqual(self.inspect(lines + [event('TaskChainStart')])['status'], 'unknown')
        lines.insert(-1, event('SubTaskCompleted', details={'task': 'RecruitConfirm', 'action': 'ClickSelf'}))
        self.assertEqual(self.inspect(lines)['status'], 'unknown')

    def result(self):
        return {'status':'completed', 'account':'example', 'game_day':'2026-09-14',
                'steps': {key: {'status':'completed'} for key in ('pre','priority','drain','award','checks')}}

    def test_reward_false_does_not_erase_recruit_or_missing_drain(self):
        r = self.result()
        r['steps']['pre']['tasks']=[{'type':'Recruit','recruitment': self.inspect(self.rounds())}]
        r['steps']['checks']['rewards']={'status':'evaluated','daily_orundum':'claimed',
            'daily_annihilation_ticket':'claimed', 'reminder_required':False}
        r['steps']['drain']['status']='pending'
        out=summarize(r)
        self.assertEqual(out['status'], 'incomplete')
        self.assertTrue(out['reminder_required'])
        codes={n['code'] for n in out['notices']}
        self.assertIn('step_drain', codes)
        self.assertIn('recruit_0_0', codes)
        self.assertNotIn('daily_orundum', codes)

    def test_unknown_rewards_and_medicine_do_not_become_claimed_or_empty(self):
        r=self.result()
        r['steps']['drain'].update(reminder_required=True, medicine_check={'status':'unknown','reminder_required':True})
        out=summarize(r)
        self.assertEqual(out['status'],'completed_with_reminder')
        self.assertEqual({n['code'] for n in out['notices']}, {'medicine','daily_orundum','daily_annihilation_ticket'})
        self.assertEqual(out['facts'], [])

    def test_input_is_not_mutated(self):
        r=self.result(); before=copy.deepcopy(r)
        summarize(r)
        self.assertEqual(r,before)

    def test_failed_fight_observation_is_not_reported_as_verified_or_zero(self):
        r = self.result()
        r['steps']['drain'].update(status='failed', completed_runs=0, remaining_sanity=None,
            battle_observation={'status':'observed', 'observed_completed_runs':5,
                'drop_status':'failed', 'usable_for_planning':False,
                'latest_sanity':{'current':25, 'maximum':205, 'observed_at':'2026-09-18 22:54:17'}})
        out = summarize(r)
        self.assertEqual(out['status'], 'incomplete')
        self.assertTrue(any('日志上报完成 5 场' in text for text in out['facts']))
        self.assertFalse(any('完成 0 场' in text for text in out['facts']))
        self.assertTrue({'drain_evidence','drain_drops'} <= {n['code'] for n in out['notices']})
        del r['steps']['drain']['battle_observation']
        self.assertEqual(summarize(r)['facts'], [])

    def test_untranslated_component_notice_is_not_lost(self):
        r=self.result()
        r['steps']['pre']['reminder_required']=True
        out=summarize(r)
        self.assertIn('review_pre', {n['code'] for n in out['notices']})

    def complete_result(self, account):
        r = self.result()
        r['account'] = account
        r['steps']['drain'].update(stage='AP-5', completed_runs=6, remaining_sanity=25)
        r['steps']['checks']['rewards'] = {'status':'evaluated', 'daily_orundum':'claimed',
                                         'daily_annihilation_ticket':'claimed'}
        return r

    def test_multi_groups_shared_facts_and_preserves_scoped_notices(self):
        a, b = self.complete_result('A'), self.complete_result('B')
        for r in (a, b):
            r['steps']['drain']['medicine_check'] = {'status':'unknown'}
        b['steps']['pre']['tasks'] = [{'type':'Recruit', 'recruitment': self.inspect(self.rounds())}]
        before = copy.deepcopy([a, b])
        out = summarize_many([b, a], ['A', 'B'], a['game_day'])
        self.assertEqual(out['status'], 'completed_with_reminder')
        medicine = next(n for n in out['notices'] if n['code'] == 'medicine')
        self.assertEqual(medicine['accounts'], ['A', 'B'])
        preserved = next(n for n in out['notices'] if n['code'] == 'recruit_0_0')
        self.assertEqual(preserved['accounts'], ['B'])
        shared = [f for f in out['facts'] if f['accounts'] == ['A', 'B']]
        self.assertEqual(len(shared), 2)
        self.assertLess(out['text'].index(medicine['text']), out['text'].index(shared[0]['text']))
        self.assertEqual([a, b], before)

    def test_multi_complete_missing_and_partial(self):
        a, b = self.complete_result('A'), self.complete_result('B')
        self.assertEqual(summarize_many([a, b], ['A', 'B'], a['game_day'])['status'], 'completed')
        out = summarize_many([a], ['A', 'B'], a['game_day'])
        self.assertEqual(out['status'], 'incomplete')
        self.assertEqual(out['missing_accounts'], ['B'])
        self.assertEqual(out['notices'][0]['accounts'], ['B'])
        b['steps']['drain']['status'] = 'pending'
        b['steps']['checks']['rewards']['daily_annihilation_ticket'] = 'unknown'
        out = summarize_many([a, b], ['A', 'B'], a['game_day'])
        self.assertEqual(out['status'], 'incomplete')
        self.assertTrue({'step_drain', 'daily_annihilation_ticket'} <= {n['code'] for n in out['notices']})
        self.assertTrue(all(n['accounts'] == ['B'] for n in out['notices']))
        empty = summarize_many([], ['A', 'B'], a['game_day'])
        self.assertEqual(empty['missing_accounts'], ['A', 'B'])
        self.assertEqual(empty['facts'], [])

    def test_multi_rejects_ambiguous_sources(self):
        a = self.complete_result('A')
        for results, accounts, day in (([a, a], ['A'], a['game_day']),
                                       ([a], ['B'], a['game_day']),
                                       ([a], ['A', 'A'], a['game_day']),
                                       ([a], ['A'], '2026-09-15')):
            with self.assertRaises(ValueError):
                summarize_many(results, accounts, day)

    def test_cli_multi_missing_preserves_source_and_refuses_output_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'result.json'
            source.write_text(json.dumps(self.complete_result('A')), encoding='utf-8')
            before = source.read_bytes()
            args = ['--daily-result', str(source), '--expect-account', 'A', '--expect-account', 'B',
                    '--game-day', '2026-09-14', '--output-dir', str(root/'brief'), '--json']
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(args), 2)
            value = json.loads(stdout.getvalue())
            self.assertEqual(value['missing_accounts'], ['B'])
            written = (root/'brief/brief.json').read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(args), 2)
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'unknown')
            self.assertEqual(written, (root/'brief/brief.json').read_bytes())
            self.assertEqual(source.read_bytes(), before)
            # Existing single-account CLI is unchanged and does not need an account list.
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(['--daily-result', str(source), '--json']), 0)
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'completed')

    def test_cli_bad_input_never_reports_completion(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'bad.json'
            path.write_text('null', encoding='utf-8')
            for args in ([], ['--daily-result', str(path)], ['--expect-account', 'A'],
                         ['--expect-account', 'A', '--game-day', 'bad-date']):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(main([*args, '--json']), 2)
                self.assertEqual(json.loads(stdout.getvalue())['status'], 'unknown')


if __name__=='__main__':
    unittest.main()
