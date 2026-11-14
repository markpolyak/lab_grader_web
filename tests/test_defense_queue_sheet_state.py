import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location('queue_sheet_test', Path(__file__).parents[1] / 'grading/defense_queue/sheet_state.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class SheetStateTests(unittest.TestCase):
    def test_admitted_formats(self):
        for value in ['v', 'v-3', 'v@10.5', 'v@10,5-3', '✓']:
            with self.subTest(value=value):
                self.assertTrue(module.parse_lab_cell(value).admitted)
        state = module.parse_lab_cell('v@10,5-3')
        self.assertEqual((state.score, state.penalty), (Decimal('10.5'), 3))

    def test_defended_is_not_another_admission(self):
        for value in ['vv', 'vv-3', 'vv@10,5-2', '✓✓']:
            state = module.parse_lab_cell(value)
            self.assertTrue(state.defended)
            self.assertFalse(state.admitted)

    def test_unknown_and_failed_cells_do_not_admit(self):
        for value in [None, '', 'x', '?', '?ошибка', 'v-3v-3', 'approved', 'vvv']:
            self.assertFalse(module.parse_lab_cell(value).admitted)

    def test_first_available_lab_skips_unready_gaps(self):
        self.assertEqual(module.first_admitted_lab(['1', '2'], {'1': 'x', '2': 'v'}), '2')
        self.assertEqual(module.first_admitted_lab(['1', '2'], {'1': 'vv', '2': 'v'}), '2')
        self.assertEqual(module.first_admitted_lab(['1', '2'], {'1': 'v', '2': 'v'}, defended_in_history=frozenset({'1'})), '2')

    def test_deadline_absence_and_parse_failure_are_distinct(self):
        parse = lambda text: None
        self.assertFalse(module.read_deadline('', parse=parse).available)
        self.assertFalse(module.read_deadline('bad date', parse=parse).available)
        self.assertTrue(module.read_deadline('', parse=parse, explicitly_no_deadline=True).available)

    def test_manual_deadline_recovers_unavailable_sheet(self):
        date = datetime(2026, 11, 6, tzinfo=timezone.utc)
        self.assertEqual(module.read_deadline('invalid', parse=lambda x: None, manual=date), module.DeadlineState(date, True, 'manual'))
        self.assertEqual(module.read_deadline('valid', parse=lambda x: date), module.DeadlineState(date, True, 'sheet'))


if __name__ == '__main__':
    unittest.main()
