"""Minimal Tk interface for the existing single-process sequence renderer."""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path

from ke_generate import DEFAULTS, INTS, FLOATS, validate_config, command
from ke_maps import scan_maps, load_whitelist, save_whitelist, initial_whitelist, available_whitelist

FIELDS = [
 ('dagor', 'Папка Dagor Engine'), ('game', 'Папка War Thunder'), ('output', 'Родительская папка результатов'),
 ('count', 'Количество кадров (1–1000)'), ('frames_per_position', 'Кадров на позицию'), ('seed', 'Seed'),
 ('width', 'Ширина'), ('height', 'Высота'), ('fov', 'Горизонтальный FOV, °'),
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
]


MODE_LABELS = {'whole_map': 'Весь ландшафт', 'tank_map': 'Танковое поле', 'around_point': 'Вокруг точки'}

def next_numbered_directory(parent, prefix='ke-generate-'):
    parent = Path(parent)
    if not parent.is_dir():
        raise ValueError('Родительская папка результатов не существует')
    for number in range(1, 1000000):
        candidate = parent / f'{prefix}{number:02d}'
        if not candidate.exists():
            return candidate
    raise ValueError('Не удалось подобрать свободное имя папки')


class App:
    def __init__(self, root):
        self.root = root
        root.title('KolobanovEye — тестовый генератор')
        root.geometry('1250x850')
        root.minsize(1000, 720)
        self.events = queue.Queue()
        self.process = None
        self.running = False
        self.values = {k: tk.StringVar(value=str(DEFAULTS[k])) for k, _ in FIELDS}
        self.keep_open = tk.BooleanVar(value=False)
        self.capture_last_clip = tk.BooleanVar(value=False)
        self.capture_tank_map = tk.BooleanVar(value=True)
        self.render_debug = tk.BooleanVar(value=False)
        self.wait_frame_stability = tk.BooleanVar(value=DEFAULTS['wait_frame_stability'])
        self.wait_sky_convergence = tk.BooleanVar(value=DEFAULTS['wait_sky_convergence'])
        self.scan_buildings = tk.BooleanVar(value=False)
        self.values['output'].set(str(Path(DEFAULTS['output']).parent))
        # The GUI opts into the new tank-map sampler; old CLI configurations keep their defaults.
        self.values['placement_mode'].set(MODE_LABELS['tank_map'])
        self.values['map_margin_percent'].set('0.0')
        self.last_run = None
        self.detected_maps = []
        self.selected_maps = None
        self.active_map = tk.StringVar(value=DEFAULTS['map_id'])
        self.map_summary = tk.StringVar(value='Поиск avg-карт…')
        main = ttk.Frame(root, padding=12)
        main.pack(fill='both', expand=True)
        map_bar = ttk.Frame(main)
        map_bar.pack(fill='x')
        ttk.Label(map_bar, textvariable=self.map_summary).pack(side='left')
        ttk.Button(map_bar, text='Карты / белый список…', command=self.open_maps).pack(side='left', padx=10)
        ttk.Button(map_bar, text='Обновить', command=self.refresh_maps).pack(side='left')
        ttk.Label(map_bar, text='Текущий запуск:').pack(side='left', padx=(15, 4))
        self.active_map_combo = ttk.Combobox(map_bar, textvariable=self.active_map,
                                             state='readonly', width=27)
        self.active_map_combo.pack(side='left')
        ttk.Label(main, text='Одна карта: текущий выбор; несколько: равные квоты по whitelist. Модель: Tiger H1.').pack(anchor='w')
        ttk.Label(main, text='Выберите папку результатов: ke-generate-01, 02… создаются автоматически.').pack(anchor='w', pady=(2, 8))
        form = ttk.Frame(main)
        form.pack(fill='x')
        labels = dict(FIELDS)
        groups = [
            ('Пути', [('dagor',), ('game',), ('output',)]),
            ('Кадры', [('count',), ('frames_per_position',), ('seed',), ('width','height','Размер, пиксели: ширина / высота'), ('fov',)]),
            ('Танк и место', [('placement_mode',), ('map_margin_percent',), ('tank_x','tank_z','Центр поиска: X / Z'), ('tank_yaw',), ('turret_yaw_min','turret_yaw_max','Башня относительно корпуса, °'), ('gun_pitch_min','gun_pitch_max','Наклон орудия, °'), ('placement_radius',), ('minimum_ground_y',), ('max_height_range',), ('ground_offset',)]),
            ('Камера', [('distance_min','distance_max','Дистанция по горизонтали: от / до'),
                        ('camera_height_min','camera_height_max','Высота над рельефом: от / до'), ('camera_clearance',)]),
            ('Служебные параметры', [('camera_attempts',), ('placement_attempts',), ('clipmap_feedback',), ('timeout',), ('min_warmup_renders',)])]
        self.widgets = {}
        def add_widget(parent, key):
            if key == 'placement_mode':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=tuple(MODE_LABELS.values()), state='readonly', width=18)
            elif key == 'clipmap_feedback':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('software', 'hardware'), state='readonly', width=12)
            else:
                widget = ttk.Entry(parent, textvariable=self.values[key], width=12)
            widget.pack(side='left', fill='x', expand=True)
            self.widgets[key] = widget
            if key in ('dagor', 'game', 'output'):
                ttk.Button(parent, text='…', width=3, command=lambda k=key: self.choose_directory(k)).pack(side='left')
        for idx, (title, rows) in enumerate(groups):
            box = ttk.LabelFrame(form, text=title, padding=8)
            box.grid(row=idx//2, column=idx%2, sticky='nsew', padx=(0,8), pady=3)
            for row in rows:
                container = ttk.Frame(box); container.pack(fill='x', pady=2)
                label = labels[row[0]] if len(row) == 1 else row[2]
                ttk.Label(container, text=label, width=38).pack(side='left')
                add_widget(container, row[0])
                if len(row) > 1:
                    ttk.Label(container, text=' — ').pack(side='left')
                    add_widget(container, row[1])
        form.columnconfigure(0, weight=1); form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(main); buttons.pack(fill='x', pady=8)
        self.run_button = ttk.Button(buttons, text='Создать и проверить датасет', command=self.run)
        self.run_button.pack(side='left')
        ttk.Checkbutton(buttons, text='Оставить редактор открытым', variable=self.keep_open).pack(side='left', padx=8)
        menu_button = ttk.Menubutton(buttons, text='Дополнительно')
        menu = tk.Menu(menu_button, tearoff=False)
        menu.add_command(label='Проверить существующую серию…', command=self.check_run)
        menu.add_command(label='Проверить рельеф серии…', command=self.check_terrain)
        menu.add_command(label='Восстановить метаданные после сбоя…', command=self.recover_run)
        menu.add_checkbutton(label='Сохранять обзорную текстуру танкового поля', variable=self.capture_tank_map)
        menu.add_checkbutton(label='Диагностика: last_clip всего ландшафта (старый режим)', variable=self.capture_last_clip)
        menu.add_checkbutton(label='Найти предполагаемую застройку', variable=self.scan_buildings)
        menu.add_command(label='Позиции на карте…', command=self.fixed_clip_map)
        menu.add_checkbutton(label='Ждать стабильности изображения', variable=self.wait_frame_stability)
        menu.add_checkbutton(label='Ждать сходимости освещения неба', variable=self.wait_sky_convergence)
        menu.add_checkbutton(label='Диагностика рендера (MRT, глубина и другие буферы)', variable=self.render_debug)
        menu.add_command(label='Показать рамки на копиях…', command=self.preview_run)
        menu.add_command(label='Экспортировать COCO отдельно…', command=self.coco_run)
        menu.add_separator()
        menu.add_command(label='Открыть настройки…', command=self.open_config)
        menu.add_command(label='Сохранить настройки…', command=self.save_config)
        menu_button['menu'] = menu
        menu_button.pack(side='left', padx=8)
        ttk.Button(buttons, text='? Справка', command=self.show_help).pack(side='left')
        self.open_button = ttk.Button(buttons, text='Открыть папку', command=self.open_result, state='disabled')
        self.open_button.pack(side='left', padx=5)
        ttk.Button(buttons, text='Копировать журнал', command=self.copy_log).pack(side='left')
        self.state = tk.StringVar(value='Готово')
        ttk.Label(main, textvariable=self.state).pack(anchor='w')
        self.log = tk.Text(main, height=12, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True, pady=(4, 0))
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        root.after(100, self.poll)
        root.after(0, self.refresh_maps)

    def open_result(self):
        if not self.last_run:
            return
        try:
            if not Path(self.last_run).is_dir():
                raise OSError('Папка результата не найдена')
            if sys.platform != 'win32':
                raise OSError('Открытие папки доступно в Windows')
            os.startfile(self.last_run)
        except OSError as exc:
            messagebox.showerror('Не удалось открыть папку', str(exc))

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
            self.active_map_combo['values'] = tuple(valid)
            if self.active_map.get() not in valid:
                self.active_map.set(valid[0] if valid else '')
            failures = sum(item['status'] != 'header_ok' for item in entries)
            stale = len(self.selected_maps) - len(valid)
            self.map_summary.set(
                f'Карт avg: {len(entries)} | В белом списке: {len(valid)}'
                + (f' | Проблемных/не найденных: {failures + stale}' if failures + stale else '')
            )
        except (OSError, ValueError) as exc:
            self.detected_maps = []
            self.map_summary.set(f'Ошибка обнаружения карт: {exc}')
            self.active_map_combo['values'] = ()
            self.active_map.set('')

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
        data = {}
        for key, var in self.values.items():
            value = var.get().strip()
            if key == 'placement_mode':
                value = next((k for k, label in MODE_LABELS.items() if label == value), value)
            try:
                data[key] = int(value) if key in INTS else float(value.replace(',', '.')) if key in FLOATS else value
            except ValueError as exc:
                raise ValueError(f'{key}: неверное число') from exc
        if not data['output']:
            raise ValueError('Укажите родительскую папку результатов')
        data['output'] = str(next_numbered_directory(data['output']))
        data['keep_open'] = self.keep_open.get()
        data['capture_last_clip'] = self.capture_last_clip.get()
        data['capture_tank_map'] = self.capture_tank_map.get()
        data['render_debug'] = self.render_debug.get()
        data['wait_frame_stability'] = self.wait_frame_stability.get()
        data['wait_sky_convergence'] = self.wait_sky_convergence.get()
        data['scan_buildings'] = self.scan_buildings.get()
        data['map_id'] = self.active_map.get()
        data['map_ids'] = available_whitelist(self.detected_maps, self.selected_maps or [])
        return validate_config(data)

    def open_config(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not path: return
        try:
            data = validate_config(json.loads(Path(path).read_text(encoding='utf-8')))
            self.keep_open.set(data['keep_open'])
            self.capture_last_clip.set(data['capture_last_clip'])
            self.capture_tank_map.set(data['capture_tank_map'])
            self.render_debug.set(data['render_debug'])
            self.wait_frame_stability.set(data['wait_frame_stability'])
            self.wait_sky_convergence.set(data['wait_sky_convergence'])
            self.scan_buildings.set(data['scan_buildings'])
            for k, v in data.items():
                if k in self.values:
                    self.values[k].set(str(Path(v).parent) if k == 'output' else MODE_LABELS.get(v, v) if k == 'placement_mode' else str(v))
            self.refresh_maps()
            if data['map_id'] in available_whitelist(self.detected_maps, self.selected_maps or []):
                self.active_map.set(data['map_id'])
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
        if self.running: return
        self.refresh_maps()
        active = available_whitelist(self.detected_maps, self.selected_maps or [])
        if not active or self.active_map.get() not in active:
            messagebox.showerror('Нет активной карты',
                'Выберите и включите в белый список хотя бы одну карту avg_*.bin.')
            return
        try:
            data = self.get_config()
            cmd = (command(data) if len(data['map_ids']) <= 1 else
                   [sys.executable, '-u', str(Path(__file__).resolve().with_name('ke_multi_generate.py')),
                    '--config-json', json.dumps(data, ensure_ascii=False)])
        except ValueError as exc:
            messagebox.showerror('Ошибка настроек', str(exc)); return
        self.running = True; self.run_button.config(state='disabled'); self.state.set(f"Генерация {data['map_id']}: {data['output']}")
        threading.Thread(target=self.worker, args=(data, cmd), daemon=True).start()

    def show_help(self):
        messagebox.showinfo('Справка',
            'Открыть папку — показывает последний успешный результат в Проводнике. Копировать журнал — копирует выделенный текст или весь журнал.\n\n'
            'При открытом редакторе проверка и экспорт начнутся после закрытия его окна.\n\n'
            'Основная кнопка: создаёт новую папку ke-generate-XX, рендерит кадры, проверяет их и создаёт COCO в подпапке coco.\n\n'
            'Проверить серию — проверяет файлы и границы рамок существующей серии.\n'
            'Проверить рельеф — оценивает перепад heightmap под углами габарита; воду и препятствия не проверяет.\n'
            'Восстановление — проверяет уже записанные кадры и создаёт отдельные метаданные; рендер не продолжает.\n'
            'Рамки — создаёт отдельные копии кадров с зелёной рамкой.\n'
            'COCO — создаёт JSON-разметку для существующей серии без копирования изображений.\n\n'
            'COCO — формат данных для обучения детектора; здесь рамка приблизительная, видимость не проверена. '
            'Папку серии нельзя отделять от файлов coco: в JSON сохранены относительные пути к кадрам.\n\n'
            'Где размещать: «Танковое поле» — координаты из tankMapCoord0/1 (максимальный вписанный квадрат); «Весь ландшафт» — старый режим; «Вокруг точки» — вручную указанный центр.\n\n'
            'Минимальная высота земли Y: для Normandy по замеру вода около Y=0; 0.5 задаёт запас. Проверяются центр и углы габарита. Водоёмы выше порога могут остаться.\n\n'
            'X и Z — координаты центра поиска точки на карте; при радиусе 0 танк ставится именно туда. Поворот — направление корпуса, в градусах.\n\n'
            'Дистанция — горизонтальное расстояние от центра танка до камеры. Высота от–до — высота камеры над рельефом в точке расположения камеры.\n'
            'Минимальный запас камеры — защитный порог высоты камеры над рельефом в точке камеры; это не диапазон случайной высоты.\n\n'
            'Seed определяет расположение и ракурсы. Радиус 0 сохраняет указанную точку; при ненулевом радиусе выбираются точки; число ракурсов на каждую задаётся полем «Кадров на позицию». Вода, препятствия и маски пока не проверяются.')

    def choose_run(self):
        return filedialog.askdirectory(title='Выберите папку серии ke-generate-XX',
                                       initialdir=str(self.last_run or self.values['output'].get().strip()))

    def check_run(self):
        run = self.choose_run()
        if run: self.start_tool('ke_validate.py', ['--run', run])

    def check_terrain(self):
        run = self.choose_run()
        if run: self.start_tool('ke_terrain_audit.py', ['--run', run])

    def recover_run(self):
        run = self.choose_run()
        if run: self.start_tool('ke_recover.py', ['--run', run])

    def fixed_clip_map(self):
        run = self.choose_run()
        if not run: return
        from kolobanoveye import LEVEL
        run = Path(run).resolve()
        map_id = LEVEL.as_posix()
        saved = run / 'generation-config.json'
        if saved.is_file():
            try:
                map_id = json.loads(saved.read_text(encoding='utf-8')).get('map_id', map_id)
            except (ValueError, OSError):
                pass
        tank_map = run / 'maps' / f'{Path(map_id).stem}-tank.png'
        tank_bounds = run / 'tank-map-bounds.json'
        if tank_map.is_file() and tank_bounds.is_file():
            try:
                info = json.loads(tank_bounds.read_text(encoding='utf-8'))
                square = info.get('tank_square')
                if not square:
                    raise ValueError('В отчёте нет танкового квадрата')
                b = [square[k] for k in ('min_x', 'max_x', 'min_z', 'max_z')]
                output = next_numbered_directory(run, 'tank-map-')
                output.mkdir()
            except (OSError, ValueError, KeyError) as exc:
                messagebox.showerror('Танковая карта', str(exc)); return
            self.start_tool('ke_map.py', [
                '--run', str(run), '--image', str(tank_map),
                '--out', str(output / 'positions.html'), '--bounds',
                *(str(x) for x in b), '--flip-z', '--link-image'])
            return
        image = run / 'maps' / f'{Path(map_id).stem}.png'
        if not image.is_file():
            image = run / 'render-debug' / 'terrain-last-clip.png'
        if not image.is_file():
            image = run.parent / 'maps' / f'{Path(map_id).stem}.png'
        if not image.is_file():
            messagebox.showerror('Нет текстуры', 'Для старой карты last_clip включите диагностику всего ландшафта и создайте новую серию.')
            return
        try:
            from ke_map import read_landscape_bounds
            read_landscape_bounds(Path(run), fixed_clip=True)
            out = next_numbered_directory(Path(run), 'fixed-clip-map-')
            out.mkdir()
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror('Нет границ текстуры', str(exc)); return
        self.start_tool('ke_map.py', ['--run', str(run), '--image', str(image), '--out', str(out / 'positions.html'), '--fixed-clip', '--link-image'])

    def preview_run(self):
        run = self.choose_run()
        if not run: return
        try: out = next_numbered_directory(Path(run).parent, 'ke-review-')
        except ValueError as exc: messagebox.showerror('Ошибка', str(exc)); return
        self.start_tool('ke_review.py', ['--run', run, '--out', str(out)])

    def coco_run(self):
        run = self.choose_run()
        if not run: return
        try: out = next_numbered_directory(Path(run), 'coco-')
        except ValueError as exc: messagebox.showerror('Ошибка', str(exc)); return
        self.start_tool('ke_coco.py', ['--runs', run, '--out', str(out)])

    def start_tool(self, script, arguments):
        if self.running: return
        self.running = True; self.run_button.config(state='disabled'); self.state.set('Проверка выполняется…')
        cmd = [sys.executable, '-u', str(Path(__file__).resolve().with_name(script)), *arguments]
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
            run = Path(data['output'])
            def execute(stage, args):
                self.events.put(('line', f'\n[KE] {stage}\n'))
                proc = subprocess.Popen(args, cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
                self.process = proc
                for line in proc.stdout: self.events.put(('line', line))
                code = proc.wait()
                if code: raise RuntimeError(f'{stage}: код {code}. Результаты: {run}')
            if len(data.get('map_ids', [])) > 1:
                execute('Многокартовая генерация (с проверкой и COCO)', cmd)
                self.events.put(('success', str(run)))
                return
            execute('1/4 Генерация', cmd)
            with (run / 'generation-config.json').open('x', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n')
            script = Path(__file__).resolve().parent
            execute('2/4 Проверка серии', [sys.executable, '-u', str(script / 'ke_validate.py'), '--run', str(run)])
            execute('3/4 Диагностика рельефа', [sys.executable, '-u', str(script / 'ke_terrain_audit.py'), '--run', str(run), '--write', '--max-height-range', str(data['max_height_range'])])
            execute('4/4 Экспорт COCO', [sys.executable, '-u', str(script / 'ke_coco.py'), '--runs', str(run), '--out', str(run / 'coco')])
            self.events.put(('success', str(run)))
        except (OSError, ValueError, RuntimeError) as exc:
            self.events.put(('error', str(exc)))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'line':
                    self.log.config(state='normal'); self.log.insert('end', value); self.log.see('end'); self.log.config(state='disabled')
                else:
                    self.running = False; self.run_button.config(state='normal')
                    if kind == 'success':
                        self.last_run = value
                        self.open_button.config(state='normal')
                        self.state.set(f'Готово: {value}')
                        messagebox.showinfo('Готово', f'Кадры и метаданные: {value}\nCOCO: {value}\\coco\\all.json\n\nРамка приблизительная; видимость не проверена.')
                    else:
                        self.state.set('Завершено' if kind == 'done' and value == 0 else f'Ошибка: {value}. Проверьте журнал.')
        except queue.Empty: pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk(); App(root); root.mainloop()

if __name__ == '__main__': main()
