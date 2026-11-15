"""Minimal Tk interface for the existing single-process sequence renderer."""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from ke_generate import DEFAULTS, INTS, FLOATS, validate_config, command

FIELDS = [
 ('dagor', 'Папка Dagor Engine'), ('game', 'Папка War Thunder'), ('output', 'Родительская папка результатов'),
 ('count', 'Количество кадров (2–1000)'), ('seed', 'Seed'),
 ('width', 'Ширина'), ('height', 'Высота'), ('fov', 'Горизонтальный FOV, °'),
 ('tank_x', 'Позиция танка X'), ('tank_z', 'Позиция танка Z'), ('tank_yaw', 'Поворот танка, °'),
 ('placement_radius', 'Радиус поиска точки'), ('placement_attempts', 'Попыток размещения'),
 ('max_height_range', 'Макс. перепад высот'), ('ground_offset', 'Отступ танка от земли'), ('camera_clearance', 'Высота камеры над землёй'),
 ('distance_min', 'Расстояние камеры: от'), ('distance_max', 'Расстояние камеры: до'),
 ('elevation_min', 'Возвышение камеры, °: от'), ('elevation_max', 'Возвышение камеры, °: до'),
 ('clipmap_feedback', 'Обратная связь clipmap'), ('timeout', 'Таймаут, с'),
]


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
        root.geometry('900x780')
        self.events = queue.Queue()
        self.process = None
        self.running = False
        self.values = {k: tk.StringVar(value=str(DEFAULTS[k])) for k, _ in FIELDS}
        self.values['output'].set(str(Path(DEFAULTS['output']).parent))
        self.last_run = None
        main = ttk.Frame(root, padding=12)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='Карта: avg_normandy  |  Модель: Tiger H1  |  Класс: tank').pack(anchor='w')
        ttk.Label(main, text='Выберите папку результатов: ke-generate-01, 02… создаются автоматически.').pack(anchor='w', pady=(2, 8))
        form = ttk.Frame(main)
        form.pack(fill='x')
        for idx, (key, label) in enumerate(FIELDS):
            row, col = divmod(idx, 2)
            parent = ttk.Frame(form)
            parent.grid(row=row, column=col, sticky='ew', padx=(0, 12), pady=2)
            ttk.Label(parent, text=label, width=29).pack(side='left')
            if key == 'clipmap_feedback':
                widget = ttk.Combobox(parent, textvariable=self.values[key], values=('software','hardware'), state='readonly', width=12)
            else:
                widget = ttk.Entry(parent, textvariable=self.values[key])
            widget.pack(side='left', fill='x', expand=True)
            if key in ('dagor', 'game', 'output'):
                ttk.Button(parent, text='…', width=3, command=lambda k=key: self.choose_directory(k)).pack(side='left')
        form.columnconfigure(0, weight=1); form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(main); buttons.pack(fill='x', pady=8)
        self.run_button = ttk.Button(buttons, text='Создать и проверить датасет', command=self.run)
        self.run_button.pack(side='left')
        menu_button = ttk.Menubutton(buttons, text='Дополнительно')
        menu = tk.Menu(menu_button, tearoff=False)
        menu.add_command(label='Проверить существующую серию…', command=self.check_run)
        menu.add_command(label='Проверить рельеф серии…', command=self.check_terrain)
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

    def get_config(self):
        data = {}
        for key, var in self.values.items():
            value = var.get().strip()
            try:
                data[key] = int(value) if key in INTS else float(value.replace(',', '.')) if key in FLOATS else value
            except ValueError as exc:
                raise ValueError(f'{key}: неверное число') from exc
        if not data['output']:
            raise ValueError('Укажите родительскую папку результатов')
        data['output'] = str(next_numbered_directory(data['output']))
        return validate_config(data)

    def open_config(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not path: return
        try:
            data = validate_config(json.loads(Path(path).read_text(encoding='utf-8')))
            for k, v in data.items(): self.values[k].set(str(Path(v).parent) if k == 'output' else str(v))
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
        try:
            data = self.get_config()
            cmd = command(data)
        except ValueError as exc:
            messagebox.showerror('Ошибка настроек', str(exc)); return
        self.running = True; self.run_button.config(state='disabled'); self.state.set(f"Генерация: {data['output']}")
        threading.Thread(target=self.worker, args=(data, cmd), daemon=True).start()

    def show_help(self):
        messagebox.showinfo('Справка',
            'Открыть папку — показывает последний успешный результат в Проводнике. Копировать журнал — копирует выделенный текст или весь журнал.\n\n'
            'Основная кнопка: создаёт новую папку ke-generate-XX, рендерит кадры, проверяет их и создаёт COCO в подпапке coco.\n\n'
            'Проверить серию — проверяет файлы и границы рамок существующей серии.\n'
            'Проверить рельеф — оценивает перепад heightmap под углами габарита; воду и препятствия не проверяет.\n'
            'Рамки — создаёт отдельные копии кадров с зелёной рамкой.\n'
            'COCO — создаёт JSON-разметку для существующей серии без копирования изображений.\n\n'
            'COCO — формат данных для обучения детектора; здесь рамка приблизительная, видимость не проверена. '
            'Папку серии нельзя отделять от файлов coco: в JSON сохранены относительные пути к кадрам.\n\n'
            'Seed определяет расположение и ракурсы. Радиус 0 сохраняет указанную точку; при ненулевом радиусе выбирается одна точка на серию. Вода, препятствия и маски пока не проверяются.')

    def choose_run(self):
        return filedialog.askdirectory(title='Выберите папку серии ke-generate-XX',
                                       initialdir=str(self.last_run or self.values['output'].get().strip()))

    def check_run(self):
        run = self.choose_run()
        if run: self.start_tool('ke_validate.py', ['--run', run])

    def check_terrain(self):
        run = self.choose_run()
        if run: self.start_tool('ke_terrain_audit.py', ['--run', run])

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
