import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button as MuiButton,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Snackbar,
  Tab,
  Tabs,
} from "@mui/material";

import { createQueueApi } from "../../../api/queue";
import {
  createLatestGate,
  formatDay,
  formatInCourseTz,
  groupsLabel,
  nextPollDelay,
  sessionTimeRange,
  sessionTitle,
  signupText,
  statusKey,
} from "../../queue/state";
import { usePolling } from "../../queue/usePolling";
import { HistoryPanel } from "./HistoryPanel";
import { ManualSessionDialog } from "./ManualSessionDialog";
import { OutcomeDialog } from "./OutcomeDialog";
import { SessionPanel } from "./SessionPanel";
import {
  BackButton,
  Container,
  HeaderRow,
  HintText,
  PageTitle,
  Panel,
  SectionHeading,
  SessionTile,
  SessionTiles,
  TileMeta,
  TileTitle,
  touchDialogSx,
} from "./styled";
import {
  SIGNUP_COLOR,
  adminErrorKeys,
  adminLoginPath,
  adminQueuePath,
  canOpenManual as queueAcceptsManual,
  isDayOver,
  pickSessionId,
  queueNotice,
  runAdminAction,
  sessionSummary,
  splitEntries,
  tileResults,
} from "./state";

// Админка ходит относительными путями /api/v1/admin/..., как LabList:
// их переписывают и прокси Vite, и Caddy в проде.
const api = createQueueApi({ baseUrl: "/api/v1" });

const SCHEDULE_MOMENT = { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
// Коды ScheduleStatus.error: для них есть перевод adminQueue.schedule.errors.<CODE>.
const KNOWN_SCHEDULE_ERRORS = ["FETCH_FAILED", "BAD_PAGE", "NO_LESSONS", "SNAPSHOT_TOO_OLD"];


// Страница очереди курса для преподавателя: /admin/courses/:courseId/queue.
export function AdminQueuePage() {
  const { courseId } = useParams();
  return <AdminQueue key={courseId} courseId={courseId} />;
}


function AdminQueue({ courseId }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const navigate = useNavigate();

  const [gate] = useState(createLatestGate);
  const failuresRef = useRef(0);
  const busyRef = useRef(false);
  const groupsRequestedRef = useRef(false);

  const [data, setData] = useState(null);
  const [pollError, setPollError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [tab, setTab] = useState("today");
  const [historyOpened, setHistoryOpened] = useState(false);
  const [busy, setBusy] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: "", severity: "info" });
  const [finishTarget, setFinishTarget] = useState(null);
  const [fixTarget, setFixTarget] = useState(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [groups, setGroups] = useState(null);
  const [groupsError, setGroupsError] = useState(null);
  const [groupsLoading, setGroupsLoading] = useState(false);

  const showSnackbar = useCallback(
    (message, severity = "info") => setSnackbar({ open: true, message, severity }),
    []
  );

  // Сессия админа живёт час, опрос продлевает её (_renew_admin_session). Если
  // она всё же кончилась - вход и сразу назад сюда. Адрес возврата строится от
  // курса, а не от адресной строки: иначе второй 401 подряд вложил бы
  // /admin?next=... сам в себя.
  const goToLogin = useCallback(() => {
    navigate(adminLoginPath(adminQueuePath(courseId)), { replace: true });
  }, [navigate, courseId]);

  const poll = useCallback(async () => {
    const ticket = gate.begin();
    try {
      const next = await api.getDay(courseId);
      if (gate.accept(ticket) && next && Array.isArray(next.sessions)) setData(next);
      failuresRef.current = 0;
      setPollError(null);
      return nextPollDelay({ admin: true });
    } catch (error) {
      if (error.status === 401) {
        goToLogin();
        return null;
      }
      const code = error.code || "unknown";
      failuresRef.current += 1;
      setPollError(code);
      if (code === "not_found" || code === "COURSE_NOT_FOUND") return null;
      return nextPollDelay({
        admin: true,
        failures: failuresRef.current,
        rateLimited: code === "rate_limit",
      });
    }
  }, [courseId, gate, goToLogin]);

  const refresh = usePolling(poll);

  const ensureGroups = useCallback(() => {
    if (groupsRequestedRef.current) return;
    groupsRequestedRef.current = true;
    setGroupsLoading(true);
    api
      .getGroups(courseId)
      .then((list) => {
        setGroups(Array.isArray(list) ? list : []);
        setGroupsError(null);
      })
      .catch((error) => {
        groupsRequestedRef.current = false;
        setGroupsError(error.code || "unknown");
      })
      .finally(() => setGroupsLoading(false));
  }, [courseId]);

  // Одно действие преподавателя (runAdminAction); повторное нажатие, пока
  // запрос в пути, игнорируется. useCallback - ради memo в HistoryPanel.
  const runAction = useCallback(async (action, successMessage) => {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    const ticket = gate.begin();
    try {
      return await runAdminAction(action, {
        onSuccess: (result) => {
          if (gate.accept(ticket) && result && Array.isArray(result.sessions)) setData(result);
          if (successMessage) showSnackbar(successMessage, "success");
        },
        onError: (error) => {
          gate.accept(ticket);
          const severity = error.code === "QUEUE_CHANGED" ? "warning" : "error";
          showSnackbar(t(adminErrorKeys(error.code)), severity);
        },
        onUnauthorized: () => {
          gate.accept(ticket);
          goToLogin();
        },
        refresh,
      });
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [gate, goToLogin, refresh, showSnackbar, t]);

  const handleCallNext = (session, head) =>
    runAction(
      () => api.callNext(courseId, session.id, head ? head.id : null),
      head ? t("adminQueue.toast.called", { name: head.name }) : null
    );

  const handleOutcome = async (entry, outcome) => {
    const result = await runAction(
      () => api.setOutcome(courseId, entry.id, outcome),
      t("adminQueue.toast.outcome", { name: entry.name, status: t(statusKey(outcome)) })
    );
    if (result) setFixTarget(null);
  };

  // Исход из истории: день записи мог закончиться, экран «Сегодня» её уже
  // не показывает. Ответ (null при ошибке) HistoryPanel использует сам.
  const handleHistoryOutcome = useCallback(
    (entry, outcome) =>
      runAction(
        () => api.setOutcome(courseId, entry.id, outcome),
        t("adminQueue.toast.outcome", { name: entry.name, status: t(statusKey(outcome)) })
      ),
    [courseId, runAction, t]
  );

  const handleReturn = async (entry) => {
    const result = await runAction(
      () => api.returnToQueue(courseId, entry.id),
      t("adminQueue.toast.returned", { name: entry.name })
    );
    if (result) setFixTarget(null);
  };

  const handleFinish = async () => {
    const session = finishTarget;
    const result = await runAction(
      () => api.finishSession(courseId, session.id),
      t("adminQueue.toast.finished")
    );
    if (result) setFinishTarget(null);
  };

  const handleManual = async ({ groups: chosen, closesAt }) => {
    const result = await runAction(
      () => api.openManual(courseId, { groups: chosen, closesAt }),
      t("adminQueue.toast.manualOpened")
    );
    if (!result) return;
    setManualOpen(false);
    // Ответ - состояние дня и открытая сессия: она и становится выбранной.
    if (result.session) setSelectedId(result.session.id);
  };

  const openManual = () => {
    ensureGroups();
    setManualOpen(true);
  };

  const changeTab = (_event, value) => {
    setTab(value);
    if (value === "history") setHistoryOpened(true);
  };

  const sessions = data ? data.sessions : [];
  const currentId = pickSessionId(sessions, selectedId);
  const selected = sessions.find((s) => s.id === currentId) || null;
  const offset = data ? data.utc_offset_minutes : null;
  const schedule = data && data.schedule;
  const canOpenManual = queueAcceptsManual(data);
  const notice = queueNotice(data);

  const scheduleReason =
    schedule && KNOWN_SCHEDULE_ERRORS.includes(schedule.error)
      ? t(`adminQueue.schedule.errors.${schedule.error}`)
      : null;

  return (
    <Container>
      <Panel>
        <BackButton onClick={() => navigate("/admin/courses")}>{t("adminQueue.back")}</BackButton>
        <HeaderRow>
          <PageTitle>
            {data && data.course_name
              ? t("adminQueue.titleWithCourse", { course: data.course_name })
              : t("adminQueue.title")}
          </PageTitle>
          {data && data.day && <HintText>{t("adminQueue.today", { date: formatDay(data.day, lang) })}</HintText>}
        </HeaderRow>

        <Tabs value={tab} onChange={changeTab}>
          <Tab value="today" label={t("adminQueue.tabs.today")} />
          <Tab value="history" label={t("adminQueue.tabs.history")} />
        </Tabs>

        {tab === "today" && !data && !pollError && (
          <>
            <LinearProgress />
            <HintText>{t("adminQueue.loading")}</HintText>
          </>
        )}

        {tab === "today" && !data && pollError && (
          <Alert
            severity="error"
            action={
              <MuiButton color="inherit" size="small" onClick={() => refresh()}>
                {t("adminQueue.retry")}
              </MuiButton>
            }
          >
            {t(adminErrorKeys(pollError))}
          </Alert>
        )}

        {tab === "today" && data && (
          <>
            {pollError && (
              <Alert severity="warning">
                {t("adminQueue.connectionLost", { reason: t(adminErrorKeys(pollError)) })}
              </Alert>
            )}
            {data.config_error && (
              <Alert severity="error">{t("adminQueue.configError", { error: data.config_error })}</Alert>
            )}
            {notice === "archived" && (
              <Alert severity="info">{t("adminQueue.archived", { status: data.course_status })}</Alert>
            )}
            {notice === "disabled" && <Alert severity="info">{t("adminQueue.disabled")}</Alert>}
            {schedule && schedule.state === "stale" && (
              <Alert severity="warning">
                {t("adminQueue.schedule.stale", {
                  moment: formatInCourseTz(schedule.fetched_at, offset, lang, SCHEDULE_MOMENT) || "—",
                })}
                {scheduleReason && ` ${t("adminQueue.schedule.reason", { reason: scheduleReason })}`}
              </Alert>
            )}
            {schedule && schedule.state === "unavailable" && (
              <Alert severity="error">
                {t("adminQueue.schedule.unavailable")}
                {scheduleReason && ` ${t("adminQueue.schedule.reason", { reason: scheduleReason })}`}
              </Alert>
            )}

            <HeaderRow>
              <SectionHeading>{t("adminQueue.sessionsTitle")}</SectionHeading>
              {/* Очередь, которая записей не принимает, вручную не открыть:
                  почему - сказано выше. */}
              {canOpenManual && (
                <MuiButton variant="outlined" disabled={busy} onClick={openManual}>
                  {t("adminQueue.manual.button")}
                </MuiButton>
              )}
            </HeaderRow>

            {sessions.length === 0 && (
              <HintText>
                {canOpenManual ? t("adminQueue.noSessions") : t("adminQueue.noSessionsClosed")}
              </HintText>
            )}

            {/* Одно занятие выбирать не из чего: его карточка повторила бы
                шапку панели ниже и на телефоне отодвинула бы «Вызвать
                следующего» за край экрана. */}
            {sessions.length > 1 && (
              <SessionTiles>
                {sessions.map((session) => {
                  const { called, waiting } = splitEntries(session.entries);
                  const results = tileResults(sessionSummary(session.entries));
                  return (
                    <SessionTile
                      key={session.id}
                      type="button"
                      $selected={session.id === currentId}
                      aria-pressed={session.id === currentId}
                      onClick={() => setSelectedId(session.id)}
                    >
                      <TileTitle>{sessionTitle(session, t)}</TileTitle>
                      <TileMeta>
                        {groupsLabel(session, t)}
                        {session.kind !== "all_day" && ` · ${sessionTimeRange(session, offset, lang)}`}
                      </TileMeta>
                      <Chip
                        size="small"
                        color={SIGNUP_COLOR[session.signup] || "default"}
                        label={signupText(session, t, offset, lang)}
                      />
                      <TileMeta>
                        {t("adminQueue.tile.counts", { waiting: waiting.length, called: called.length })}
                      </TileMeta>
                      {results && <TileMeta>{t("adminQueue.tile.results", results)}</TileMeta>}
                    </SessionTile>
                  );
                })}
              </SessionTiles>
            )}

            {selected && (
              <SessionPanel
                session={selected}
                nowIso={data.now}
                offset={offset}
                lang={lang}
                busy={busy}
                onCallNext={handleCallNext}
                onOutcome={handleOutcome}
                onReturn={handleReturn}
                onClose={(session) =>
                  runAction(() => api.closeSignup(courseId, session.id), t("adminQueue.toast.closed"))
                }
                onReopen={(session) =>
                  runAction(() => api.reopenSignup(courseId, session.id), t("adminQueue.toast.reopened"))
                }
                onFinish={setFinishTarget}
                onFix={setFixTarget}
              />
            )}

            <HintText>
              {t("adminQueue.updatedAt", {
                time: formatInCourseTz(data.now, offset, lang, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                  hourCycle: "h23",
                }),
              })}
            </HintText>
          </>
        )}

        {/* История монтируется при первом открытии вкладки и дальше не
            размонтируется, чтобы не терять фильтры при переключении. */}
        {historyOpened && (
          <div hidden={tab !== "history"}>
            <HistoryPanel
              api={api}
              courseId={courseId}
              offset={offset}
              lang={lang}
              groups={groups}
              busy={busy}
              onNeedGroups={ensureGroups}
              onUnauthorized={goToLogin}
              onOutcome={handleHistoryOutcome}
            />
          </div>
        )}
      </Panel>

      {finishTarget && (
        <Dialog
          open
          onClose={busy ? undefined : () => setFinishTarget(null)}
          maxWidth="xs"
          fullWidth
          sx={touchDialogSx}
        >
          <DialogTitle>{t("adminQueue.session.finishTitle")}</DialogTitle>
          <DialogContent>
            {t("adminQueue.session.finishText", {
              count: splitEntries(finishTarget.entries).waiting.length,
            })}
          </DialogContent>
          <DialogActions>
            <MuiButton onClick={() => setFinishTarget(null)} disabled={busy}>
              {t("adminQueue.cancel")}
            </MuiButton>
            <MuiButton color="error" variant="contained" onClick={handleFinish} disabled={busy}>
              {t("adminQueue.session.finishConfirm")}
            </MuiButton>
          </DialogActions>
        </Dialog>
      )}

      {fixTarget && (
        <OutcomeDialog
          entry={fixTarget}
          dayOver={Boolean(selected && isDayOver(selected, data && data.now))}
          busy={busy}
          onOutcome={handleOutcome}
          onReturn={handleReturn}
          onClose={() => setFixTarget(null)}
        />
      )}

      {manualOpen && (
        <ManualSessionDialog
          groups={groups}
          groupsLoading={groupsLoading}
          groupsError={groupsError}
          busy={busy}
          onRetryGroups={ensureGroups}
          onClose={() => setManualOpen(false)}
          onSubmit={handleManual}
        />
      )}

      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          severity={snackbar.severity}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
