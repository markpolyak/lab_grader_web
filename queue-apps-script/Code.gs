/**
 * Lab Defense Queue — автозакрытие очереди (Google Apps Script), версия 2.3.
 *
 * Изменения относительно версии 2.2:
 *   1. Служебный лист autoclose_status БОЛЬШЕ НЕ НУЖЕН: всё состояние скрипта
 *      хранится в Script Properties (PropertiesService) — внутри проекта
 *      скрипта. Лист от версии 2.x при первом прогоне переносится в свойства
 *      и автоматически удаляется из таблицы.
 *   2. Часовой пояс «сегодня» берётся из настроек САМОЙ ТАБЛИЦЫ
 *      (Файл → Настройки), а не из ключа utc_offset. Поставьте таблице
 *      часовой пояс курса один раз — ключ utc_offset больше не нужен.
 *   3. Выравнивание/формат истории: перед дописыванием строк целевой
 *      диапазон форматируется как plain text ('@'). Раньше Sheets на лету
 *      превращал «2026-11-11» в настоящую дату (выравнивание вправо) и
 *      числа — в числа, отчего строки истории выглядели «вразнобой».
 *      Теперь все строки хранятся текстом, единообразно с backend
 *      (appendCells со stringValue).
 *   4. Аренда copy_lease удалена: она была нужна для координации с backend,
 *      а backend в неё не играет. От одновременных запусков самого скрипта
 *      защищает LockService. Защита #16 усилена: «лист изменился» проверяется
 *      ДО любых записей — при правке во время закрытия ни история, ни лист
 *      не затрагиваются, свежая правка преподавателя теряться не может.
 *
 *   5. Меню зависит от состояния: «Включить автозакрытие» видно только пока
 *      скрипт не установлен (нет триггера), «Выключить» — только когда
 *      установлен; «Разрешить/Запретить закрытие» — по значению script_enabled.
 *      Меню перестраивается при перезагрузке страницы таблицы.
 *
 * Унаследовано из версии 2.1:
 *   - installTriggers() по умолчанию ставит script_enabled=TRUE;
 *   - пункты меню «Разрешить/запретить скрипту закрытие»;
 *   - showStatus() показывает диагностику (теперь из свойств).
 *
 * Что делает closeDay():
 *   для каждого листа «Очередь-YYYY-MM-DD» с датой СТРОГО раньше сегодняшней
 *   1. копирует его строки в «История_очереди» (статус «ждёт» или пустой -> «не_дошло»);
 *   2. проверяет, что все строки попали в историю;
 *   3. только после этого удаляет лист очереди.
 *
 * Правила безопасности:
 *   - сегодняшний и будущие листы не трогаются никогда;
 *   - листы с посторонними именами (например «..._conflict») пропускаются;
 *   - повторный запуск после сбоя доделывает остаток и не создаёт дублей;
 *   - листы очереди скрипт не создаёт и не очищает (это делает backend);
 *   - ручных действий нет: закрытие только по триггеру.
 *
 * Контракт с backend:
 *   - формат строки очереди:  ПОЗИЦИЯ | ГРУППА | ФИО | ЛР | Пар№ | GitHub | Время | Статус;
 *   - формат строки истории:  Дата | ПОЗИЦИЯ | ГРУППА | ФИО | ЛР | Пар№ | GitHub | Время | Статус;
 *   - при повторном запуске строки сверяются как мультимножество по Дате,
 *     ГРУППЕ, ФИО, ЛР и Пар№; повторные записи сохраняются отдельно;
 *   - все значения истории хранятся ТЕКСТОМ (формат '@'), в том же виде,
 *     в каком их пишет backend (appendCells со stringValue);
 *   - скрипт самодостаточен: backend ничего не пишет в таблицу для него.
 *     Состояние скрипта — в Script Properties, часовой пояс «сегодня» —
 *     в настройках таблицы.
 */

const VERSION = '2.4';

const QUEUE_PREFIX = 'Очередь-';
const QUEUE_SHEET_RE = /^Очередь-(\d{4}-\d{2}-\d{2})$/;
const HISTORY_SHEET = 'История_очереди';
const LEGACY_STATUS_SHEET = 'autoclose_status'; // лист версий 2.x: значения переносятся в свойства
const QUEUE_COLUMNS = 8;   // ПОЗИЦИЯ..Статус
const HISTORY_COLUMNS = 9; // Дата + ПОЗИЦИЯ..Статус
const HISTORY_HEADERS = ['Дата', 'ПОЗИЦИЯ', 'ГРУППА', 'ФИО', 'ЛР', 'Пар№', 'GitHub', 'Время', 'Статус'];

const STATUS_NOT_REACHED = 'не_дошло';
// Статусы, которые при закрытии становятся «не_дошло» (запись была, результата нет).
const NOT_FINISHED = ['ждёт', ''];

const TRIGGER_HOUR = 2;            // ежедневный запуск около 02:50
const LOCK_WAIT_MS = 30000;

/* ---------- Служебные свойства (вместо листа autoclose_status) ---------- */

function prop_(key) {
  return PropertiesService.getScriptProperties().getProperty(key) || '';
}

function setProp_(key, value) {
  const properties = PropertiesService.getScriptProperties();
  if (value === '' || value === null || value === undefined) {
    properties.deleteProperty(key);
  } else {
    properties.setProperty(key, String(value));
  }
}

/**
 * Перенос значений из старого листа autoclose_status (версии 2.x) в свойства.
 * Лист — чисто служебный, после переноса удаляется (если удалить не вышло,
 * например из-за защиты, — он останется и попытка повторится при следующем
 * прогоне; статус напомнит о нём).
 */
function migrateLegacyStatusSheet_(ss) {
  const legacy = ss.getSheetByName(LEGACY_STATUS_SHEET);
  if (!legacy) return;
  const last = legacy.getLastRow();
  if (last >= 2) {
    legacy.getRange(2, 1, last - 1, 2).getValues().forEach(row => {
      const key = String(row[0]).trim();
      const value = String(row[1]).trim();
      if (!key || !value) return;
      // utc_offset заменён часовым поясом таблицы, copy_lease удалён.
      if (key === 'utc_offset' || key === 'copy_lease') return;
      if (prop_(key) === '') setProp_(key, value);
    });
  }
  try {
    ss.deleteSheet(legacy);
  } catch (e) {
    // защищён/заблокирован — попробуем в следующий раз, статус напомнит
  }
}

/* ---------- Меню ---------- */

/**
 * Меню зависит от состояния: показываем только релевантные действия.
 * Состояние читается из Script Properties — они доступны в onOpen.
 * При самом первом открытии (авторизации скрипта ещё нет) чтение может
 * упасть — тогда показываем полный набор, лишние пункты безвредны.
 * Меню перестраивается при перезагрузке страницы таблицы.
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  const menu = ui.createMenu('Очередь');

  let installed = null;
  let enabled = null;
  try {
    installed = prop_('installed_at') !== '';
    enabled = prop_('script_enabled') === 'TRUE';
  } catch (e) {
    // AuthMode.LIMITED без авторизации — состояние нечитаемо, показываем всё
  }

  if (installed !== true) menu.addItem('Включить автозакрытие', 'installTriggers');
  if (installed !== false) menu.addItem('Выключить автозакрытие', 'removeTriggers');
  if (enabled !== true) menu.addItem('Разрешить скрипту закрытие', 'enableScript');
  if (enabled !== false) menu.addItem('Запретить скрипту закрытие', 'disableScript');
  menu.addSeparator();
  menu.addItem('Статус автозакрытия', 'showStatus');
  menu.addItem('Защитить историю', 'protectHistory');
  menu.addToUi();
}

/* ---------- Установка ---------- */

/** Запускается один раз преподавателем. Создаёт ежедневный триггер. */
function installTriggers() {
  const ss = SpreadsheetApp.getActive();
  migrateLegacyStatusSheet_(ss);

  // По умолчанию скрипт включён. Если состояние уже есть (меню или legacy) —
  // не перезаписываем решение.
  if (prop_('script_enabled') === '') {
    setProp_('script_enabled', 'TRUE');
  }

  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'closeDay')
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('closeDay')
    .timeBased()
    .everyDays(1)
    .atHour(TRIGGER_HOUR)
    .nearMinute(50)
    .create();

  setProp_('installed_at', nowIso_(ss));
  setProp_('script_version', VERSION);

  closeDay(); // первый прогон сразу
  notify_('Автозакрытие включено: ежедневно около 02:50.\n' +
    'Часовой пояс «сегодня»: ' + ss.getSpreadsheetTimeZone() +
    ' — проверьте Файл → Настройки таблицы (должен совпадать с курсом).\n' +
    'Отключение: Очередь → Запретить скрипту закрытие / Выключить автозакрытие.');
}

function removeTriggers() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'closeDay')
    .forEach(t => ScriptApp.deleteTrigger(t));
  setProp_('installed_at', '');
  notify_('Автозакрытие отключено (триггеры удалены). script_enabled не изменён.\nОбновите страницу — меню перестроится.');
}

/* Ручное управление script_enabled. */
function enableScript() {
  setProp_('script_enabled', 'TRUE');
  notify_('Скрипту разрешено закрытие (script_enabled=TRUE).');
}

function disableScript() {
  setProp_('script_enabled', 'FALSE');
  notify_('Скрипту запрещено закрытие (script_enabled=FALSE).');
}

/* ---------- Закрытие ---------- */

/** Обработчик триггера. Безопасен при повторном и одновременном запуске. */
function closeDay() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(LOCK_WAIT_MS)) return;

  const ss = SpreadsheetApp.getActive();
  try {
    migrateLegacyStatusSheet_(ss);
    setProp_('last_run', nowIso_(ss));

    if (isDisabled_()) return;             // преподаватель запретил скрипт

    const errors = [];
    let copied = 0;
    const today = todayStr_(ss);
    ss.getSheets().forEach(sheet => {
      const m = QUEUE_SHEET_RE.exec(sheet.getName());
      if (!m || m[1] >= today) return;     // только строго прошлые даты
      try {
        copied += closeSheet_(ss, sheet, m[1]);
      } catch (e) {
        errors.push(sheet.getName() + ': ' + e.message);
      }
    });

    setProp_('last_rows_copied', String(copied));
    setProp_('last_error', errors.join(' | '));
    if (errors.length === 0) setProp_('last_success', nowIso_(ss));
  } finally {
    lock.releaseLock();
  }
}

/** Копирует один лист очереди в историю и удаляет его. Возвращает число строк. */
function closeSheet_(ss, sheet, dateStr) {
  const history = ensureHistorySheet_(ss);
  const rows = readQueueRows_(sheet, dateStr);
  let copied = 0;

  // Защита #16 — ДО любых записей: если лист изменился с момента чтения
  // (преподаватель правит прошлый день прямо сейчас), не пишем ничего и
  // не удаляем лист. Следующий прогон прочитает свежее состояние целиком;
  // идемпотентный ключ без статуса гарантированно не теряет новую правку.
  if (JSON.stringify(readQueueRows_(sheet, dateStr)) !== JSON.stringify(rows)) {
    throw new Error('лист изменился во время закрытия, повтор при следующем запуске');
  }

  if (rows.length > 0) {
    const existingCounts = readHistoryCounts_(history, dateStr);
    const seenCounts = {};
    const pending = [];
    rows.forEach(r => {
      const key = rowKey_(r);
      seenCounts[key] = (seenCounts[key] || 0) + 1;
      if (seenCounts[key] <= (existingCounts[key] || 0)) return;
      pending.push(r);
    });

    if (pending.length > 0) {
      appendHistoryRows_(history, pending);
      copied = pending.length;
      SpreadsheetApp.flush();

      // контроль полноты (appendRow не атомарен, как batch у backend)
      const after = readHistoryCounts_(history, dateStr);
      Object.keys(seenCounts).forEach(key => {
        if ((after[key] || 0) < seenCounts[key]) {
          throw new Error('строки не попали в историю: ' + key);
        }
      });
    }
  }

  ss.deleteSheet(sheet);
  return copied;
}

/**
 * Дописывает строки в историю единообразным текстом.
 * Sheets разбирает значения при записи по формату ячеек: без '@' строка
 * «2026-11-11» превращается в настоящую дату (выравнивание вправо), числа —
 * в числа. Формат plain text на целевом диапазоне ДО записи держит все
 * строки текстом, единообразно с backend (appendCells со stringValue).
 */
function appendHistoryRows_(history, pending) {
  const startRow = history.getLastRow() + 1;
  const needed = startRow + pending.length - 1;
  if (needed > history.getMaxRows()) {
    history.insertRowsAfter(history.getMaxRows(), needed - history.getMaxRows());
  }
  history.getRange(startRow, 1, pending.length, HISTORY_COLUMNS).setNumberFormat('@');
  pending.forEach(r => history.appendRow(r));
}

/**
 * Строки очереди -> строки истории.
 * Очередь (8 колонок): ПОЗИЦИЯ | ГРУППА | ФИО | ЛР | Пар№ | GitHub | Время | Статус.
 * История (9 колонок): + Дата перед ними, Статус остаётся последним.
 */
function readQueueRows_(sheet, dateStr) {
  const last = sheet.getLastRow();
  if (last < 2) return [];
  const out = [];
  sheet.getRange(2, 1, last - 1, QUEUE_COLUMNS).getValues().forEach(r => {
    if (r[0] === '' && r[1] === '' && r[2] === '') return; // пустая строка
    let status = String(r[7]).trim();
    if (NOT_FINISHED.indexOf(status) >= 0) status = STATUS_NOT_REACHED;
    out.push([
      dateStr, r[0],
      String(r[1]).trim(), String(r[2]).trim(), String(r[3]).trim(), String(r[4]).trim(),
      String(r[5]).trim(), String(r[6]).trim(),
      status
    ]);
  });
  return out;
}

function rowKey_(r) {
  // Дата | ГРУППА | ФИО | ЛР | Пар№ — индексы 0,2,3,4,5 строки истории.
  return JSON.stringify([r[0], r[2], r[3], r[4], r[5]]
    .map(v => String(v).trim()));
}

function readHistoryCounts_(history, dateStr) {
  const counts = {};
  const last = history.getLastRow();
  if (last < 2) return counts;
  history.getRange(2, 1, last - 1, HISTORY_COLUMNS).getValues().forEach(r => {
    if (cellDate_(r[0]) !== dateStr) return;
    const key = rowKey_([cellDate_(r[0]), ...r.slice(1)]);
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

function cellDate_(v) {
  if (v instanceof Date) {
    return Utilities.formatDate(v, SpreadsheetApp.getActive().getSpreadsheetTimeZone(), 'yyyy-MM-dd');
  }
  return String(v).trim();
}

/* ---------- Лист истории и защита ---------- */

function ensureHistorySheet_(ss) {
  let sheet = ss.getSheetByName(HISTORY_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(HISTORY_SHEET);
    sheet.getRange(1, 1, 1, HISTORY_COLUMNS).setValues([HISTORY_HEADERS]);
    sheet.setFrozenRows(1);
    // весь столбец набора — plain text (см. appendHistoryRows_)
    sheet.getRange(1, 1, sheet.getMaxRows(), HISTORY_COLUMNS).setNumberFormat('@');
  }
  return sheet;
}

function protectHistory() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureHistorySheet_(ss);
  const found = sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET);
  const protection = found.length > 0 ? found[0] : sheet.protect();
  protection.setDescription('История очереди: только добавление');
  protection.setWarningOnly(false);
  protection.removeEditors(protection.getEditors());
  if (protection.canDomainEdit()) protection.setDomainEdit(false);

  let email = prop_('service_account_email');
  if (!email) {
    try {
      const ui = SpreadsheetApp.getUi();
      const result = ui.prompt(
        'Защитить историю',
        'Адрес сервисного аккаунта backend (или пусто — пропустить):',
        ui.ButtonSet.OK_CANCEL
      );
      if (result.getSelectedButton() === ui.Button.OK) {
        email = result.getResponseText().trim();
        if (email) setProp_('service_account_email', email);
      }
    } catch (e) {
      // запуск без интерфейса — просто защищаем без доп. редактора
    }
  }
  if (email) protection.addEditor(email);
  notify_(email
    ? 'История защищена. Редактор: владелец и ' + email + '.'
    : 'История защищена. Редактор: только владелец.');
}

/* ---------- Время ---------- */

/** «Сегодня» по часовому поясу самой таблицы (Файл → Настройки). */
function todayStr_(ss) {
  return Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), 'yyyy-MM-dd');
}

function nowIso_(ss) {
  return Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), "yyyy-MM-dd'T'HH:mm:ssXXX");
}

/* ---------- Информация ---------- */

function showStatus() {
  const ss = SpreadsheetApp.getActive();
  const keys = ['installed_at', 'script_version', 'script_enabled', 'last_run',
                'last_success', 'last_error', 'last_rows_copied'];
  const lines = keys.map(k => k + ': ' + (prop_(k) || '—'));
  lines.push('timezone (настройки таблицы): ' + ss.getSpreadsheetTimeZone());
  if (ss.getSheetByName(LEGACY_STATUS_SHEET)) {
    lines.push('Найден лист ' + LEGACY_STATUS_SHEET + ' от старой версии: значения перенесены,\nно удалить лист автоматически не удалось (защита?). Удалите вручную.');
  }
  notify_(lines.join('\n'));
}

function isDisabled_() {
  return prop_('script_enabled').trim().toUpperCase() !== 'TRUE';
}

function notify_(message) {
  try {
    SpreadsheetApp.getUi().alert(message);
  } catch (e) {
    // запуск без интерфейса (триггер): ничего не показываем
  }
}
