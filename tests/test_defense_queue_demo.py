"""Демонстрация использует ту же очередь и изолированные данные."""
import importlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('local_queue_demo',ROOT/'scripts/run_queue_demo.py')
demo=importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
package=types.ModuleType('queue_demo_test')
package.__path__=[str(ROOT/'grading/defense_queue')]
sys.modules['queue_demo_test']=package
Store=importlib.import_module('queue_demo_test.storage').QueueStore
sync=importlib.import_module('queue_demo_test.sync').sync_group
ops=importlib.import_module('queue_demo_test.operations')
runtime=importlib.import_module('queue_demo_test.runtime')


class DemoTests(unittest.TestCase):
    def test_seed_has_live_class_future_class_and_available_later_lab(self):
        now=1794000000
        course,grid=demo.demo_data(now)
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'demo.sqlite3')
            demo.seed_queue(store,course,grid,now=now,sync_group=sync,teacher_view=ops.teacher_view)
            view=ops.teacher_view(store,demo.COURSE_ID,demo.GROUP_ID,course,deadlines_available=True,now=now,session_id='demo-current')
            self.assertEqual(view['stats']['waiting'],4)
            self.assertEqual(len(view['entries']),7)
            later=next(row for row in view['entries'] if row['github']=='demo-max')
            self.assertTrue(later['ready'])
            self.assertEqual(later['lab_key'],'2')
            self.assertFalse(later['will_attend'])
            next_view=runtime.queue_view(store,demo.COURSE_ID,demo.GROUP_ID,course,deadlines_available=True,now=now)
            self.assertEqual(next_view['session']['session_id'],'demo-next')
            self.assertEqual(sum(row['will_attend'] for row in next_view['entries']),2)
            self.assertFalse(next(row for row in next_view['entries'] if row['github']=='demo-ivan')['answered'])

if __name__=='__main__': unittest.main()
