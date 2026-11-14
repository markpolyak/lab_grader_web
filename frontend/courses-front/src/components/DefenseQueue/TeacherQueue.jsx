import { queueError, q, useQueueLanguage, QueueLanguage } from "./locale";
import { ErrorNotice } from "./Notice";
import { Hint } from "./Hint";
import { Fragment, useEffect, useRef, useState } from "react";
import { fetchCourses, fetchGroups, queueGroupPath, queueRequest } from "../../api";
import { usePolling } from "./usePolling";
import { entryLabel, ALL_GROUPS, filterSessions, localDateTime } from "./state";
import "./queue.css";
import { queueAppearance } from "./appearance";
import { RoundQueue, QueuePreferences } from "./RoundQueue";
const coursePath = id => `/admin/courses/${encodeURIComponent(id)}/queue`;
const eventNames = {
  config_schedule_override: "Применена поправка расписания",
  called: "Вызван",
  defended: "Защитил",
  failed: "Не защитил",
  no_show: "Не явился",
  withdrawn: "Исключён",
  added_to_tail: "Добавлен в конец",
  roster_frozen: "Состав зафиксирован",
  schedule_changed: "Занятие перенесено",
  enrollment_closed: "Запись закрыта",
  enrollment_opened: "Запись открыта",
  cancelled: "Занятие отменено",
  restored: "Занятие восстановлено",
  call_cancelled: "Вызов отменён",
  arrived: "Студент вернулся",
  action_undone: "Действие отменено",
  result_corrected: "Результат исправлен",
  sheet_defended: "Защита подтверждена таблицей",
  session_finished: "Ожидание завершено"
};
const statHelp = {
  called: "Студенты, которых преподаватель уже вызвал и ещё не отметил результат.",
  waiting: "Студенты, ожидающие вызова, включая добавленных преподавателем и повторные попытки.",
  defended: "Успешные защиты на этом занятии. Один студент может защитить несколько работ.",
  failed: "Завершённые попытки с результатом «Не сдал» на этом занятии.",
  no_show: "Студенты, которых преподаватель отметил как отсутствующих на этом занятии."
};
export function TeacherQueue({
  initialCourse = ""
}) {
  const { displayDate } = useQueueLanguage();
  const [courses, setCourses] = useState([]),
    [course, setCourse] = useState(initialCourse);
  const [groups, setGroups] = useState([]),
    [group, setGroup] = useState("");
  const [sessionId, setSessionId] = useState(""),
    [tab, setTab] = useState("queue");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const sessions = usePolling("/admin/queue/sessions");
  const filtered = filterSessions(sessions.data, course, group);
  const selectedSession = filtered.find(session => session.session_id === sessionId);
  const selectedGroup = selectedSession?.group_id;
  const path = course && selectedGroup ? queueGroupPath(course, selectedGroup, true) : null;
  const roster = usePolling(path && sessionId ? `${path}?session_id=${encodeURIComponent(sessionId)}` : null);
  const [settingsVersion, setSettingsVersion] = useState(0);
  useEffect(() => {
    let alive = true;
    fetchCourses("all").then(rows => {
      if (alive) {
        setCourses(rows);
        setCourse(value => value || rows[0]?.id || "");
      }
    }).catch(err => setError(err));
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    let alive = true;
    setGroups([]);
    setGroup("");
    setSessionId("");
    if (course) fetchGroups(course).then(rows => {
      if (alive) {
        setGroups(rows);
        setGroup(rows[0] || "");
      }
    }).catch(err => {
      if (alive) setError(err);
    });
    return () => {
      alive = false;
    };
  }, [course]);
  useEffect(() => {
    if (!filtered.some(s => s.session_id === sessionId)) {
      const now = Date.now() / 1000;
      setSessionId((filtered.find(s => s.starts_at <= now && s.ends_at > now && s.enrollment_status !== "cancelled") || filtered.find(s => s.starts_at > now && s.enrollment_status !== "cancelled") || filtered.at(-1))?.session_id || "");
    }
  }, [course, group, sessions.data, sessionId]);
  useEffect(() => {
    if (tab !== "schedule" && group === ALL_GROUPS) {
      setGroup(selectedGroup || groups[0] || "");
    }
  }, [tab, group, selectedGroup, groups]);
  async function perform(task) {
    setBusy(true);
    setError("");
    try {
      const response = await task();
      roster.refresh();
      sessions.refresh();
      return response;
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  const action = payload => perform(() => queueRequest(`${path}/actions`, {
    method: "POST",
    body: {
      session_id: sessionId,
      request_id: crypto.randomUUID(),
      ...payload
    }
  }));
  const data = roster.data;
  return <main className="queue-surface queue-admin" style={queueAppearance}>
    <div className="queue-topbar"><a className="queue-back" href="/admin/courses">{q("← Предметы")}</a><QueueLanguage /></div>
    <header className="queue-page-heading"><h1>{q("Защита лабораторных")}</h1>
    <p className="queue-muted">{q("Полный состав занятия, результаты и расписание.")}</p></header>
    <div className="queue-toolbar queue-filters">
      <label>{q("Предмет")}<select disabled={busy} value={course} onChange={e => setCourse(e.target.value)}>{courses.map(c => <option key={c.id} value={c.id}>{c.name || c.id}</option>)}</select></label>
      <label>{q("Группа")}<select disabled={busy} value={group} onChange={e => {
          setGroup(e.target.value);
          setSessionId("");
        }}>{tab === "schedule" && <option value={ALL_GROUPS}>{q("Все")}</option>}{groups.map(g => <option key={g}>{g}</option>)}</select></label>
      <label>{q("Занятие")}<select disabled={busy} value={sessionId} onChange={e => setSessionId(e.target.value)}><option value="">{q("Выберите занятие")}</option>{filtered.map(s => <option key={s.session_id} value={s.session_id}>{group === ALL_GROUPS && `${s.group_id} · `}{displayDate(s.starts_at, s.timezone)}{s.enrollment_status === "cancelled" ? q(" · отменено") : ""}</option>)}</select></label>
    </div>
    <nav className="queue-tabs" aria-label={q("Разделы панели")}>{[["queue", q("Очередь")], ["schedule", q("Расписание")], ["settings", q("Дедлайны и порядок")], ["preferences", q("Настройки")]].map(([key, label]) => <button key={key} disabled={busy} aria-current={tab === key ? "page" : undefined} className={tab === key ? "primary" : ""} onClick={() => setTab(key)}>{label}</button>)}</nav>
    <ErrorNotice error={error || sessions.error || roster.error} />
    <div className={`queue-feedback ${busy ? "is-saving" : ""}`} role="status" aria-live="polite" aria-atomic="true"><span className="queue-spinner" aria-hidden="true" />{busy ? q("Сохраняется…") : ""}</div>
    <div className="queue-content" key={tab} aria-busy={busy}>
    {tab === "queue" && <>
      {!sessionId && <p className="queue-notice">{q("Назначьте занятие во вкладке «Расписание».")}</p>}
      {sessionId && !data && !roster.error && <p role="status">{q("Загружается очередь…")}</p>}
      {data && <>
        <div className="queue-session-heading"><h2>{displayDate(data.session.starts_at, data.session.timezone)} <span className="queue-group-badge">{data.session.group_id}</span></h2>
        <p>{data.mode === "fifo" ? q("FIFO — по времени записи") : data.mode === "random" ? q("Случайный порядок") : q("Приоритет по дедлайнам, от −1")}. {data.attendance_open ? q(data.continuous_enrollment ? "Участие можно подтвердить или отменить до конца занятия." : "Участие можно подтвердить или отменить до начала занятия.") : q("Состав зафиксирован; изменения доступны преподавателю.")}</p></div>
        {data.stale && <p className="queue-notice">{q("Таблица недоступна. Показан сохранённый состав, обновление допуска ожидается.")}</p>}
        {Date.now() / 1000 < data.session.starts_at && data.session.enrollment_status !== "cancelled" && <button disabled={busy} onClick={() => action({
            action: data.session.enrollment_status === "closed" ? "open_enrollment" : "close_enrollment"
          })}>{data.session.enrollment_status === "closed" ? q("Открыть запись") : q("Закрыть запись досрочно")}</button>}
        <h3><Hint text="Счётчики относятся к этому занятию. Результаты учитываются по попыткам защиты.">{q("Итоги выбранного занятия")}</Hint></h3>
        <dl className={`queue-summary ${Date.now() / 1000 < data.session.starts_at ? "is-inactive" : ""}`} aria-disabled={Date.now() / 1000 < data.session.starts_at}><div><dt><Hint text="Число разных студентов, подтвердивших участие в выбранном занятии, включая уже завершивших защиту.">{q("Подтвердили участие")}</Hint></dt><dd>{new Set(data.entries.filter(row => row.will_attend).map(row => row.github.toLowerCase())).size}</dd></div>{[["called", q("Сдают сейчас")], ["waiting", q("Ждут")], ["defended", q("Сдано")], ["failed", q("Неудачных попыток")], ["no_show", q("Неявок")]].map(([key, label]) => <div key={key}><dt><Hint text={statHelp[key]}>{label}</Hint></dt><dd>{data.stats[key]}</dd></div>)}</dl>
        {Date.now() / 1000 < data.session.starts_at && <p className="queue-muted">{q("До начала занятия статистика не учитывается. Пока показан предварительный состав.")}</p>}
        <RoundQueue data={data} action={action} busy={busy} />
        <details><summary>{q("История и попытки")}</summary>
          <ul className="queue-history">{data.history.map(e => <li key={e.event_id}>{displayDate(e.occurred_at, data.session.timezone)} · {q(eventNames[e.kind] || e.kind)}{e.full_name ? q(" · {{v0}} · работа {{v1}}", {
                  v0: e.full_name,
                  v1: e.lab_key
                }) : ""}{e.kind === "result_corrected" && <span> · {q({defended:"Сдал",failed:"Не сдал",no_show:"Не пришёл"}[e.previous_outcome] || "")} → {q({defended:"Сдал",failed:"Не сдал",no_show:"Не пришёл"}[e.outcome] || "")}</span>}{e.attempt_number ? q(" · попытка {{v0}}", {
                  v0: e.attempt_number
                }) : ""}</li>)}</ul>
          <p>{q("Сверка с таблицей:")} {data.reconciliation?.done_at ? displayDate(data.reconciliation.done_at, data.session.timezone) : data.reconciliation?.error ? q("ожидается повтор после ошибки чтения") : data.reconciliation?.checked_at ? q("Ранняя сверка: {{early}}; ночная: {{night}}", { early: displayDate(data.reconciliation.checked_at, data.session.timezone), night: displayDate(data.reconciliation.due_at, data.session.timezone) }) : q("через 10 минут после окончания и в полночь")}.</p>
        </details>
      </>}
    </>}
    {tab === "preferences" && course && <QueuePreferences key={course} course={course} />}
    {tab === "schedule" && <Schedule course={course} group={group} sessions={sessions.data || []} perform={perform} busy={busy} version={settingsVersion} onSaved={() => setSettingsVersion(v => v + 1)} />}
    {tab === "settings" && course && (!group || group === ALL_GROUPS ? <p className="queue-notice">{q("Выберите конкретную группу, чтобы посмотреть её дедлайны и настройки.")}</p> : <Settings key={`${course}:${group}`} course={course} group={group} perform={perform} busy={busy} />)}
    </div>
  </main>;
}
function Settings({
  course,
  group,
  perform,
  busy
}) {
  const { displayDate } = useQueueLanguage();
  const settingsPath = `${coursePath(course)}/settings${group ? `?group_id=${encodeURIComponent(group)}` : ""}`;
  const modeState = usePolling(settingsPath);
  const modeLocked = modeState.data?.mode_locked;
  const [config, setConfig] = useState(null),
    [random, setRandom] = useState(false),
    [fifo, setFifo] = useState(false),
    [opensBeforeMinutes, setOpensBeforeMinutes] = useState(15),
    [deadlines, setDeadlines] = useState({}),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    queueRequest(settingsPath).then(data => {
      if (alive) {
        setConfig(data);
        setRandom(Boolean(data.queue.random));
        setFifo(Boolean(data.queue.fifo));
        setOpensBeforeMinutes(data.queue.opens_before_minutes ?? 15);
        setDeadlines(data.queue.deadlines || {});
      }
    }).catch(err => setError(err));
    return () => {
      alive = false;
    };
  }, [settingsPath]);
  if (error) return <p role="alert">{queueError(error)}</p>;
  if (!config) return <p role="status">{q("Загружаются настройки…")}</p>;
  const patch = (key, value, remove = false) => setDeadlines(old => {
    const next = {
      ...old
    };
    if (remove) delete next[key];else next[key] = value;
    return next;
  });
  return <form onSubmit={e => {
    e.preventDefault();
    perform(() => queueRequest(`${coursePath(course)}/settings`, {
      method: "PUT",
      body: {
        random,
        fifo,
        opens_before_minutes: opensBeforeMinutes,
        deadlines
      }
    }).then(() => modeState.refresh()));
  }}>
    <h2>{q("Дедлайны и порядок")}</h2>
    {modeState.error && <p role="alert">{queueError(modeState.error)}</p>}
    <div className="queue-actions">
    <label className="queue-checkbox"><input type="checkbox" checked={!fifo && (modeLocked ? true : random)} disabled={(!fifo && modeLocked) || modeLocked == null || busy} onChange={e => { setRandom(e.target.checked); if (e.target.checked) setFifo(false); }} /><Hint text="Без читаемых дедлайнов включён случайный порядок. При доступных дедлайнах работает очередь от −1; случайный порядок можно выбрать вручную.">{q("Случайный порядок")}</Hint></label>
    <label className="queue-checkbox"><input type="checkbox" checked={fifo} disabled={busy} onChange={e => { setFifo(e.target.checked); if (e.target.checked) setRandom(false); }} /><Hint text="Первым вызывается тот, кто раньше подтвердил приход. Запись открывается перед парой и продолжается до её конца.">{q("FIFO — по времени записи")}</Hint></label>
    </div>
    {fifo && <label>{q("Открывать запись за, минут")}<input type="number" min="0" max="1440" step="1" required value={opensBeforeMinutes} disabled={busy} onChange={e => setOpensBeforeMinutes(e.target.value === "" ? "" : Number(e.target.value))} /></label>}
    {modeLocked && !fifo && <p className="queue-notice">{q("Есть непрочитанный дедлайн без ручного значения: случайный режим обязателен. После восстановления данных автоматически включится приоритет, если случайный режим не выбран вручную.")}</p>}
    <p>{q("«Без дедлайна» означает отсутствие срока по правилам работы. «Из таблицы» требует читаемую дату; пустая ячейка не считается отсутствием срока.")}</p>
    <div className="queue-settings">{config.labs.map(lab => <fieldset key={lab.key}><legend>{lab.title}</legend>
      <label>{q("Источник")}<select value={!(lab.key in deadlines) ? "sheet" : deadlines[lab.key] === null ? "none" : "manual"} onChange={e => patch(lab.key, e.target.value === "none" ? null : "", e.target.value === "sheet")}><option value="sheet">{q("Из таблицы")}</option><option value="none">{q("Без дедлайна")}</option><option value="manual">{q("Вручную")}</option></select></label>
      {!(lab.key in deadlines) && <div className="queue-sheet-deadline" aria-live="polite">{(() => {
        const received = modeState.data?.sheet_deadlines?.[lab.key] || config.sheet_deadlines?.[lab.key];
        const zone = modeState.data?.timezone || config.timezone;
        if (!received || !received.available) return <small>{q("Дата не получена из таблицы")}{received?.raw ? `: ${received.raw}` : ""}</small>;
        return <small>{received.value != null ? displayDate(received.value, zone) : q("Без дедлайна")}</small>;
      })()}</div>}
      {lab.key in deadlines && deadlines[lab.key] !== null && <label>{q("Дата")}<input required placeholder={q("ГГГГ-ММ-ДД")} value={deadlines[lab.key]} onChange={e => patch(lab.key, e.target.value)} /></label>}
    </fieldset>)}</div><button className="primary" disabled={busy}>{q("Сохранить настройки")}</button>
  </form>;
}
function Schedule({
  course,
  group,
  sessions,
  perform,
  busy,
  version,
  onSaved
}) {
  const { displayDate } = useQueueLanguage();
  const [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [teacher, setTeacher] = useState("main");
  const [unmatchedGroups, setUnmatchedGroups] = useState([]);
  const [source, setSource] = useState({
      source: "manual",
      university: ""
    }),
    [error, setError] = useState("");
  const [edit, setEdit] = useState(null);
  const concreteGroup = group && group !== ALL_GROUPS;
  const visibleSessions = filterSessions(sessions, course, group);
  const visibleUnmatched = unmatchedGroups.filter(name => group === ALL_GROUPS || name === group);
  const path = concreteGroup ? queueGroupPath(course, group, true) : null;
  useEffect(() => { setEdit(null); }, [course, group]);
  useEffect(() => {
    let alive = true;
    if (course) queueRequest(`${coursePath(course)}/settings`).then(data => {
      if (alive) {
        setSource(data.queue.schedule?.[group] || { source: "manual", university: "" });
        setUnmatchedGroups(Object.entries(data.queue.schedule || {}).filter(([, value]) => value.match_status === "group_not_found").map(([name]) => name));
      }
    }).catch(err => setError(err));
    return () => {
      alive = false;
    };
  }, [course, group, version]);
  const field = (key, value) => setSource(old => ({
    ...old,
    [key]: value
  }));
  const iso = value => new Date(value).toISOString();
  const changeSession = (session, body) => perform(async () => {
    await queueRequest(`${queueGroupPath(session.course_id, session.group_id, true)}/sessions/${encodeURIComponent(session.session_id)}`, { method: "PATCH", body });
    setEdit(null);
  });
  return <>
    <h2>{q("Расписание преподавателя")}</h2>
    {visibleUnmatched.length > 0 && <p className="queue-notice">{q("Для групп {{groups}} не найдено расписание ГУАП. Выберите группу и добавьте занятие вручную или настройте её источник.", { groups: visibleUnmatched.join(", ") })}</p>}
    <p>{q("Расписание соответствует выбранным фильтрам. Время отображается в часовом поясе занятия. Ввод дат использует часовой пояс браузера (")} {Intl.DateTimeFormat().resolvedOptions().timeZone}).</p>
    <div className="queue-table-wrap"><table className="queue-table"><thead><tr><th>{q("Предмет / группа")}</th><th>{q("Начало")}</th><th>{q("Окончание")}</th><th>{q("Преподаватель / источник")}</th><th><Hint text="«Отменить» отменяет занятие для всей группы. «Восстановить» возвращает ошибочно отменённое занятие и сохранённые записи.">{q("Действия")}</Hint></th></tr></thead><tbody>
      {visibleSessions.map(session => <Fragment key={session.session_id}>
        <tr><td>{session.course_title}{session.university && <small><br />{session.university}</small>}<br />{session.group_id}</td>
          <td>{displayDate(session.starts_at, session.timezone)}</td><td>{displayDate(session.ends_at, session.timezone)}{session.enrollment_status === "cancelled" && q(" · отменено")}</td>
          <td>{session.teacher_key}<br />{session.source_kind === "university" ? q("Вуз") : q("Вручную")}</td>
          <td><div className="queue-actions">
            <Hint control text="Меняет время занятия, сохраняя его список. Новое начало должно быть в будущем; продолжительность — 90 минут."><button disabled={busy || session.can_move === false || session.enrollment_status === "cancelled"}
              title={session.service_started ? q("Защиты уже начались. Для другого времени добавьте новое занятие.") : undefined}
              aria-expanded={edit?.session_id === session.session_id} aria-controls={`move-${session.session_id}`}
              onClick={() => setEdit(edit?.session_id === session.session_id ? null : session)}>{q("Перенести")}</button></Hint>
            {session.enrollment_status === "cancelled"
              ? <button title={q("«Отменить» отменяет занятие для всей группы. «Восстановить» возвращает ошибочно отменённое занятие и сохранённые записи.")} disabled={busy || session.can_restore === false} onClick={() => changeSession(session, { restore: true })}>{q("Восстановить")}</button>
              : <button title={q("«Отменить» отменяет занятие для всей группы. «Восстановить» возвращает ошибочно отменённое занятие и сохранённые записи.")} disabled={busy || session.can_cancel === false} onClick={() => changeSession(session, { cancel: true })}>{q("Отменить")}</button>}
          </div>{Boolean(session.service_started) && <small>{q("Защиты уже начались. Для другого времени добавьте новое занятие.")}</small>}</td>
        </tr>
        {edit?.session_id === session.session_id && session.enrollment_status !== "cancelled" && session.can_move !== false && <tr><td colSpan={5}><SessionMoveForm key={session.session_id} session={session} busy={busy} perform={perform} onClose={() => setEdit(null)} /></td></tr>}
      </Fragment>)}
    </tbody></table></div>
    {!visibleSessions.length && <p className="queue-notice">{q("По выбранным фильтрам занятий нет.")}</p>}
    {!concreteGroup && <p className="queue-notice">{q("Чтобы добавить занятие или изменить источник расписания, выберите конкретную группу.")}</p>}
    {course && concreteGroup && <>
      <form onSubmit={e => {
        e.preventDefault();
        perform(() => queueRequest(`${path}/sessions`, {
          method: "POST",
          body: {
            session_id: crypto.randomUUID(),
            teacher_key: teacher,
            starts_at: iso(start),
            ends_at: iso(end)
          }
        }));
      }}>
        <h3>{q("Добавить занятие ·")} {group}</h3><div className="queue-toolbar"><label>{q("Начало")}<input type="datetime-local" required value={start} onChange={e => setStart(e.target.value)} /></label><label>{q("Окончание")}<input type="datetime-local" required value={end} onChange={e => setEnd(e.target.value)} /></label><label>{q("Ключ преподавателя")}<input required value={teacher} onChange={e => setTeacher(e.target.value)} /></label><button disabled={busy}>{q("Добавить")}</button></div>
      </form>
      <form onSubmit={e => {
        e.preventDefault();
        perform(async () => {
          await queueRequest(`${path}/schedule`, {
            method: "PUT",
            body: {
              config: source
            }
          });
          await queueRequest(`${path}/schedule/refresh`, {
            method: "POST"
          });
          onSaved();
        });
      }}>
        <h3>{q("Источник расписания ·")} {group}</h3>{error && <p role="alert">{queueError(error)}</p>}<div className="queue-settings">
          <label>{q("Вуз")}<input value={source.university || ""} onChange={e => field("university", e.target.value)} placeholder={q("Название вуза")} /></label>
          <label>{q("Источник")}<select value={source.source} onChange={e => {
              field("source", e.target.value);
              setError("");
            }}>{!["manual", "suai", "import"].includes(source.source) && <option value={source.source}>{source.source}</option>}<option value="manual">{q("Вручную")}</option><option value="suai">{q("ГУАП")}</option><option value="import">{q("Импорт другого вуза")}</option></select></label>
          {source.source === "suai" && <>
            <label>{q("ID группы на сайте")}<input required value={source.group_id || ""} onChange={e => field("group_id", e.target.value)} /></label>
            <label>{q("Точное название предмета")}<input required value={source.subject || ""} onChange={e => field("subject", e.target.value)} /></label>
            <label>{q("Вид занятия (например ЛР)")}<input value={source.kind || ""} onChange={e => field("kind", e.target.value)} /></label>
            <label>{q("ID преподавателя (необязательно)")}<input value={source.teacher_id || ""} onChange={e => field("teacher_id", e.target.value)} /></label>
            <label>{q("Ключ преподавателя")}<input value={source.teacher_key || "main"} onChange={e => field("teacher_key", e.target.value)} /></label>
            <label>{q("Понедельник нечётной недели")}<input type="date" required value={source.week_anchor || ""} onChange={e => field("week_anchor", e.target.value)} /></label>
            <label>{q("Конец семестра")}<input type="date" value={source.semester_end || ""} onChange={e => field("semester_end", e.target.value)} /></label>
          </>}
        </div>
        {source.source === "import" && <label>{q("Занятия JSON (key, starts_at, ends_at, teacher_key)")}<textarea rows={8} defaultValue={JSON.stringify(source.lessons || [], null, 2)} key={`${course}:${group}:${version}:${source.source}`} onChange={e => {
            try {
              const rows = JSON.parse(e.target.value);
              if (!Array.isArray(rows)) throw Error();
              field("lessons", rows);
              setError("");
            } catch {
              setError(q("Нужен корректный JSON-массив занятий"));
            }
          }} /></label>}
        <button disabled={busy || Boolean(error)}>{q("Сохранить и обновить расписание")}</button>
      </form>
    </>}
  </>;
}


function SessionMoveForm({ session, busy, perform, onClose }) {
  useQueueLanguage();
  const [start, setStart] = useState(() => localDateTime(session.starts_at));
  const [error, setError] = useState(null);
  const input = useRef(null);
  useEffect(() => {
    input.current?.focus({ preventScroll: true });
    input.current?.closest("tr")?.scrollIntoView?.({ block: "nearest", behavior: "auto" });
  }, []);
  return <form id={`move-${session.session_id}`} className="queue-notice queue-move-form" onSubmit={event => {
    event.preventDefault();
    setError(null);
    if (new Date(start).getTime() <= Date.now()) { setError("Перенести занятие можно только на будущее время"); return; }
    perform(async () => {
      try {
        await queueRequest(`${queueGroupPath(session.course_id, session.group_id, true)}/sessions/${encodeURIComponent(session.session_id)}`, {
          method:"PATCH", body:{ starts_at:new Date(start).toISOString() }
        });
        onClose();
      } catch (failure) { setError(failure); throw failure; }
    });
  }}>
    <h3>{q("Перенос занятия ·")} {session.group_id}</h3>
    <div className="queue-toolbar"><label>{q("Новое начало")}<input ref={input} name="starts_at" type="datetime-local" min={localDateTime(Date.now() / 1000)} required value={start} onChange={event => setStart(event.target.value)} /></label>
      <span className="queue-muted">{q("Продолжительность — 90 минут. Окончание рассчитывается автоматически.")}</span>
      <button disabled={busy}>{q("Сохранить перенос")}</button><button disabled={busy} type="button" onClick={onClose}>{q("Закрыть")}</button></div>
    {error && <p role="alert" className="queue-error">{queueError(error)}</p>}
  </form>;
}
