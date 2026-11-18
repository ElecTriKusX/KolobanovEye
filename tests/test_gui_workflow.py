import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ke_gui import App
from tools.ke_coco import export
from tests.test_coco import make_run


class WorkflowTest(unittest.TestCase):
    def test_open_folder_always_available_and_does_not_require_last_run(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "dataset"
            with patch("ke_gui.DATASET_DIR", path), patch("ke_gui.sys.platform", "win32"), \
                 patch("ke_gui.os.startfile", create=True) as opened:
                App.open_result(SimpleNamespace())
                self.assertTrue(path.is_dir())
                opened.assert_called_once_with(path)

    def test_no_selected_maps_prevents_generation(self):
        app = SimpleNamespace(running=False, refresh_maps=lambda: None,
                              detected_maps=[], selected_maps=[])
        with patch("ke_gui.available_whitelist", return_value=[]), \
             patch("ke_gui.messagebox.showerror") as error:
            App.run(app)
            error.assert_called_once()


    def test_one_pipeline_process_and_error_propagation(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                calls = []
                dataset = Path(d) / 'dataset'
                class Process:
                    def __init__(self, args, **kw):
                        calls.append(args)
                        self.stdout = iter(['[KE] TEST\n'])
                    def wait(self):
                        return 1 if failure else 0
                app = SimpleNamespace(events=queue.Queue(), process=None)
                with patch('ke_gui.subprocess.Popen', Process), patch('ke_gui.DATASET_DIR', dataset):
                    App.worker(app, {'output': str(dataset)}, ['renderer'])
                events = []
                while not app.events.empty():
                    events.append(app.events.get_nowait())
                self.assertEqual(calls, [['renderer']])
                if failure:
                    self.assertEqual(events[-1][0], 'error')
                    self.assertIn('dataset/raw', events[-1][1])
                else:
                    self.assertEqual(events[-1], ('success', str(dataset / 'raw')))

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
            self.assertEqual(len(environments), 1)
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
