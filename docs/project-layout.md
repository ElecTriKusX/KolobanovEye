# Структура KolobanovEye 2.0

Актуальная архитектура ветки `feature/occluder-guided-sampling` (26 ноября 2026). **Один DaEditor на весь запуск**, адаптивная online-обработка выбранных карт через mailbox и `probe.dcmd`. Ниже фактические пути нового генератора; старые папки `runs/<map>` и промежуточный `coco/all.json` больше не создаются.

```text
KolobanovEye/
├── kolobanoveye.py           # Dagor: сборка, проверка ресурсов, низкоуровневый кадр
├── ke_gui.py                  # основной Tkinter GUI
├── ke_generate.py             # CLI с JSON-конфигом
├── ke_single_editor.py        # один DaEditor, очередь карт, сборка COCO из journal
├── ke_dataset.py              # безопасная замена dataset/raw
├── config.example.json
├── tools/
│   ├── __init__.py
│   ├── ke_pipeline.py         # обязательные настройки единого конвейера
│   ├── ke_online_capture.py   # планировщик online-заданий и добор
│   ├── ke_maps.py             # поиск карт и whitelist
│   ├── ke_annotation.py       # проверка bbox из BLK
│   ├── ke_frame_journal.py    # чтение journal
│   ├── ke_coco.py             # поля COCO и вспомогательные функции
│   ├── ke_validate.py         # внутренняя валидация
│   ├── ke_review.py           # работа с PNG для тестов и анализа
│   ├── ke_analysis.py         # HTML-карты с точками по кнопке
│   ├── ke_diagnose.py         # техническая диагностика
│   └── vendor_ooz.py
├── engine/patches/            # Dagor C++ патчи, применяются сборщиком
├── vendor/
│   ├── ooz.lock.json          # закреплённые SHA-256 OOZ
│   └── ooz/                   # локальные исходники OOZ
├── tests/
├── docs/
├── dataset/
│   ├── README.md
│   ├── raw/                   # текущий завершённый несегментированный датасет
│   │   ├── images/
│   │   ├── masks/amodal/
│   │   ├── masks/visible/
│   │   ├── annotations/instances.json
│   │   ├── metadata/
│   │   │   ├── images.jsonl
│   │   │   ├── scenes.jsonl
│   │   │   ├── maps.json
│   │   │   └── generation_config.json
│   │   └── manifest.json
│   ├── runs/
│   │   └── run-YYYYMMDDTHHMMSSZ-XXXXXXXX/
│   │       ├── capture/       # разные задания BLK по картам
│   │       ├── plans/         # планы камер/позиций по картам
│   │       ├── maps/          # оригинальные обзорные PNG танкового поля
│   │       ├── environment/   # общая конфигурация окружения
│   │       ├── journals/      # временные журналы при ошибке
│   │       ├── probe.level.blk
│   │       ├── probe.level.local.blk
│   │       ├── probe.dcmd     # одна native-команда kolobanoveye.batch
│   │       ├── .logs/                 # настоящий debug-журнал пакетной консоли
│   │       ├── host-stdout.log
│   │       ├── manifest.json
│   │       └── generation-config.json
│   └── analysis/              # только после «Провести анализ»
│       ├── index.html
│       ├── maps/*-positions.html
│       └── report.json
├── build-logs/                # локальные файлы сборки
├── .gitignore
├── README.md
└── PLAN.md
```

## Принципы

1. `ke_single_editor.py` формирует квоты по выбранным картам, несколько файлов конфигурации BLK и **одну** очередь `probe.dcmd`. Одна команда запускает `daEditor3x-dev.exe` с этой очередью. В C++ каждый `kolobanoveye.frame` загружает свою карту через `BinSceneViewPlugin::loadKolobanovEyeLevel`. `AcesScene::loadLevel` очищает предыдущую сцену и пересоздаёт её ресурсы.
2. Изображения записываются **сразу во временный каталог raw/images**; после завершения и проверки вся папка `dataset/raw` обновляется. В `runs/run-...` нет `images/`, вложенной `runs/` и дублированного `coco/`.
3. `raw/annotations/instances.json` — единственный основной COCO текущего датасета. Планы и файлы Dagor в `runs/` технические, не для публикации.
4. `dataset/analysis` появляется **только после нажатия кнопки** и ссылается на обзорные карты `runs/<run-id>/maps` без копирования PNG. В отчёте и HTML показываются уникальные расстановки танков; правильность ориентации карты нужно проверить на Windows.
5. В GUI карта не выбирается отдельно как «Текущий запуск»: используются все отмеченные в белом списке доступные карты. Единственный режим размещения — умная расстановка на танковом поле. Анализ объектов подготавливает обязательный кэш; ручные режимы удалены из генерации.
6. Пустой `raw` или провал одной карты не публикуется. При ошибке прежний `raw` сохраняется, а техническая папка запуска остаётся для расследования. В GUI перед заменой прежнего `raw` запрашивается подтверждение.
7. Папки `dataset/raw`, `dataset/runs`, `dataset/analysis` не попадают в Git (`.gitignore`); `dataset/README.md` отслеживается.

## Локальная проверка

```powershell
cd D:\Projects\KolobanovEye
python -m unittest discover -v
python .\kolobanoveye.py build --dagor D:\Projects\DagorEngine
python .\ke_gui.py
```

Без GUI: `python .\ke_generate.py --config .\config.example.json`; если ранее был создан `raw`, добавить `--replace-raw`.

Обзор результатов по запросу: `python -m tools.ke_analysis --dataset .\dataset`.

**Статус подтверждения:** до очистки успешен Windows-прогон 600 кадров на Abandoned Factory. После очистки необходима повторная сборка и многокартовый тест. Не сливать в main до приёмки.
