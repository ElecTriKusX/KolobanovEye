import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ke_gui import App, next_numbered_directory
from ke_coco import export
from test_coco import make_run


class WorkflowTest(unittest.TestCase):
    def test_numbered_paths_skip_existing_files_and_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'ke-generate-01').mkdir()
            (p/'ke-generate-02').write_text('existing')
            self.assertEqual(next_numbered_directory(p).name, 'ke-generate-03')
            self.assertFalse((p/'ke-generate-03').exists())
            with self.assertRaises(ValueError): next_numbered_directory(p/'missing')

    def test_pipeline_in_order_and_stops_on_failure(self):
        for failure in (None, 1):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                run = Path(d)/'ke-generate-01'; calls = []
                class Process:
                    def __init__(self, args, **kw):
                        calls.append(args)
                        if len(calls) == 1: run.mkdir()
                        self.stdout = iter(['line\n'])
                    def wait(self): return 1 if failure == len(calls)-1 else 0
                app = SimpleNamespace(events=queue.Queue(), process=None)
                with patch('ke_gui.subprocess.Popen', Process):
                    App.worker(app, {'output':str(run), 'max_height_range':0.5}, ['renderer'])
                events=[]
                while not app.events.empty(): events.append(app.events.get_nowait())
                if failure is None:
                    self.assertEqual(len(calls), 4)
                    self.assertEqual(events[-1], ('success', str(run)))
                    self.assertTrue((run/'generation-config.json').exists())
                    self.assertIn('ke_validate.py', calls[1][2])
                    self.assertIn('ke_terrain_audit.py', calls[2][2])
                    self.assertIn('ke_coco.py', calls[3][2])
                else:
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(events[-1][0], 'error')
                    self.assertIn('Проверка серии', events[-1][1])

    def test_coco_can_be_inside_run_but_not_images(self):
        with tempfile.TemporaryDirectory() as d:
            run = make_run(Path(d), 'run', 10, 20)
            export([run], run/'coco')
            data=json.loads((run/'coco/all.json').read_text())
            self.assertTrue((run/'coco'/data['images'][0]['file_name']).is_file())
            with self.assertRaises(ValueError): export([run], run/'images'/ 'bad')

    def test_worker_forces_utf8_output(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / 'ke-generate-01'; environments = []
            class Process:
                def __init__(self, args, **kw):
                    environments.append(kw['env'])
                    if len(environments) == 1: run.mkdir()
                    self.stdout = iter([])
                def wait(self): return 0
            app = SimpleNamespace(events=queue.Queue(), process=None)
            with patch('ke_gui.subprocess.Popen', Process):
                App.worker(app, {'output': str(run), 'max_height_range':0.5}, ['renderer'])
            self.assertEqual(len(environments), 4)
            self.assertTrue(all(e['PYTHONIOENCODING'] == 'utf-8' for e in environments))

    def test_copy_selected_or_entire_log(self):
        import tkinter as tk
        class Log:
            def __init__(self, selection): self.selection = selection
            def get(self, a, b):
                if a == 'sel.first':
                    if self.selection is None: raise tk.TclError('no selection')
                    return self.selection
                return 'весь журнал'
        class Root:
            def clipboard_clear(self): self.value = ''
            def clipboard_append(self, s): self.value += s
        for selection, expected in [('выделено', 'выделено'), (None, 'весь журнал')]:
            root = Root(); state = SimpleNamespace(set=lambda x: None)
            App.copy_log(SimpleNamespace(log=Log(selection), root=root, state=state))
            self.assertEqual(root.value, expected)
