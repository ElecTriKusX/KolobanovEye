# Структура KolobanovEye 2.0

Зафиксирована в ветке `feature/avg-map-catalog-whitelist`. Структура предназначена для развития генератора, а не для упаковки публичного релиза или train/val/test.

```text
KolobanovEye/
├── kolobanoveye.py              # Dagor: build, inspect, frame
├── ke_gui.py                     # основной графический интерфейс
├── ke_generate.py                # запуск генерации из JSON-конфига
├── ke_dataset.py                 # сборка dataset/raw из проверенных запусков
├── ke_multi_generate.py          # квоты и запуск Dagor по картам
├── ke_*.py                       # существующие служебные утилиты и модули
├── config.example.json           # пример настроек
├── engine/
│   ├── README.md
│   └── patches/                  # Dagor C++ патчи, привязанные к ревизии
├── tests/
│   ├── test_*.py                 # Python unit/integration tests
│   ├── ooz_linux.py              # тесты декодера (специальный запуск)
│   ├── ooz_backend.cpp
│   └── ooz_linux_compat.h
├── docs/
│   ├── project-layout.md
│   ├── project-status.md
│   └── map-catalog-roadmap.md
├── dataset/
│   ├── README.md                 # единственный отслеживаемый Git файл здесь
│   ├── raw/                      # актуальный единый исходный датасет
│   │   ├── images/
│   │   ├── annotations/instances.json
│   │   ├── metadata/images.jsonl
│   │   ├── metadata/scenes.jsonl
│   │   ├── metadata/maps.json
│   │   ├── metadata/generation_config.json
│   │   └── manifest.json
│   └── runs/                     # отдельные технические запуски и журналы Dagor
├── .github/workflows/            # CI: Python + проверки применения патчей
├── .gitignore
├── README.md
└── PLAN.md
```

## Назначение каталогов

- `engine/patches`: **только исходные изменения Dagor**. Порядок применения контролируется `kolobanoveye.py build`. Не применяйте патчи вручную по одному к уже модифицированному Dagor.
- `tests/`: все автоматические тесты. Команда из корня **не изменилась**: `python -m unittest discover -v`.
- `docs/`: требования, статус задач и архитектурные решения.
- `dataset/raw/`: **один новый полный результат генерации**; никаких подкаталогов по картам и никаких train/val/test. Карта и остальные характеристики хранятся в COCO/метаданных.
- `dataset/runs/`: техническая история запусков с журналами, снимками и промежуточными данными. Не является содержимым публичного датасета.
- `dataset/README.md`: точная схема результата и правила безопасного обновления `raw`.

Скрипты `ke_*.py` пока остаются в корне намеренно: GUI и существующие команды/документация используют их как исполняемые точки входа. Отдельное механическое перемещение всех Python-модулей сейчас могло бы сломать интеграцию с Dagor, поэтому следующим структурным шагом может быть перенос внутренней логики в пакет без изменения пользовательских команд.

## Использование

```powershell
cd D:\Projects\KolobanovEye
python -m unittest discover -v
python .\kolobanoveye.py build --dagor D:\Projects\DagorEngine --ooz D:\Projects\ooz
python .\ke_gui.py
```

CLI без GUI:

```powershell
python .\ke_generate.py --config .\config.example.json
# Если dataset/raw уже существует и его нужно заменить:
python .\ke_generate.py --config .\config.example.json --replace-raw
```

В GUI при существующем `dataset/raw` показывается запрос подтверждения; до полного успеха новая генерация не меняет прежний `raw`.

> **Важно:** генерация занимает много места. `dataset/raw` при успешном следующем запуске заменяется, но предыдущие технические результаты в `dataset/runs` не удаляются автоматически. Временные каталоги, ошибки и весь `dataset` защищены `.gitignore`.
