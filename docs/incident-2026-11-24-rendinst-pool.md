# Инцидент 24.11.2026: RendInst unitedVdata OOM при смене карт

В Windows-прогоне с выключенным capture_visibility_masks запросили 120 кадров
на 20 картах. В журнале 16 MAP_TASK_OK, 96 FRAME_OK; 17-я карта
avg_finland.bin падает в AcesScene::bdlCustomLoad → preload_all_required_res →
RendInstGameResFactory::loadGameResourceData → ShaderResUnitedVdata::addRes.
Фатальная проверка shaderResUnitedData.cpp:1814 — buf.allowRebuild.
Контекст: avg_finland_winter.grp → finland_detailed_forest_fir_b →
tree_fir_snow.grp → tree_fir_snow_lush_large_a.

Пул: 6258 зарегистрированных ресурсов, 15 VBs по 64 MiB, суммарное занятое
пространство ~1137 MiB вместе с IB, большинство VBs заполнены на 99–100%.
Движок попытался discard unused, но не смог выделить очередной ресурс.
Это лимит/фрагментация объединённого пула, а не доказательство исчерпания
всей VRAM. Маски выключены, падение до MODEL_OK новой карты.

В закреплённом Dagor AcesScene::clear освобождает сцену и clearRIGen,
но не запускает free_unused_game_resources(). Эта функция штатно повторяет
проходы по фабрикам с forced=false, освобождая ресурсы без внешних ссылок.
RendInstGameResFactory::freeUnusedResources вызывает delRes и
releaseUnusedBuffers; при нулевом числе ресурсов очищает unitedVdata.

Исправление-кандидат: после clear() и сброса levelSettingsBlk, перед
load_binary_dump, при -kolobanoveye-probe вызываем нефорсированную сборку
неиспользуемых ресурсов. Это не ослабляет assertion и не повышает предел.
В debug: [KE] MAP_RESOURCE_GC map=... ri_before=... ri_after=... released=...
Отдельный тест проверяет точку вставки, идемпотентность и CRLF.
Сообщение о native OOM теперь выводит контекст и последний MAP_TASK_BEGIN
даже после длинного дампа ресурсов.

Приёмка: пересобрать Dagor; повторить исходный 20-картовый набор при
выключенных масках. Сравнить ri_before/ri_after на переходах и подтвердить
120 FRAME_OK + MAP_BATCH_OK. Затем отдельно проверить режим масок.
Если счётчик не падает или OOM повторится, исследовать оставшиеся
владельцы ссылок (RIGen, LandClass, отложенные LOD-загрузки); не делать
forced unload. До Windows-проверки исправление не считается подтверждённым.
