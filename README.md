# KolobanovEye 2.0 · этап 0

Прототип проверяет, сможет ли открытый Dagor загрузить бинарную карту **Normandy** и модель **Tiger H1** из вашей установки War Thunder и сохранить полноразмерный RGB-кадр. Код работы со сценой на C++, команды сборки и запуска на Python. Открытый DaEditorX создаёт контекст DX11, загружает ресурсы через локальную конфигурацию CDK и принимает команду захвата из batch-файла. Во время проверки может быть видно окно. Рендер выполняется в отдельную текстуру. Внутри проекта-запуска нет геометрии, вся геометрия для кадра поступает из установленных ресурсов игры.

**Статус.** На Linux проверены синтаксис изменённых C++ файлов, применимость патча и диагностика Python. Сборка Windows x64 и чтение каталога моделей из GRP подтверждены. На проверенной установке War Thunder загрузка Normandy останавливается на распаковке lightmap через заглушку Oodle. RGB-кадр пока не получен. `FRAME_WRITTEN` подтверждает прохождение программных проверок. Для приёмки требуется визуально подтвердить карту, окружение, модель и материалы.

## Сборка под Windows x64

Понадобятся Git, Python 3.10+, 7-Zip и официальный toolchain Dagor. В `make_devtools.py` описаны требуемые Visual Studio и Windows SDK. Для запуска нужен рабочий DX11-драйвер и активная рабочая сессия Windows. Используйте короткие пути латиницей без пробелов для checkout, toolchain, установки игры и папки результатов. Распакуйте этот архив, например в `D:\dev\KolobanovEye_2.0_phase0`.

В PowerShell из папки `D:\dev`:

```powershell
git clone https://github.com/ElecTriKusX/DagorEngine.git
git -C .\DagorEngine checkout --detach 9639942de5a8e5b01d91977f60ed82cda22d5f41
Set-Location .\DagorEngine
py -3 .\make_devtools.py D:\dev\devtools
```

Скачайте [tools-base.7z](https://dagorenginedata.cdn.gaijin.net/head-2026.11.07/tools-base.7z) из README зафиксированного коммита и распакуйте его в корень **открытого** `D:\dev\DagorEngine`. Каталог `tools\dagor_cdk\commonData` должен содержать `guiShaders.ps40.shdump.bin`. Откройте новый PowerShell после установки toolchain. Если путь к Jam не задан глобально, добавьте его в текущую сессию.

```powershell
$env:Path = "D:\dev\devtools;$env:Path"
Set-Location D:\dev\KolobanovEye_2.0_phase0
py -3 .\kolobanoveye.py inspect --game D:\Games\WarThunder > .\inventory.json
py -3 .\kolobanoveye.py build --dagor D:\dev\DagorEngine
```

`build` проверяет коммит, проверяет и применяет патчи по порядку и запускает два Jam-таргета x64/dev. Его можно запускать повторно на том же checkout. Он сохраняет журналы в `build-logs\editor.log` и `build-logs\dumpGrp.log`. Ожидаемые бинарники — `daEditor3x-dev.exe` и `dumpGrp-dev.exe` в `DagorEngine\tools\dagor_cdk\windows-x86_64`.

`inspect` можно запустить **до установки toolchain**. Он читает метаданные и заголовки установленных файлов, включая структуру блоков карты и версию шейдеров CDK. Код возврата 1 означает ошибку в `inventory.json`. Этот шаг не открывает архивные payload, не строит сцену и не подтверждает совместимый рендер.

Документация и исходники сборки. [How to Build](https://gaijinentertainment.github.io/DagorEngine/getting-started/how_to_build.html) · [README зафиксированного коммита](https://github.com/ElecTriKusX/DagorEngine/blob/9639942de5a8e5b01d91977f60ed82cda22d5f41/README.md) · [Windows build script](https://github.com/ElecTriKusX/DagorEngine/blob/9639942de5a8e5b01d91977f60ed82cda22d5f41/prog/tools/build_dagor_cdk_mini.cmd)

При обновлении с первого архива распакуйте этот архив с заменой файлов и снова выполните `build`. Скрипт пропускает ранее применённый патч и применяет исправление для `-Wunused-parameter`.

## Один кадр

```powershell
py -3 .\kolobanoveye.py frame --dagor D:\dev\DagorEngine --game D:\Games\WarThunder --out D:\dev\ke-run-01
```

Для каждой попытки задайте новый `--out`. Если CDK расположен отдельно, укажите `--cdk` с путём к каталогу, где находится `application.blk`. В штатной конфигурации CDK относительные пути к игре рассчитаны на вложение в `WarThunder\WarThunderCDK`, поэтому внешний каталог должен использовать конфигурацию с корректными локальными путями.

`dumpGrp -dumpR` сохраняет имена и классы ресурсов в `grp.txt`. Если в пакете несколько подходящих вариантов, укажите найденные имена через `--model` и `--skeleton`. Второй параметр также принимает имя `GeomNodeTree` из другого зарегистрированного пакета. Для многоузловой модели требуется корректное дерево и его соответствие именам render nodes.

По умолчанию проверяется точка `X=0, Z=0` на Normandy. Камера смотрит с заданного смещения на центр габаритов. Это пробная композиция. Для смены координат используйте `--tank-xz X Z`, `--eye X Y Z` в мировых координатах, `--yaw` и `--fov` в градусах. `--width` и `--height` задают разрешение, по умолчанию 1280×720. Программа создаёт `capture.blk` с фактическими параметрами. Файлы игры и оригинальные бинарники CDK берутся по локальным путям. Сборка, служебный workspace и выходные данные лежат вне установки игры. Скрипты `setup.cmd`, `dagored.cmd` и `missioned.cmd` из CDK для этого запуска не нужны.

| Наблюдение | Что проверять |
| --- | --- |
| `inspect` возвращает 1 | Секция `errors` в JSON. Для шейдеров сравните фактические magic и версию с ожидаемыми `VSPSdump/11.3`. |
| `dumpGrp` завершился до запуска DaEditorX | `grp.txt` и код возврата. Ищите `DynModel` класса `B4B7D9C4` и `GeomNodeTree` класса `56F81B6D`. |
| DaEditorX завершился до `MAP_BEGIN` | `probe.dcmd.err`, подкаталог `.logs/daEditor3x-dev.exe-*` с журналом `debug` и `host-stdout.log`. При старте проверьте workspace, DX11, GUI dump, шейдеры CDK и VROMFS. |
| Есть `MAP_BEGIN`, затем ошибка | Найдите последний `[BIN] tag` в журнале `debug`. `TERRAIN` при успешном разборе означает, что в заданном XZ не получена высота. |
| `MODEL_INDEX` / `MODEL_LOAD` | Сравните `grp.txt` с `MODEL_PACK`. Проверьте `resPacks.blk`, пути пакетов, их дескрипторы и зависимости. |
| `SKELETON` / `MODEL_TEXTURES` | Сверьте ресурс дерева, показанные имена нод и загрузку DDSx. |
| `STREAMING` / `RENDER` / `READBACK` | Проверьте счётчики текстур и окружения, выбранный рендерер и DX11. По завершении таймаута процесса журналы остаются в папке запуска. |
| `FRAME_WRITTEN` | Откройте `frame.png`. Проверьте Normandy, небо, рельеф и собранный Tiger H1 с материалами. Сопоставьте с локальным кадром War Thunder при близком ракурсе и освещении. |

Для разбора ошибки достаточно вывести `inventory.json`, `grp.txt` и сообщения вокруг первого сбоя из журналов. Сами игровые пакеты и кадр можно оставить локально.

При успешном захвате `frame.blk` содержит мировое положение танка и камеры, FOV, глубину отсечения, LOD и `bbox_amodal_aabb_xywh`. Это приближение полной проекции 3D-габарита, обрезанное по границам кадра. Видимость проверяется по изображению. Попадание статической геометрии в луч к центру логируется отдельно и не используется для вычисления bbox. Форматы COCO, маски, случайное размещение и пакетный датасет относятся к следующим этапам.

## Ограничение текущего запуска War Thunder

DaEditorX запускается с DX11 и доходит до `[KE] MAP_BEGIN`. В блоке `TEX` карты Normandy чтение `avg_normandy_lightmap` вызывает `OodleLoadCB::open`. В сборке без Oodle эта функция попадает в `oodleIoStub.cpp`. В журнале также видны такие ошибки на нескольких DDSx-пакетах. Успешный `inspect` подтверждает сигнатуры и версии контейнеров, а для содержимого нужно отдельное декодирование.

Зафиксированный Dagor ожидает авторизованный Oodle SDK **2.7.6** для динамического `daKernel-dev.dll` на Windows x64. В каталоге toolchain `oodle.2.7` Jam проверяет маркер `oodle2_version_2.7.6`, включает `include/oodle2.h` и линкует `static_lib/oo2core_win64.lib`. Если полный `build` также пересобирает статический `dumpGrp`, для него отдельная ветка Jam ожидает SDK **2.9.2** в `oodle.2.9`. Если `dumpGrp` уже собран и читает каталог, можно отдельно пересобрать динамический `daKernel` и DaEditorX штатными Jam-таргетами. Успешную загрузку с SDK ещё предстоит проверить. [Правила сборки Oodle в Dagor](https://github.com/ElecTriKusX/DagorEngine/blob/9639942de5a8e5b01d91977f60ed82cda22d5f41/prog/engine/ioSys/jamfile)

## Почему этот хост

| Задача | Код и границы проверки |
| --- | --- |
| Бинарный уровень | `AcesScene::loadLevel` вызывает `load_binary_dump`. Публичная версия ожидает заголовок `DBLD/3x64`. Общий загрузчик читает текстуры, `SCN`, `ENVI` и внешние включения. `AcesScene::bdlCustomLoad` добавляет `lmap`, `HM2`, `RIGz`, статические коллизии `FRT/SCol` и другие подсистемы. |
| Размещение и коллизии | `getHeightmapAtPoint` запрашивает высоту и нормаль. `tracerayNormalizedStaticScene` проверяет статическую коллизию. Наличие некоторых коллизий не гарантирует полноту препятствий карты, воды или игровой физики. |
| Пакеты | `initPlugins` использует `application.blk`, VROMFS, `resPacks.blk`, `dynModelDesc.bin`, `riDesc.bin` и относящиеся к установке патчи. Версии GRP и DDSx заголовков проверяются отдельно от фактической загрузки. |
| Модель | `get_one_game_resource_ex` разрешает типизированные ресурсы. `DynamicRenderableSceneInstance` выбирает LOD и проходит в штатные стадии deferred-рендера. Поза строится по локальному `GeomNodeTree`. CDK-шейдеры и список доступных материалов определяют фактический внешний вид. |
| Камера и RGB | `ViewportWindow` получает программную камеру. `renderInTex` вызывает `renderScreenshot`. После чтения render target `save_png32` пишет 24-битный RGB. Кадр проверяется на размеры, тип цвета и наличие неоднородных пикселей. Эти проверки не измеряют качество текстур или видимость танка. |
| Окружение | `ISkiesService::setWeather` и `overrideWeather` поддерживают пресеты и параметры окружения. Проект-запуск запрашивает `day/clear`. Какой свет и погода реально получатся, зависит от BLK и VROMFS. Дождь, туман и вариации параметров на этом этапе не подтверждены. |

[binSceneView](https://github.com/ElecTriKusX/DagorEngine/tree/9639942de5a8e5b01d91977f60ed82cda22d5f41/prog/tools/sceneTools/daEditorX/binSceneView) · [Загрузчик уровней](https://github.com/ElecTriKusX/DagorEngine/blob/9639942de5a8e5b01d91977f60ed82cda22d5f41/prog/engine/scene/loadLevel.cpp) · [Ресурсы редактора](https://github.com/ElecTriKusX/DagorEngine/blob/9639942de5a8e5b01d91977f60ed82cda22d5f41/prog/tools/sceneTools/daEditorX/de_engine.cpp) · [Оффскрин-захват](https://github.com/ElecTriKusX/DagorEngine/blob/9639942de5a8e5b01d91977f60ed82cda22d5f41/prog/tools/libTools/EditorCore/ec_genappwnd.cpp)

Сведение материалов по именам шейдеров зависит от версии bindump и состава классов CDK. В открытом checkout также есть заглушка Oodle. Если журнал укажет на неё, нужно установить, какой реальный блок или пакет требует этот кодек, прежде чем выбирать другую сборку или проверять путь через официальный CDK. Вариант диагностики при несовместимости — воспроизвести открытие тех же ресурсов в штатных средствах CDK и выяснить, доступен ли у этой версии документированный способ автоматического захвата. Документация Gaijin подтверждает экспорт пользовательских локаций в `.bin`, а автоматический захват произвольной готовой карты с выбранным танком через CDK ещё требует проверки. [War Thunder CDK](https://wiki.warthunder.com/war_thunder_cdk) · [Экспорт локаций](https://wiki.warthunder.com/cdk/creation_location)

## Проверки пакета

```powershell
py -3 -m unittest -v
```

Тесты Python проверяют разбор заголовка уровня и границ его блоков, диагностику версии шейдеров, необязательный HQ-пакет, выбор типизированных записей из `dumpGrp` и различение RGB/RGBA PNG. Для них создаются только синтетические тестовые байты. Они не используются для рендера.

## Сборка с ooz и проверка Normandy без Oodle SDK

На Linux адаптер и декодер были скомпилированы вместе с тестовым
`IGenLoad`, который возвращал данные порциями. Все шесть файлов
`testdata/dickens.*` из upstream дали 10 192 446 байт и одинаковый
SHA-256 `b24c37886142e11d0ee687db6ab06f936207aa7f2ea1fd1d9a36763c7a507e6a`.
Проверены чтение, пропуск байтов, повторное открытие и отказ при коротком
входном потоке. Для повтора из корня KolobanovEye под Linux x86-64 с g++:

```sh
git -C /path/to/DagorEngine apply /path/to/KolobanovEye/patches/dagor-ooz.patch
python tests/ooz_linux.py --dagor /path/to/DagorEngine --ooz /path/to/ooz
```

Если патч уже применён, начните со второй команды. Линковку Windows и
игровые пакеты нужно проверить отдельно.

Для этого режима нужен локальный Git checkout [powzix/ooz](https://github.com/powzix/ooz) на коммите
`05038060aa68f9187ae9923b2388ca8db40e58d1` и уже собранный Windows x64 `dumpGrp-dev.exe`.
Откройте **Developer PowerShell for VS** с целевой архитектурой **x64**. В нём должны быть
доступны `cl.exe`, `lib.exe` и установленный Windows SDK. Выполните команды из каталога
KolobanovEye, обновлённого с GitHub.

```powershell
$env:Path = "D:\Develop\devtools;$env:Path"
git -C D:\Projects\ooz rev-parse HEAD
python .\kolobanoveye.py probe-ooz --game D:\Games\WarThunder --ooz-exe D:\Projects\ooz\x64\Release\ooz.exe
```

`probe-ooz` читает DDSx из вложенного блока `TEX` в `avg_normandy.bin`.
Он выводит смещение, размеры, первый тип потока и параметры текстуры. Для обычной
2D-текстуры PC сверяет сумму размеров mip-уровней с DDSx. Скрипт добавляет
8-байтовую длину только для CLI, запускает его отдельным процессом, проверяет
длину результата и печатает `OOZ_LIGHTMAP_OK` с SHA-256. Временные файлы
удаляются, карта открывается только для чтения. Контрольная сумма позволяет
сравнить результаты повторных запусков. Успешный тест покрывает одну lightmap.

Если `rev-parse` вернул ошибку или другой коммит, подготовьте отдельный checkout
и передайте `--ooz D:\Projects\ooz-src` в команду сборки ниже. Для
`probe-ooz` можно использовать уже собранный `ooz.exe`.

```powershell
git clone https://github.com/powzix/ooz.git D:\Projects\ooz-src
git -C D:\Projects\ooz-src checkout --detach 05038060aa68f9187ae9923b2388ca8db40e58d1
```

После `OOZ_LIGHTMAP_OK` выполните:

```powershell
python .\kolobanoveye.py build --dagor D:\Projects\DagorEngine --ooz D:\Projects\ooz
```

Скрипт применяет `patches/dagor-ooz.patch`, собирает локальную x64-библиотеку
декодера и запускает Jam только для DaEditorX с его зависимостями, включая
динамический `daKernel`. Журналы — `build-logs\ooz-compile.log`,
`build-logs\ooz-lib.log` и `build-logs\editor-ooz.log`. В последнем должна быть
строка `Building daKernel with the decode-only ooz backend`. Флаги Jam этого
режима — `-sUseOoz=yes` и `-sOozLib=<абсолютный путь к ke-ooz.lib>`.
Обычный `build` сохраняет штатный выбор SDK или заглушки. Jam по-прежнему
сообщает об отсутствующей или неполной версии SDK по своим штатным правилам.

```powershell
python .\kolobanoveye.py frame --dagor D:\Projects\DagorEngine --game D:\Games\WarThunder --out D:\Projects\ke-run-03 --model pzkpfw_VI_ausf_h1_tiger --skeleton pzkpfw_VI_ausf_h1_tiger_skeleton
```

При ошибке сохраните вывод `probe-ooz`. Если он успешен, приложите первый сбой из
`editor-ooz.log` либо из журнала `debug` под `ke-run-03\.logs`. Запись
`FRAME_WRITTEN` и визуальная проверка `ke-run-03\frame.png` подтверждают
получение кадра.

`ooz` читает Kraken, Mermaid/Selkie, Leviathan, LZNA и Bitknit. Поддержка
контрольных сумм Oodle quantum отсутствует в upstream. Backend отклоняет такие
потоки и возвращает ошибку на вызовах сжатия. Потоковый интерфейс Dagor
распаковывает блок целиком при первом чтении или положительном `seekrel`.
Лимит — 256 МиБ отдельно для входа и выхода. У каждого буфера дополнительно
выделяется 64 байта для доступа SIMD на границе. Upstream прямо предупреждает,
что декодер не fuzz-safe. Проверка заголовков и размеров не делает
безопасными произвольные повреждённые потоки. Этот режим рассчитан на
ресурсы доверенной локальной установки.

Код декодера `ooz` распространяется по GPL-3.0-or-later. Скрипт берёт
исходники из указанного checkout, готовит локальную копию без CLI, добавляет
проверку отказа выделения памяти, исправляет индексирование двумерной
таблицы LZNA и явно отклоняет флаг контрольных сумм.
В репозиторий входят адаптер и скрипт, а исходники декодера и собранный с ним
`daKernel-dev.dll` остаются у пользователя. При распространении сборки с
`ooz` нужно выполнить условия GPL для распространяемого результата.
[Исходники и лицензия ooz](https://github.com/powzix/ooz/blob/05038060aa68f9187ae9923b2388ca8db40e58d1/kraken.cpp)
