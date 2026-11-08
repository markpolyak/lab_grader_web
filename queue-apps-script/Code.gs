/**
 * Lab Defense Queue — автозакрытие очереди (Google Apps Script), версия 2.
 *
 * Скрипт работает внутри Google Таблицы курса и НЕОБЯЗАТЕЛЕН: основное закрытие
 * выполняет ленивая проверка в backend. Скрипт делает ту же самую операцию
 * по расписанию, поэтому при исправной работе backend находит нечего закрывать.
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
 * Контракт с backend (backend должен делать то же самое и теми же правилами):
 *   - формат строки истории: Дата | ПОЗИЦИЯ | ГРУППА | ФИО | ЛР | Пар№ | Статус;
 *   - ключ строки: Дата | ПОЗИЦИЯ | ГРУППА | ФИО | ЛР (повторная запись студента
 *     на ту же ЛР в тот же день имеет другую ПОЗИЦИЮ и не считается дублем);
 *   - перед копированием проверять и брать аренду copy_lease на листе autoclose_status;
 *   - значения служебного листа autoclose_status (ключ | значение):
 *       пишет скрипт:  installed_at, last_run, last_success, last_error,
 *                      last_rows_copied, script_version, copy_lease
 *       пишет backend: script_enabled (TRUE/FALSE, галочка в интерфейсе),
 *                      utc_offset (например +03:00, из timezone курса),
 *                      service_account_email (для защиты истории)
 */

const VERSION = '2';

const QUEUE_PREFIX = 'Очередь-';
const QUEUE_SHEET_RE = /^Очередь-(\d{4}-\d{2}-\d{2})$/;
const HISTORY_SHEET = 'История_очереди';
const STATUS_SHEET = 'autoclose_status';
const HISTORY_HEADERS = ['Дата', 'ПОЗИЦИЯ', 'ГРУППА', 'ФИО', 'ЛР', 'Пар№', 'Статус'];

const STATUS_NOT_REACHED = 'не_дошло';
// Статусы, которые при закрытии становятся «не_дошло» (запись была, результата нет).
// Если появится статус «вызван», добавьте его сюда.
const NOT_FINISHED = ['ждёт', ''];

const TRIGGER_HOUR = 1;            // ежедневный запуск около 01:30
const LEASE_OWNER = 'script';
const LEASE_TTL_MS = 5 * 60 * 1000;
const LOCK_WAIT_MS = 30000;

/* ---------- Меню (только установка и информация, закрывать вручную нельзя) ---------- */

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Очередь')
    .addItem('Включить автозакрытие', 'installTriggers')
    .addItem('Выключить автозакрытие', 'removeTriggers')
    .addItem('Статус автозакрытия', 'showStatus')
    .addItem('Защитить историю', 'protectHistory')
    .addToUi();
}

/* ---------- Установка ---------- */

/** Запускается один раз преподавателем. Создаёт ежедневный триггер. */
function installTriggers() {
  const ss = SpreadsheetApp.getActive();
  ensureStatusSheet_(ss);

  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'closeDay')
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('closeDay')
    .timeBased()
    .everyDays(1)
    .atHour(TRIGGER_HOUR)
    .nearMinute(30)
    .create();

  setStatus_(ss, 'installed_at', nowIso_());
  setStatus_(ss, 'script_version', VERSION);

  closeDay(); // первый прогон сразу: проверяет доступы и формат листов
  notify_('Автозакрытие включено: ежедневно около 01:30. Состояние: лист «' + STATUS_SHEET + '».');
}

function removeTriggers() {
  const ss = SpreadsheetApp.getActive();
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'closeDay')
    .forEach(t => ScriptApp.deleteTrigger(t));
  setStatus_(ss, 'installed_at', '');
  notify_('Автозакрытие отключено.');
}

/* ---------- Закрытие ---------- */

/** Обработчик триггера. Безопасен при повторном и одновременном запуске. */
function closeDay() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(LOCK_WAIT_MS)) return; // другой запуск скрипта уже идёт

  const ss = SpreadsheetApp.getActive();
  try {
    ensureStatusSheet_(ss);
    setStatus_(ss, 'last_run', nowIso_());

    if (isDisabled_(ss)) return;           // преподаватель снял галочку в интерфейсе
    if (!acquireLease_(ss)) return;        // backend сейчас сам закрывает день

    const errors = [];
    let copied = 0;
    try {
      const today = todayStr_(ss);
      ss.getSheets().forEach(sheet => {
        const m = QUEUE_SHEET_RE.exec(sheet.getName());
        if (!m || m[1] >= today) return;   // только строго прошлые даты и только наш формат
        try {
          copied += closeSheet_(ss, sheet, m[1]);
        } catch (e) {
          errors.push(sheet.getName() + ': ' + e.message);
        }
      });
    } finally {
      releaseLease_(ss);
    }

    setStatus_(ss, 'last_rows_copied', copied);
    setStatus_(ss, 'last_error', errors.join(' | '));
    if (errors.length === 0) setStatus_(ss, 'last_success', nowIso_());
  } finally {
    lock.releaseLock();
  }
}

/** Копирует один лист очереди в историю и удаляет его. Возвращает число строк. */
function closeSheet_(ss, sheet, dateStr) {
  const history = ensureHistorySheet_(ss);
  const rows = readQueueRows_(sheet, dateStr);

  if (rows.length > 0) {
    const existing = readHistoryKeys_(history, dateStr);
    rows.forEach(r => {
      const key = rowKey_(r);
      if (existing[key]) return;           // уже скопировано раньше (повторный запуск)
      history.appendRow(r);
      existing[key] = true;
    });
    SpreadsheetApp.flush();

    // контроль полноты: каждая строка очереди обязана быть в истории
    const after = readHistoryKeys_(history, dateStr);
    rows.forEach(r => {
      if (!after[rowKey_(r)]) throw new Error('строка не попала в историю: ' + rowKey_(r));
    });
  }

  // лист удаляем, только если он не изменился с момента чтения
  if (JSON.stringify(readQueueRows_(sheet, dateStr)) !== JSON.stringify(rows)) {
    throw new Error('лист изменился во время закрытия, повтор при следующем запуске');
  }
  ss.deleteSheet(sheet);
  return rows.length;
}

/** Строки очереди -> строки истории (формат листа «История_очереди»). */
function readQueueRows_(sheet, dateStr) {
  const last = sheet.getLastRow();
  if (last < 2) return [];
  const out = [];
  sheet.getRange(2, 1, last - 1, 6).getValues().forEach(r => {
    if (r[0] === '' && r[1] === '' && r[2] === '') return; // пустая строка
    let status = String(r[5]).trim();
    if (NOT_FINISHED.indexOf(status) >= 0) status = STATUS_NOT_REACHED;
    out.push([
      dateStr, r[0],
      String(r[1]).trim(), String(r[2]).trim(), String(r[3]).trim(), String(r[4]).trim(),
      status
    ]);
  });
  return out;
}

function rowKey_(r) {
  return [r[0], r[1], r[2], r[3], r[4]].map(v => String(v).trim()).join('|');
}

function readHistoryKeys_(history, dateStr) {
  const keys = {};
  const last = history.getLastRow();
  if (last < 2) return keys;
  history.getRange(2, 1, last - 1, 7).getValues().forEach(r => {
    if (cellDate_(r[0]) !== dateStr) return;
    keys[rowKey_([dateStr, r[1], r[2], r[3], r[4]])] = true;
  });
  return keys;
}

/** Дата из ячейки истории: Таблицы могли превратить строку в настоящую дату. */
function cellDate_(v) {
  if (v instanceof Date) {
    return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  return String(v).trim();
}

/* ---------- Лист истории и защита ---------- */

function ensureHistorySheet_(ss) {
  let sheet = ss.getSheetByName(HISTORY_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(HISTORY_SHEET);
    sheet.getRange(1, 1, 1, 7).setValues([HISTORY_HEADERS]);
    sheet.setFrozenRows(1);
  }
  sheet.getRange('A:A').setNumberFormat('@'); // дата хранится текстом, чтобы не превращаться в Date
  return sheet;
}

/** Защищает лист истории целиком: править могут только владелец и сервисный аккаунт. */
function protectHistory() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureHistorySheet_(ss);
  const found = sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET);
  const protection = found.length > 0 ? found[0] : sheet.protect();
  protection.setDescription('История очереди: только добавление');
  protection.setWarningOnly(false);
  protection.removeEditors(protection.getEditors());
  if (protection.canDomainEdit()) protection.setDomainEdit(false);

  const email = getStatus_(ss, 'service_account_email');
  if (email) protection.addEditor(email);
  notify_(email
    ? 'История защищена. Редактор: владелец и ' + email + '.'
    : 'История защищена. Адрес сервисного аккаунта не найден в «' + STATUS_SHEET +
      '» (ключ service_account_email): добавьте его редактором защиты вручную, иначе backend не сможет писать.');
}

/* ---------- Служебный лист autoclose_status ---------- */

function ensureStatusSheet_(ss) {
  let sheet = ss.getSheetByName(STATUS_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(STATUS_SHEET);
    sheet.getRange(1, 1, 1, 2).setValues([['key', 'value']]);
    sheet.getRange('B:B').setNumberFormat('@'); // значения только текстом (иначе ISO-даты станут датами)
  }
  return sheet;
}

function getStatus_(ss, key) {
  const sheet = ss.getSheetByName(STATUS_SHEET);
  if (!sheet || sheet.getLastRow() < 2) return '';
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 2).getValues();
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] === key) return String(rows[i][1]);
  }
  return '';
}

function setStatus_(ss, key, value) {
  const sheet = ensureStatusSheet_(ss);
  const last = sheet.getLastRow();
  if (last >= 2) {
    const keys = sheet.getRange(2, 1, last - 1, 1).getValues();
    for (let i = 0; i < keys.length; i++) {
      if (keys[i][0] === key) {
        sheet.getRange(i + 2, 2).setNumberFormat('@').setValue(String(value));
        return;
      }
    }
  }
  sheet.appendRow([key, String(value)]);
}

function isDisabled_(ss) {
  return getStatus_(ss, 'script_enabled').trim().toUpperCase() !== 'TRUE';
}

/**
 * Аренда: «кто сейчас копирует». Не атомарна (у Таблиц нет compare-and-set),
 * поэтому это лишь снижение вероятности гонки; от дублей защищает ключ строки.
 */
function acquireLease_(ss) {
  const current = getStatus_(ss, 'copy_lease');
  if (current) {
    const parts = current.split('|');
    if (parts[0] !== LEASE_OWNER && Number(parts[1]) > Date.now()) return false;
  }
  setStatus_(ss, 'copy_lease', LEASE_OWNER + '|' + (Date.now() + LEASE_TTL_MS));
  return true;
}

function releaseLease_(ss) {
  setStatus_(ss, 'copy_lease', '');
}

/* ---------- Время ---------- */

/** «Сегодня» по часовому поясу курса (utc_offset из backend), иначе по поясу скрипта. */
function todayStr_(ss) {
  const m = /^([+-])(\d{2}):(\d{2})$/.exec(getStatus_(ss, 'utc_offset'));
  if (m) {
    const minutes = (m[1] === '-' ? -1 : 1) * (parseInt(m[2], 10) * 60 + parseInt(m[3], 10));
    return Utilities.formatDate(new Date(Date.now() + minutes * 60000), 'UTC', 'yyyy-MM-dd');
  }
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

function nowIso_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd'T'HH:mm:ssXXX");
}

/* ---------- Информация ---------- */

function showStatus() {
  const ss = SpreadsheetApp.getActive();
  const keys = ['installed_at', 'script_version', 'script_enabled', 'last_run',
                'last_success', 'last_error', 'last_rows_copied'];
  notify_(keys.map(k => k + ': ' + (getStatus_(ss, k) || '—')).join('\n'));
}

function notify_(message) {
  try {
    SpreadsheetApp.getUi().alert(message);
  } catch (e) {
    // запуск без интерфейса (триггер): ничего не показываем
  }
}
