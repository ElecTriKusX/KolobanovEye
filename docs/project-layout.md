# Структура KolobanovEye 2.0

Актуальная схема ветки `feature/avg-map-catalog-whitelist`. Главная цель — отделить точки входа, вторичные Python-утилиты, C++-патчи, тесты и результаты генерации, не затрагивая будущий Dataset Splitter.

```text
KolobanovEye/
├── kolobanoveye.py              # Dagor: build / inspect / frame
├── ke_gui.py                     # графический интерфейс
├── ke_generate.py                # основная генерация из JSON
├── ke_multi_generate.py          # несколько карт и квоты
├── ke_dataset.py                 # проверка и атомарная сборка raw
├── config.example.json
│
├── tools/                        # ВТОРИЧНЫЕ утилиты и Python-модули
│   ├── __init__.py
│   ├── ke_maps.py                # обнаружение карт, границы
│   ├── ke_coco.py                # COCO
│   ├── ke_validate.py            # проверка серии
│   ├── ke_terrain_audit.py       # отчёт о рельефе
│   ├── ke_review.py              # диагностические рамки
│   ├── ke_recover.py             # восстановление после сбоя
│   ├── ke_diagnose.py            # диагностика Dagor
│   ├── ke_map.py                 # отображение позиций на карте
│   ├── ke_annotation.py, ke_frame_journal.py,
│   ├── ke_pixel_debug.py, ke_png_fast.py,
│   ├── ke_pilot.py, ke_urban.py
│   └── vendor_ooz.py             # фиксация локальной копии OOZ
│
├── engine/
│   ├── README.md
│   └── patches/                  # 46 C++-патчей Dagor
├── vendor/
│   ├── README.md                 # инструкция по локальному OOZ
│   ├── ooz.lock.json             # SHA-256 пяти закреплённых файлов
│   └── ooz/                      # ТОЛЬКО локально, игнорируется Git
│       ├── kraken.cpp
│       ├── lzna.cpp
│       ├── bitknit.cpp
│       ├── stdafx.h
│       └── targetver.h
│
├── tests/                        # автоматические тесты
├── docs/                         # проектная документация
├── dataset/
│   ├── README.md                 # отслеживается Git
│   ├── raw/                      # один актуальный несегментированный датасет
│   │   ├── images/
│   │   ├── annotations/instances.json
│   │   ├── metadata/
│   │   └── manifest.json
│   └── runs/                     # логи, диагностика, промежуточные кадры
│
├── build-logs/                   # локальная сборка и артефакты OOZ, не в Git
├── .github/workflows/
├── .gitignore
├── README.md
└── PLAN.md
```

## Принцип организации

Пять основных исполняемых скриптов оставлены в корне. Все второстепенные инструменты и вспомогательные модули находятся в `tools/`. Они запускаются через `python -m tools.<имя_модуля>`, например:

```powershell
python -m tools.ke_maps --game D:\Games\WarThunder --show-whitelist
python -m tools.ke_validate --run .\dataset\runs\run-XXXX\runs\avg_normandy
python -m tools.vendor_ooz
```

Главные команды неизменны:

```powershell
python -m unittest discover -v
python .\kolobanoveye.py build --dagor D:\Projects\DagorEngine
python .\ke_gui.py
```

Для **первой** установки на новую машину исходники OOZ можно закрепить из прежней проверенной `build-logs/ooz-local` командой `python -m tools.vendor_ooz`. Дальше `build` автоматически использует `vendor/ooz` без `--ooz` и без доступа к исходному репозиторию. Все пять файлов должны соответствовать закреплённым SHA-256 из `vendor/ooz.lock.json`.

Исходники OOZ не размещены в открытом GitHub, поскольку их лицензия на распространение не подтверждена. Если планируется новый clone без локального `vendor/ooz`, нужно сохранить частную копию пяти исходников либо отдельно решить вопрос лицензирования.

`dataset/raw/` формируется заново **за один запуск** после валидации, без train/val/test. Прежний `raw` заменяется только после успешного завершения и подтверждения в GUI. Технические `dataset/runs` остаются для диагностики. Всё это исключено из Git; внутри `dataset/` отслеживается только README.

## Особенность Dagor

После предыдущей ошибки компиляции заменили `auto target = dag::create_tex(...)` на `UniqueTex target = dag::create_tex(...)` в ROI-рендере. Скрипт сборки умеет обновить уже применённый старый патч в `DagorEngine` без сброса и повторного применения всех патчей. Следующий Windows-компилятор может выявить другие проблемы; требуется реальный прогон на твоём компьютере.

Критерии генерации — в `docs/map-catalog-roadmap.md`, фактические результаты — в `docs/project-status.md`.
