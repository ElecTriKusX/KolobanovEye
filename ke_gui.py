"""Minimal Tk interface for the existing single-process sequence renderer."""
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from ke_generate import DEFAULTS, INTS, FLOATS, validate_config, command

FIELDS = [
 ('dagor', 'Папка Dagor Engine'), ('game', 'Папка War Thunder'), ('output', 'Новая папка результата'),
 ('count', 'Количество кадров (2–1000)'), ('seed', 'Seed'),
 ('width', 'Ширина'), ('height', 'Высота'), ('fov', 'Горизонтальный FOV, °'),
 ('tank_x', 'Позиция танка X'), ('tank_z', 'Позиция танка Z'), ('tank_yaw', 'Поворот танка, °'),
 ('ground_offset', 'Отступ танка от земли'), ('camera_clearance', 'Высота камеры над землёй'),
 ('distance_min', 'Расстояние камеры: от'), ('distance_max', 'Расстояние камеры: до'),
 ('elevation_min', 'Возвышение камеры, °: от'), ('elevation_max', 'Возвышение камеры, °: до'),
 ('clipmap_feedback', 'Обратная связь clipmap'), ('timeout', 'Таймаут, с'),
]


class App:
    def __init__(self, root):
        self.root = root
        root.title('KolobanovEye — тестовый генератор')
        root.geometry('900x780')
        self.events = queue.Queue()
        self.process = None
        self.running = False
        self.values = {k: tk.StringVar(value=str(DEFAULTS[k])) for k, _ in FIELDS}
        self.vars = {}
        main = ttk.Frame(root, padding=12)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='Карта: avg_normandy  |  Модель: Tiger H1  |  Класс: tank').pack(anchor='w')
        ttk.Label(main, text='Выходная папка должна быть новой. Рендер выполняется локально.').pack(anchor='w', pady=(2, 8))
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
        self.run_button = ttk.Button(buttons, text='Запустить генерацию', command=self.run)
        self.run_button.pack(side='left')
        ttk.Button(buttons, text='Открыть настройки', command=self.open_config).pack(side='left', padx=5)
        ttk.Button(buttons, text='Сохранить настройки', command=self.save_config).pack(side='left')
        ttk.Button(buttons, text='Проверить серию', command=self.check_run).pack(side='left', padx=5)
        ttk.Button(buttons, text='Рамки', command=self.preview_run).pack(side='left')
        self.state = tk.StringVar(value='Готово')
        ttk.Label(main, textvariable=self.state).pack(anchor='w')
        self.log = tk.Text(main, height=12, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True, pady=(4, 0))
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        root.after(100, self.poll)

    def on_close(self):
        if self.running:
            messagebox.showinfo('Генерация выполняется', 'Дождитесь завершения генерации, прежде чем закрыть окно.')
        else:
            self.root.destroy()

    def choose_directory(self, key):
        path = filedialog.askdirectory(title='Выберите папку' + ('; для результата выберите родительскую' if key == 'output' else ''))
        if path:
            self.values[key].set(str(Path(path) / 'ke-run-new') if key == 'output' else path)

    def get_config(self):
        data = {}
        for key, var in self.values.items():
            value = var.get().strip()
            try:
                data[key] = int(value) if key in INTS else float(value.replace(',', '.')) if key in FLOATS else value
            except ValueError as exc:
                raise ValueError(f'{key}: неверное число') from exc
        return validate_config(data)

    def open_config(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not path: return
        try:
            data = validate_config(json.loads(Path(path).read_text(encoding='utf-8')))
            for k, v in data.items(): self.values[k].set(str(v))
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
            if Path(data['output']).exists(): raise ValueError('Выходная папка уже существует. Укажите новую папку.')
            cmd = command(data)
        except ValueError as exc:
            messagebox.showerror('Ошибка настроек', str(exc)); return
        self.running = True; self.run_button.config(state='disabled'); self.state.set('Генерация выполняется…')
        threading.Thread(target=self.worker, args=(data, cmd), daemon=True).start()

    def check_run(self):
        self.start_tool('ke_validate.py', ['--run', self.values['output'].get().strip()])

    def preview_run(self):
        run = self.values['output'].get().strip()
        if not run:
            messagebox.showerror('Ошибка', 'Укажите папку серии.'); return
        out = filedialog.asksaveasfilename(title='Укажите новую папку для копий с рамками')
        if out:
            self.start_tool('ke_review.py', ['--run', run, '--out', out])

    def start_tool(self, script, arguments):
        if self.running: return
        self.running = True; self.run_button.config(state='disabled'); self.state.set('Проверка выполняется…')
        cmd = [sys.executable, '-u', str(Path(__file__).resolve().with_name(script)), *arguments]
        threading.Thread(target=self.tool_worker, args=(cmd,), daemon=True).start()

    def tool_worker(self, cmd):
        try:
            proc = subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            for line in proc.stdout: self.events.put(('line', line))
            self.events.put(('done', proc.wait()))
        except OSError as exc: self.events.put(('error', str(exc)))

    def worker(self, data, cmd):
        try:
            proc = subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            self.process = proc
            for line in proc.stdout: self.events.put(('line', line))
            code = proc.wait()
            output = Path(data['output'])
            if output.is_dir():
                with (output / 'generation-config.json').open('x', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n')
            self.events.put(('done', code))
        except (OSError, ValueError) as exc: self.events.put(('error', str(exc)))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'line':
                    self.log.config(state='normal'); self.log.insert('end', value); self.log.see('end'); self.log.config(state='disabled')
                else:
                    self.running = False; self.run_button.config(state='normal')
                    self.state.set('Завершено' if kind == 'done' and value == 0 else f'Ошибка: {value}. Проверьте журнал.')
        except queue.Empty: pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk(); App(root); root.mainloop()

if __name__ == '__main__': main()
