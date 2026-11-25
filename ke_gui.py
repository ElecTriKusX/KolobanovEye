"""Minimal Tk interface for the existing single-process sequence renderer."""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from ke_generate import DEFAULTS, INTS, FLOATS, validate_config
from tools.ke_gui_policy import RECOMMENDED, TESTED_RANGES, effective_config, visible_rows
from tools.ke_maps import scan_maps, load_whitelist, save_whitelist, initial_whitelist, available_whitelist

FIELDS = [
 ('dagor', 'Папка Dagor Engine'), ('game', 'Папка War Thunder'), ('output', 'Родительская папка результатов'),
 ('count', 'Количество кадров (итог)'), ('frames_per_position', 'Кадров на позицию'), ('seed', 'Seed'),
 ('width', 'Ширина'), ('height', 'Высота'), ('fov', 'Горизонтальный FOV, °'),
 ('smart_placement_cache', 'Кэш умной расстановки (2.7)'),
 ('scene_cache', 'Кэш интересных сцен (2.9.7)'),
 ('placement_mode', 'Где размещать танк'), ('map_margin_percent', 'Отступ от краёв карты, %'),
 ('tank_x', 'Центр поиска X'), ('tank_z', 'Центр поиска Z'), ('tank_yaw', 'Ориентация корпуса, °'),
 ('turret_yaw_min', 'Башня: от'), ('turret_yaw_max', 'Башня: до'),
 ('gun_pitch_min', 'Орудие: от'), ('gun_pitch_max', 'Орудие: до'),
 ('camera_attempts', 'Попыток камеры на кадр'), ('placement_radius', 'Радиус поиска точки'), ('placement_attempts', 'Попыток размещения'),
 ('minimum_ground_y', 'Минимальная высота земли Y'), ('max_height_range', 'Макс. перепад высот'), ('ground_offset', 'Отступ танка от земли'), ('camera_clearance', 'Мин. запас камеры над землёй'),
 ('distance_min', 'Горизонтальная дистанция: от'), ('distance_max', 'Горизонтальная дистанция: до'),
 ('camera_height_min', 'Высота камеры над землёй: от'), ('camera_height_max', 'Высота камеры над землёй: до'),
 ('clipmap_feedback', 'Обратная связь clipmap'), ('timeout', 'Таймаут, с'),
 ('min_warmup_renders', 'Мин. проходов прогрева (8–64)'),
 ('visibility_candidate_multiplier', 'Общий лимит кандидатов, ×'),
 ('avf_probe_size', 'AVF: размер пробы (0 — выкл.)'),
 ('avf_region', 'AVF: область пробы'),
 ('avf_backend', 'AVF: подсчёт пикселей'),
 ('avf_batch_size', 'AVF: камер в группе'),
 ('guided_sampling', 'AVF: направленный поиск (2.9)'),
 ('guided_prerender', 'AVF: отбраковка до рендера'),
]


MODE_LABELS = {'tank_map': 'Танковое поле', 'around_point': 'Вокруг точки'}
DATASET_DIR = Path(__file__).resolve().parent / 'dataset'

class App:
    def __init__(self, root):
        self.root = root
        root.title('KolobanovEye 2.0 — генератор датасета')
        root.geometry('1150x800')
        root.minsize(1000, 680)
        self.events = queue.Queue()
        self.process = None
        self.running = False
        self.values = {k: tk.StringVar(value=str(DEFAULTS[k])) for k, _ in FIELDS}
        self.keep_open = tk.BooleanVar(value=False)
        self.render_debug = tk.BooleanVar(value=False)
        self.capture_visibility_masks = tk.BooleanVar(value=DEFAULTS['capture_visibility_masks'])
        self.screen_position_weights = DEFAULTS['screen_position_weights'].copy()
        self.enforce_visibility_quotas = tk.BooleanVar(value=DEFAULTS['enforce_visibility_quotas'])
        self.online_quotas = tk.BooleanVar(value=True)
        self.visibility_weights = DEFAULTS['visibility_weights'].copy()
        self.search_occlusion = tk.BooleanVar(value=DEFAULTS['search_occlusion'])
        self.wait_frame_stability = tk.BooleanVar(value=DEFAULTS['wait_frame_stability'])
        self.wait_sky_convergence = tk.BooleanVar(value=DEFAULTS['wait_sky_convergence'])
        self.values['output'].set(str(DATASET_DIR))
        # The GUI opts into the new tank-map sampler; old CLI configurations keep their defaults.
        self.values['placement_mode'].set(MODE_LABELS['tank_map'])
        self.values['map_margin_percent'].set('0.0')
        self.detected_maps = []
        self.selected_maps = None
        self.map_summary = tk.StringVar(value='Поиск avg-карт…')
        main = ttk.Frame(root, padding=12)
        main.pack(fill='both', expand=True)
        map_bar = ttk.Frame(main)
        map_bar.pack(fill='x')
        ttk.Label(map_bar, textvariable=self.map_summary).pack(side='left')
        ttk.Button(map_bar, text='Карты / белый список…', command=self.open_maps).pack(side='left', padx=10)
        ttk.Button(map_bar, text='Обновить', command=self.refresh_maps).pack(side='left')
        profile_bar = ttk.Frame(main)
        profile_bar.pack(fill='x', pady=(8, 0))
        self.profile = tk.StringVar(value='Рекомендуемый')
        ttk.Label(profile_bar, text='Режим генерации:').pack(side='left')
        picker = ttk.Combobox(profile_bar, textvariable=self.profile, values=('Рекомендуемый', 'Ручная настройка'), state='readonly', width=22)
        picker.pack(side='left', padx=8)
        picker.bind('<<ComboboxSelected>>', lambda e: self.change_profile())
        ttk.Button(profile_bar, text='Восстановить проверенные параметры', command=self.reset_profile).pack(side='left')
        self.pipeline_summary = tk.StringVar()
        ttk.Label(main, textvariable=self.pipeline_summary, wraplength=980).pack(anchor='w', pady=(4, 0))
        ttk.Label(main, text='Датасет: KolobanovEye/dataset/raw (обновляется после успешной генерации); технические файлы: dataset/runs.').pack(anchor='w', pady=(2, 8))
        notebook = ttk.Notebook(main)
        notebook.pack(fill='x', pady=(4, 0))
        tabs = {}
        for name in ('Основное', 'Сцена и камера', 'Качество', 'Диагностика'):
            tabs[name] = ttk.Frame(notebook, padding=6)
            notebook.add(tabs[name], text=name)
            tabs[name].columnconfigure(0, weight=1)
            tabs[name].columnconfigure(1, weight=1)
        labels = dict(FIELDS)
        groups = [
            ('Основное', 'Пути и кэш', [('dagor',), ('game',), ('smart_placement_cache',), ('scene_cache',)]),
            ('Основное', 'Параметры датасета', [('count',), ('frames_per_position',), ('seed',),
                ('width','height','Размер, пиксели: ширина / высота'), ('fov',)]),
            ('Сцена и камера', 'Размещение танка', [('placement_mode',), ('map_margin_percent',),
                ('tank_x','tank_z','Центр поиска: X / Z'), ('placement_radius',), ('tank_yaw',),
                ('turret_yaw_min','turret_yaw_max','Башня относительно корпуса, °'),
                ('gun_pitch_min','gun_pitch_max','Наклон орудия, °'), ('minimum_ground_y',),
                ('max_height_range',), ('ground_offset',)]),
            ('Сцена и камера', 'Камера', [('distance_min','distance_max','Дистанция по горизонтали: от / до'),
                ('camera_height_min','camera_height_max','Высота над рельефом: от / до'), ('camera_clearance',)]),
            ('Качество', 'Поиск и квоты', [('camera_attempts',), ('placement_attempts',), ('visibility_candidate_multiplier',)]),
            ('Качество', 'Параметры AVF (ручной режим)', [('avf_probe_size',), ('avf_region',),
                ('avf_backend',), ('avf_batch_size',), ('guided_sampling',), ('guided_prerender',)]),
            ('Диагностика', 'Рендер и тайм-ауты', [('clipmap_feedback',), ('timeout',), ('min_warmup_renders',)])]
        self.widgets = {}
        self.rows = {}
        self.group_boxes = []
        def add_widget(parent, key):
            if key == 'placement_mode':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=tuple(MODE_LABELS.values()), state='readonly', width=18)
            elif key == 'guided_prerender':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('off', 'conservative'), state='readonly', width=12)
            elif key == 'guided_sampling':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('off', 'obstacle', 'fixed_camera', 'diverse'), state='readonly', width=12)
            elif key == 'avf_backend':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('gpu', 'cpu', 'verify'), state='readonly', width=12)
            elif key == 'avf_batch_size':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('1', '2', '3', '4'), state='readonly', width=12)
            elif key == 'avf_region':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('roi', 'full'), state='readonly', width=12)
            elif key == 'avf_probe_size':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('0', '64', '128', '256'), state='readonly', width=12)
            elif key == 'clipmap_feedback':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('software', 'hardware'), state='readonly', width=12)
            else:
                widget = ttk.Entry(parent, textvariable=self.values[key], width=12)
            widget.pack(side='left', fill='x', expand=True)
            self.widgets[key] = widget
            if key in ('dagor', 'game', 'output', 'smart_placement_cache', 'scene_cache'):
                ttk.Button(parent, text='…', width=3, command=lambda k=key: self.choose_directory(k)).pack(side='left')
        tab_counts = {name: 0 for name in tabs}
        for tab, title, rows in groups:
            idx = tab_counts[tab]; tab_counts[tab] += 1
            box = ttk.LabelFrame(tabs[tab], text=title, padding=8)
            box.grid(row=idx//2, column=idx%2, sticky='nsew', padx=4, pady=3)
            box.columnconfigure(0, weight=1)
            keys = []
            for i, row in enumerate(rows):
                container = ttk.Frame(box)
                container.grid(row=i, column=0, sticky='ew', pady=2)
                self.rows[row[0]] = container
                keys.append(row[0])
                label = labels[row[0]] if len(row) == 1 else row[2]
                ttk.Label(container, text=label, width=32).pack(side='left')
                add_widget(container, row[0])
                if len(row) > 1:
                    ttk.Label(container, text=' — ').pack(side='left')
                    add_widget(container, row[1])
            self.group_boxes.append((box, keys))
        def checks(tab, title, options):
            idx = tab_counts[tab]; tab_counts[tab] += 1
            box = ttk.LabelFrame(tabs[tab], text=title, padding=8)
            box.grid(row=idx//2, column=idx%2, sticky='nsew', padx=4, pady=3)
            keys = []
            for i,(key,label,var) in enumerate(options):
                row = ttk.Checkbutton(box, text=label, variable=var)
                row.grid(row=i, column=0, sticky='w', pady=3)
                self.rows[key] = row
                keys.append(key)
            self.group_boxes.append((box,keys))
        checks('Качество', 'Управление конвейером (ручной режим)', [
            ('capture_visibility_masks','Маски видимости',self.capture_visibility_masks),
            ('enforce_visibility_quotas','Строгие квоты видимости',self.enforce_visibility_quotas),
            ('online_quotas','Online-добор в одном DaEditor',self.online_quotas),
            ('search_occlusion','Поиск перекрытий',self.search_occlusion)])
        checks('Диагностика', 'Режимы отладки', [
            ('wait_frame_stability','Ждать стабильности изображения',self.wait_frame_stability),
            ('wait_sky_convergence','Ждать сходимости освещения неба',self.wait_sky_convergence),
            ('render_debug','Диагностика рендера (MRT, глубина)',self.render_debug),
            ('keep_open','Оставить редактор открытым',self.keep_open)])
        self.cache_status = tk.StringVar()
        ttk.Label(tabs['Основное'], textvariable=self.cache_status, wraplength=950).grid(
            row=2, column=0, columnspan=2, sticky='w', padx=6, pady=5)
        self.quota_status = tk.StringVar()
        ttk.Label(tabs['Качество'], textvariable=self.quota_status, wraplength=950).grid(
            row=2, column=0, columnspan=2, sticky='w', padx=6, pady=5)
        self._sync_suspended = True
        for var in list(self.values.values()) + [self.keep_open,self.render_debug,
            self.capture_visibility_masks,self.enforce_visibility_quotas,self.online_quotas,
            self.search_occlusion,self.wait_frame_stability,self.wait_sky_convergence]:
            var.trace_add('write', lambda *_: self.sync_form())
        self._sync_suspended = False
        self.reset_profile()
        buttons = ttk.Frame(main); buttons.pack(fill='x', pady=8)
        self.run_button = ttk.Button(buttons, text='Создать и проверить датасет', command=self.run)
        self.run_button.pack(side='left')
        menu_button = ttk.Menubutton(buttons, text='Настройки')
        menu = tk.Menu(menu_button, tearoff=False)
        menu.add_command(label='Открыть настройки…', command=self.open_config)
        menu.add_command(label='Сохранить настройки…', command=self.save_config)
        menu_button['menu'] = menu
        menu_button.pack(side='left', padx=8)
        self.open_button = ttk.Button(buttons, text='Открыть папку', command=self.open_result)
        self.open_button.pack(side='left', padx=5)
        ttk.Button(buttons, text='Провести анализ', command=self.analyze_dataset).pack(side='left', padx=5)
        ttk.Button(buttons, text='Анализ объектов', command=self.analyze_objects).pack(side='left', padx=5)
        ttk.Button(buttons, text='Копировать журнал', command=self.copy_log).pack(side='left')
        self.state = tk.StringVar(value='Готово')
        ttk.Label(main, textvariable=self.state).pack(anchor='w')
        self.progress = ttk.Progressbar(main, mode='determinate', maximum=1, value=0)
        self.progress.pack(fill='x', pady=(3, 0))
        journal = ttk.Frame(main)
        journal.pack(fill='both', expand=True, pady=(4, 0))
        self.log = tk.Text(journal, height=10, wrap='word', state='disabled')
        scroll = ttk.Scrollbar(journal, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        root.after(100, self.poll)
        root.after(0, self.refresh_maps)

    def raw_form(self):
        data = {key: var.get().strip() for key, var in self.values.items()}
        data['placement_mode'] = next((k for k,label in MODE_LABELS.items()
                                      if label == data['placement_mode']), data['placement_mode'])
        for key in ('keep_open','render_debug','capture_visibility_masks','enforce_visibility_quotas',
                    'search_occlusion','wait_frame_stability','wait_sky_convergence'):
            data[key] = getattr(self,key).get()
        data['visibility_quota_mode'] = 'online' if self.online_quotas.get() else 'refill'
        return data

    def apply_form(self, values):
        self._sync_suspended = True
        try:
            for k,v in values.items():
                if k in self.values:
                    self.values[k].set(MODE_LABELS.get(v,v) if k == 'placement_mode' else str(v))
                elif k == 'visibility_quota_mode':
                    self.online_quotas.set(v == 'online')
                elif isinstance(getattr(self,k,None),tk.BooleanVar):
                    getattr(self,k).set(v)
        finally:
            self._sync_suspended = False
        self.sync_form()

    def change_profile(self):
        if self.profile.get() == 'Рекомендуемый':
            self.apply_form(RECOMMENDED)
        else:
            self.sync_form()

    def reset_profile(self):
        self.profile.set('Рекомендуемый')
        self.apply_form({**RECOMMENDED, **TESTED_RANGES})

    def sync_form(self):
        if getattr(self,'_sync_suspended',True):
            return
        recommended = self.profile.get() == 'Рекомендуемый'
        raw = self.raw_form()
        effective = effective_config(raw,recommended)
        hidden = visible_rows(raw,recommended)
        for key,row in self.rows.items():
            if key in hidden:
                row.grid_remove()
            else:
                row.grid()
        for box,keys in self.group_boxes:
            if all(key in hidden for key in keys):
                box.grid_remove()
            else:
                box.grid()
        if recommended:
            self.pipeline_summary.set('Конвейер 2.9: маски • строгие квоты • online • AVF GPU • разнообразные сцены. Предфильтр отключён до отдельной проверки.')
        else:
            self.pipeline_summary.set('Ручная настройка: доступны только совместимые параметры. Скрытые зависимые значения не участвуют в запуске.')
        path = effective['smart_placement_cache']
        maps = available_whitelist(self.detected_maps, self.selected_maps or [])
        if recommended and not path:
            status = 'Требуется кэш умной расстановки: выполните «Анализ объектов» для выбранных карт и укажите папку результата.'
        elif path:
            required = ['placement-candidates.json','objects-native.tsv']
            if str(effective['avf_probe_size']) != '0':
                required.append('objects.jsonl')
            missing = [Path(mid).stem for mid in maps if not all(
                (Path(path)/Path(mid).stem/name).is_file() for name in required)]
            status = 'Кэш: отсутствуют файлы для ' + ', '.join(missing) if missing else f'Кэш: найдены файлы для {len(maps)} выбранных карт; проверка отпечатков выполняется при запуске.'
        else:
            status = 'Умная расстановка не используется в текущем ручном режиме.'
        self.cache_status.set(status)
        weights = self.visibility_weights
        self.quota_status.set('Квоты: ' + ' / '.join(str(weights[k]) for k in ('open','light','moderate','heavy'))
                              if effective['enforce_visibility_quotas'] else 'Строгий добор выключен.')

    def open_result(self):
        try:
            DATASET_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform != 'win32':
                raise OSError('Открытие папки доступно в Windows')
            os.startfile(DATASET_DIR)
        except OSError as exc:
            messagebox.showerror('Не удалось открыть папку', str(exc))

    def analyze_dataset(self):
        if self.running:
            return
        if not (DATASET_DIR / 'raw' / 'manifest.json').is_file():
            messagebox.showerror('Анализ', 'Сначала сгенерируйте dataset/raw.')
            return
        self.start_tool('ke_analysis.py', ['--dataset', str(DATASET_DIR)])


    def analyze_objects(self):
        if self.running:
            return
        try:
            selected = available_whitelist(self.detected_maps, self.selected_maps or [])
            if not selected:
                raise ValueError('Выберите хотя бы одну карту')
            args = ['--dagor', self.values['dagor'].get(), '--game', self.values['game'].get(),
                    '--output', str(DATASET_DIR)]
            for mid in selected:
                args += ['--map', mid]
            self.start_tool('ke_objects.py', args)
        except ValueError as exc:
            messagebox.showerror('Анализ объектов', str(exc))

    def copy_log(self):
        try:
            selected = self.log.get('sel.first', 'sel.last')
        except tk.TclError:
            selected = self.log.get('1.0', 'end-1c')
        self.root.clipboard_clear()
        self.root.clipboard_append(selected)
        self.state.set('Журнал скопирован в буфер обмена')

    def on_close(self):
        if self.running:
            messagebox.showinfo('Генерация выполняется', 'Дождитесь завершения генерации, прежде чем закрыть окно.')
        else:
            self.root.destroy()

    def choose_directory(self, key):
        path = filedialog.askdirectory(title='Выберите папку')
        if path:
            self.values[key].set(path)
            if key == 'game':
                self.refresh_maps()

    def refresh_maps(self):
        """Inventory only: no Dagor process and no game file modification."""
        try:
            entries = scan_maps(self.values['game'].get().strip())
            if self.selected_maps is None:
                saved = load_whitelist()
                self.selected_maps = initial_whitelist(entries) if saved is None else saved
            self.detected_maps = entries
            valid = available_whitelist(entries, self.selected_maps)
            failures = sum(item['status'] != 'header_ok' for item in entries)
            stale = len(self.selected_maps) - len(valid)
            self.map_summary.set(
                f'Карт avg: {len(entries)} | В белом списке: {len(valid)}'
                + (f' | Проблемных/не найденных: {failures + stale}' if failures + stale else '')
            )
        except (OSError, ValueError) as exc:
            self.detected_maps = []
            self.map_summary.set(f'Ошибка обнаружения карт: {exc}')
        self.sync_form()

    def open_maps(self):
        self.refresh_maps()
        window = tk.Toplevel(self.root)
        window.title('Карты War Thunder — белый список')
        window.geometry('690x550')
        window.transient(self.root)
        ttk.Label(window, text='Файлы levels/avg_*.bin. Заголовок DBLD не гарантирует успешный рендер.').pack(
            anchor='w', padx=12, pady=(10, 4))
        ttk.Label(window, text='Выбор сохраняется локально. Поддержка рендера других карт добавляется поэтапно.').pack(
            anchor='w', padx=12)
        body = ttk.Frame(window, padding=12)
        body.pack(fill='both', expand=True)
        canvas = tk.Canvas(body, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        rows = ttk.Frame(canvas)
        rows.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.configure(yscrollcommand=scroll.set)
        frame_id = canvas.create_window((0, 0), window=rows, anchor='nw')
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(frame_id, width=e.width))
        canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        checked = {}
        selected = {name.casefold() for name in (self.selected_maps or [])}
        for item in self.detected_maps:
            name = item['id']
            ok = item['status'] == 'header_ok'
            var = tk.BooleanVar(value=ok and name.casefold() in selected)
            checked[name] = var
            title = f"{name}  —  {item['status']}"
            ttk.Checkbutton(rows, text=title, variable=var, state='normal' if ok else 'disabled').pack(
                anchor='w', pady=2)
        if not self.detected_maps:
            ttk.Label(rows, text='Карты не обнаружены. Проверьте путь War Thunder.').pack(anchor='w')
        actions = ttk.Frame(window, padding=(12, 0, 12, 12))
        actions.pack(fill='x')
        def set_all(value):
            for item in self.detected_maps:
                checked[item['id']].set(value and item['status'] == 'header_ok')
        def save():
            chosen = [item['id'] for item in self.detected_maps if checked[item['id']].get()
                      and item['status'] == 'header_ok']
            try:
                save_whitelist(chosen)
            except (OSError, ValueError) as exc:
                messagebox.showerror('Белый список', str(exc), parent=window)
                return
            self.selected_maps = chosen
            self.refresh_maps()
            window.destroy()
        ttk.Button(actions, text='Выбрать все', command=lambda: set_all(True)).pack(side='left')
        ttk.Button(actions, text='Снять все', command=lambda: set_all(False)).pack(side='left', padx=5)
        ttk.Button(actions, text='Сохранить', command=save).pack(side='right')
        ttk.Button(actions, text='Отмена', command=window.destroy).pack(side='right', padx=6)

    def get_config(self):
        recommended = self.profile.get() == 'Рекомендуемый'
        data = effective_config(self.raw_form(), recommended)
        if recommended and not data['smart_placement_cache']:
            raise ValueError('Укажите кэш умной расстановки (анализ объектов выбранных карт).')
        for key,value in list(data.items()):
            try:
                data[key] = int(value) if key in INTS else float(str(value).replace(',', '.')) if key in FLOATS else value
            except ValueError as exc:
                raise ValueError(f'{dict(FIELDS).get(key,key)}: неверное число') from exc
        data['output'] = str(DATASET_DIR)
        data['capture_last_clip'] = False
        data['capture_tank_map'] = True
        data['screen_position_weights'] = self.screen_position_weights.copy()
        data['visibility_weights'] = self.visibility_weights.copy()
        data['map_ids'] = available_whitelist(self.detected_maps, self.selected_maps or [])
        if not data['map_ids']:
            raise ValueError('Выберите хотя бы одну карту для генерации')
        data['map_id'] = data['map_ids'][0]
        if data['smart_placement_cache']:
            required = ['placement-candidates.json', 'objects-native.tsv']
            if data['avf_probe_size']:
                required.append('objects.jsonl')
            missing = [Path(mid).stem for mid in data['map_ids'] if not all(
                (Path(data['smart_placement_cache'])/Path(mid).stem/name).is_file() for name in required)]
            if missing:
                raise ValueError('Кэш умной расстановки не содержит выбранные карты: ' + ', '.join(missing))
        return validate_config(data)

    def open_config(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not path: return
        try:
            data = validate_config(json.loads(Path(path).read_text(encoding='utf-8')))
            self.profile.set('Ручная настройка')
            self.screen_position_weights = data['screen_position_weights'].copy()
            self.visibility_weights = data['visibility_weights'].copy()
            self.apply_form(data)
            saved_maps = data.get('map_ids') or [data['map_id']]
            self.selected_maps = saved_maps
            self.refresh_maps()
        except (OSError, ValueError) as exc: messagebox.showerror('Ошибка настроек', str(exc))

    def save_config(self):
        try: data = self.get_config()
        except ValueError as exc:
            messagebox.showerror('Ошибка настроек', str(exc)); return
        path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON', '*.json')])
        if path:
            try: Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            except OSError as exc: messagebox.showerror('Ошибка сохранения', str(exc))

    def run(self):
        if self.running:
            return
        self.refresh_maps()
        active = available_whitelist(self.detected_maps, self.selected_maps or [])
        if not active:
            messagebox.showerror('Карты не выбраны',
                'Выберите одну или более доступных avg_*.bin в списке карт.')
            return
        try:
            data = self.get_config()
        except ValueError as exc:
            messagebox.showerror('Ошибка настроек', str(exc))
            return
        replace_raw = (DATASET_DIR / 'raw').exists()
        if replace_raw and not messagebox.askyesno(
                'Заменить исходный датасет?',
                'dataset/raw уже существует.\n\n'
                'После успешной генерации новый датасет заменит предыдущий raw. '
                'Если генерация завершится с ошибкой, прежний raw сохранится.\n\n'
                'Продолжить?'):
            return
        cmd = [sys.executable, '-u', str(Path(__file__).resolve().with_name('ke_dataset.py')),
               '--config-json', json.dumps(data, ensure_ascii=False)]
        if replace_raw:
            cmd.append('--replace-raw')
        self.running = True
        self.run_button.config(state='disabled')
        self.progress.configure(maximum=data['count'], value=0)
        self.state.set(f"Генерация: 0/{data['count']} кадров, карт: {len(data['map_ids'])}")
        threading.Thread(target=self.worker, args=(data, cmd), daemon=True).start()

    def start_tool(self, script, arguments):
        if self.running: return
        self.running = True; self.run_button.config(state='disabled'); self.state.set('Проверка выполняется…')
        self.progress.configure(maximum=1, value=0)
        cmd = [sys.executable, '-u', '-m', f'tools.{Path(script).stem}', *arguments]
        threading.Thread(target=self.tool_worker, args=(cmd,), daemon=True).start()

    def tool_worker(self, cmd):
        try:
            proc = subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            for line in proc.stdout: self.events.put(('line', line))
            self.events.put(('done', proc.wait()))
        except OSError as exc: self.events.put(('error', str(exc)))

    def worker(self, data, cmd):
        try:
            self.events.put(('line', '\n[KE] Создание dataset/raw и проверка кадров\n'))
            proc = subprocess.Popen(
                cmd, cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            self.process = proc
            for line in proc.stdout:
                self.events.put(('line', line))
            code = proc.wait()
            if code:
                raise RuntimeError(f'Генерация dataset/raw завершилась с кодом {code}. См. dataset/runs.')
            self.events.put(('success', str(DATASET_DIR / 'raw')))
        except (OSError, ValueError, RuntimeError) as exc:
            self.events.put(('error', str(exc)))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'line':
                    self.log.config(state='normal'); self.log.insert('end', value); self.log.see('end'); self.log.config(state='disabled')
                    if '[KE] ONLINE_FRAME ' in value:
                        match = re.search(r'accepted=(\d+)/(\d+).*?attempts=(\d+)', value)
                        if match:
                            accepted,total,attempts = map(int,match.groups())
                            self.progress.configure(maximum=total,value=accepted)
                            self.state.set(f'Генерация: {accepted}/{total} кадров · попыток: {attempts}')
                    elif '[KE] ONLINE_OK ' in value:
                        self.state.set('Квоты набраны; завершение экспорта…')
                else:
                    self.running = False; self.run_button.config(state='normal')
                    if kind == 'success':
                        self.progress.configure(value=self.progress.cget('maximum'))
                        self.state.set(f'Готово: {value}')
                        messagebox.showinfo('Готово', f'Кадры и метаданные: {value}\nCOCO: {value}\\annotations\\instances.json\n\nПроверьте manifest.json и visibility-quotas.json. При отключённых масках рамка приблизительная.')
                    else:
                        self.state.set('Завершено' if kind == 'done' and value == 0 else f'Ошибка: {value}. Проверьте журнал.')
        except queue.Empty: pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk(); App(root); root.mainloop()

if __name__ == '__main__': main()
