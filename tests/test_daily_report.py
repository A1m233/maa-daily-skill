import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'maa-daily/scripts'))
from recruit_check import inspect_report, MARKER
from daily_report import summarize


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

    def test_untranslated_component_notice_is_not_lost(self):
        r=self.result()
        r['steps']['pre']['reminder_required']=True
        out=summarize(r)
        self.assertIn('review_pre', {n['code'] for n in out['notices']})


if __name__=='__main__':
    unittest.main()
