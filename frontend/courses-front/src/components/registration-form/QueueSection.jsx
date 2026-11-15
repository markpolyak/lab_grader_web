import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItem,
  ListItemText,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import {
  fetchDefenseQueue,
  getQueueOAuthStartUrl,
  registerInQueue,
  shiftInQueue,
  withdrawFromQueue,
} from "../../api";
import { alpha } from "@mui/material/styles";

const STATUS_CALLED = "вызван";

const STATUS_BG = {
  "сдал": (theme) => alpha(theme.palette.success.main, 0.1),
  "не_сдал": (theme) => alpha(theme.palette.error.main, 0.08),
  "нет": (theme) => alpha(theme.palette.text.primary, 0.06),
  "отозван": (theme) => alpha(theme.palette.text.primary, 0.06),
  "не_дошло": (theme) => alpha(theme.palette.text.primary, 0.06),
  [STATUS_CALLED]: (theme) => alpha(theme.palette.info.main, 0.14),
};

const normalizeQueue = (data) => {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.queue)) return data.queue;
  if (Array.isArray(data?.entries)) return data.entries;
  if (Array.isArray(data?.rows)) return data.rows;
  return [];
};

const PENDING_ACTION_KEY = "queue_pending_action";
const PENDING_DELTA_KEY = "queue_pending_delta";

const readPendingAction = () => {
  try {
    return window.sessionStorage.getItem(PENDING_ACTION_KEY);
  } catch {
    return null;
  }
};

const writePendingAction = (action, delta) => {
  try {
    window.sessionStorage.setItem(PENDING_ACTION_KEY, action);
    if (delta != null) {
      window.sessionStorage.setItem(PENDING_DELTA_KEY, String(delta));
    } else {
      window.sessionStorage.removeItem(PENDING_DELTA_KEY);
    }
  } catch {
    // приватный режим - намерение просто не переживёт OAuth-редирект
  }
};

const clearPendingAction = () => {
  try {
    window.sessionStorage.removeItem(PENDING_ACTION_KEY);
    window.sessionStorage.removeItem(PENDING_DELTA_KEY);
  } catch {
    // noop
  }
};

const formatSessionMoment = (value, language) => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
};

const sessionLabel = (session, language, t) => {
  if (!session) return t("queue.noNextSession");
  const start = formatSessionMoment(session.starts_at, language);
  const end = formatSessionMoment(session.ends_at, language);
  const timeRange = start && end ? `${start} – ${end}` : start || end;
  return [timeRange, session.room].filter(Boolean).join(" · ") || t("queue.noNextSession");
};

const entryName = (entry) =>
  entry.team_title ||
  entry.team?.title ||
  entry.fio ||
  entry.student_name ||
  entry.name ||
  entry.title ||
  "";

const entryMeta = (entry, t) => {
  const github = entry.github ? String(entry.github).trim() : "";
  const signupTime = entry.signup_time ? String(entry.signup_time).trim() : "";
  if (!github && !signupTime) return null;
  const parts = [];
  if (github) parts.push(`${t("queue.githubLabel")}: ${github}`);
  if (signupTime) parts.push(`${t("queue.signupTimeLabel")}: ${signupTime}`);
  return parts.join(" · ");
};

export function QueueSection({ courseId, groupId, labId, formData }) {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [shiftBy, setShiftBy] = useState("1");

  // Guard против out-of-order ответов (ревью: запоздавший ответ не
  // перезаписывает свежее состояние).
  const requestSeq = useRef(0);
  const isMountedRef = useRef(false);

  // Личность студента: та же сборка ФИО, что и в /register
  // ({surname} {name} {patronymic}.strip()) — сервер сверяет её с ФИО
  // строки, привязанной к подтверждённому GitHub-логину.
  const studentName = useMemo(() => {
    if (!formData) return "";
    const patronymic = (formData.patronymic || "").trim();
    const base = `${(formData.surname || "").trim()} ${(formData.name || "").trim()}`.trim();
    return `${base}${patronymic ? ` ${patronymic}` : ""}`.trim();
  }, [formData]);
  const formReady = Boolean(
    formData && formData.surname && formData.name && formData.github && studentName
  );

  // П.14: один GET /queue возвращает и список, и персональное состояние
  // (me по student_name/student_group) — отдельный GET /queue/status больше
  // не нужен. Identity per-request через OAuth code (OAuth plan §3/§4).
  const loadQueue = useCallback(
    async ({ showLoading = false } = {}) => {
      const seq = ++requestSeq.current;
      if (showLoading) {
        setLoading(true);
      }
      setError(null);
      try {
        const identity = formReady
          ? { studentName, studentGroup: groupId }
          : {};
        const resp = await fetchDefenseQueue(courseId, groupId, labId, identity);
        if (seq !== requestSeq.current) return; // stale response - discard
        setData(resp);
      } catch (requestError) {
        if (seq !== requestSeq.current) return; // stale failure - discard
        setData(null);
        setError(requestError);
      } finally {
        if (seq === requestSeq.current && isMountedRef.current) {
          setLoading(false);
        }
      }
    },
    [courseId, groupId, labId, formReady, studentName]
  );

  const reload = useCallback(() => loadQueue({ showLoading: false }), [loadQueue]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    loadQueue({ showLoading: true });
  }, [loadQueue]);

  // П.14: поллинг 15 c, пауза на скрытой вкладке, обновление при возврате.
  useEffect(() => {
    const intervalId = setInterval(() => {
      if (!document.hidden) {
        loadQueue();
      }
    }, 15000);
    const onVisibilityChange = () => {
      if (!document.hidden) {
        loadQueue();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      clearInterval(intervalId);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [loadQueue]);

  // OAuth plan §3: любое мутационное действие начинается с полного
  // redirect'а на GitHub. Намерение сохраняется в sessionStorage; после
  // возврата с ?queue_code=... код обменивается на мутацию одним POST'ом.
  const startQueueAction = (action, delta) => {
    writePendingAction(action, delta);
    window.location.href = getQueueOAuthStartUrl(courseId, groupId, labId);
  };

  // Возврат с ?queue_code=...: выполняем отложенное действие.
  const pendingCode = useMemo(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("queue_code");
  }, []);
  const pendingAction = readPendingAction();

  const performPendingMutation = useCallback(
    async (code) => {
      const action = readPendingAction();
      clearPendingAction();
      if (!action) return;

      let delta = NaN;
      try {
        delta = Number(window.sessionStorage.getItem("queue_pending_delta"));
      } catch {
        delta = NaN;
      }

      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        if (action === "register") {
          await registerInQueue({
            courseId, groupId, labId, code, studentName, studentGroup: groupId,
          });
          setNotice(t("queue.registered"));
        } else if (action === "withdraw") {
          await withdrawFromQueue({
            courseId, groupId, labId, code, studentName, studentGroup: groupId,
          });
          setNotice(t("queue.withdrawn"));
        } else if (action === "shift") {
          await shiftInQueue({
            courseId, groupId, labId, code, studentName, studentGroup: groupId,
            delta: Number.isFinite(delta) ? delta : 1,
          });
          setNotice(t("queue.shifted"));
        }
        await loadQueue({ showLoading: false });
      } catch (requestError) {
        setError(requestError);
      } finally {
        setBusy(false);
        // Убираем queue_code из адресной строки, чтобы повторное открытие
        // страницы не пыталось выполнить мутацию тем же одноразовым кодом.
        try {
          const url = new URL(window.location.href);
          url.searchParams.delete("queue_code");
          window.history.replaceState({}, "", url.toString());
        } catch {
          // noop
        }
      }
    },
    [courseId, groupId, labId, studentName, loadQueue, t]
  );

  useEffect(() => {
    if (!pendingCode) return;
    performPendingMutation(pendingCode);
    // однократно на появление queue_code
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingCode]);

  // Производные состояния из одного ответа /queue (П.14).
  const me = data?.me ?? null;
  const sessionRequired = me === null;
  const inQueue = me?.in_queue === true;
  const called = me?.called === true;
  const position = me?.position ?? null;
  const queueSize = me?.queue_size ?? null;
  const alreadyPassed = false; // проверяется при регистрации (register -> ALREADY_PASSED)
  const session = data?.session ?? null;
  const nextSession = data?.next_session ?? null;
  const queueOpen = data?.queue_open === true;
  const queueRows = useMemo(() => data?.queue ?? [], [data]);
  const teamLab = data?.is_team_lab === true;
  const team = data?.team ?? null;

  const nextSessionLabel = useMemo(
    () => sessionLabel(nextSession, i18n.language, t),
    [i18n.language, nextSession, t]
  );
  const currentSessionLabel = useMemo(
    () => sessionLabel(session, i18n.language, t),
    [i18n.language, session, t]
  );

  const errorMessage = error
    ? error.code
      ? t(`queue.errors.${error.code}`, {
          defaultValue: error.message || t("queue.errors.generic"),
        })
      : error.message || t("queue.errors.generic")
    : null;

  const teamMembers = Array.isArray(team?.members) ? team.members : [];
  const validShift = /^[1-9]\d*$/.test(shiftBy) && Number.isSafeInteger(Number(shiftBy));


  return (
    <Paper component="section" variant="outlined" sx={{ mt: 3, p: { xs: 2, sm: 3 } }}>
      <Stack spacing={2}>
        <Box>
          <Typography variant="h6">{t("queue.title")}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t("queue.description")}
          </Typography>
        </Box>

        {loading && (
          <Stack direction="row" spacing={1} alignItems="center" role="status">
            <CircularProgress size={18} />
            <Typography variant="body2">{t("queue.loading")}</Typography>
          </Stack>
        )}

        {!loading && errorMessage && <Alert severity="error">{errorMessage}</Alert>}
        {notice && <Alert severity="success">{notice}</Alert>}

        {!loading && (
          <>
            {teamLab && team && (
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2">{t("queue.teamTitle")}</Typography>
                <Typography>{team.title || team.name || team.slug}</Typography>
                {teamMembers.length > 0 && (
                  <Typography variant="body2" color="text.secondary">
                    {t("queue.teamMembers")}:{" "}
                    {teamMembers
                      .map((member) =>
                        typeof member === "string"
                          ? member
                          : member.name || member.fio || member.username || member.github
                      )
                      .filter(Boolean)
                      .join(", ")}
                  </Typography>
                )}
              </Paper>
            )}

            {inQueue && called && (
              <Alert severity="info">{t("queue.calledBanner")}</Alert>
            )}

            {inQueue && !called && (
              <Alert severity="success">
                {t("queue.position", {
                  position: position,
                  total: queueSize ?? position,
                })}
              </Alert>
            )}

            {!inQueue && !called && !queueOpen && (
              <Alert severity="warning">
                {t("queue.closed", { nextSession: nextSessionLabel })}
              </Alert>
            )}

            {!inQueue && !called && queueOpen && (
              <Button
                variant="contained"
                color="primary"
                size="large"
                disabled={busy || !formReady}
                onClick={() => startQueueAction("register")}
              >
                {t(busy ? "queue.working" : "queue.register")}
              </Button>
            )}

            {inQueue && !called && (
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems="flex-start">
                <Button
                  variant="outlined"
                  color="error"
                  disabled={busy}
                  onClick={() => startQueueAction("withdraw")}
                >
                  {t(busy ? "queue.working" : "queue.withdraw")}
                </Button>
                <Stack direction="row" spacing={1} alignItems="center">
                  <TextField
                    type="number"
                    size="small"
                    label={t("queue.shiftBy")}
                    value={shiftBy}
                    onChange={(event) => setShiftBy(event.target.value)}
                    inputProps={{ min: 1, step: 1 }}
                    error={shiftBy !== "" && !validShift}
                    sx={{ width: 150 }}
                  />
                  <Button
                    variant="outlined"
                    disabled={busy || !validShift}
                    onClick={() => startQueueAction("shift", Number(shiftBy))}
                  >
                    {t(busy ? "queue.working" : "queue.shift")}
                  </Button>
                </Stack>
              </Stack>
            )}

            {session && (
              <Typography variant="body2" color="text.secondary">
                {t("queue.session")}: {currentSessionLabel}
              </Typography>
            )}

            <Divider />
            <Box>
              <Stack
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                sx={{ mb: 1 }}
              >
                <Typography variant="subtitle1">{t("queue.listTitle")}</Typography>
                <Button size="small" disabled={busy || loading} onClick={reload}>
                  {t("queue.refresh")}
                </Button>
              </Stack>
              {queueRows.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  {t("queue.empty")}
                </Typography>
              ) : (
                <List disablePadding>
                  {queueRows.map((entry, index) => {
                    const rowPosition = entry.position ?? index + 1;
                    return (
                      <ListItem
                        key={`${entry.fio}-${entry.lab}-${index}`}
                        divider={index < queueRows.length - 1}
                        disableGutters
                        sx={
                          STATUS_BG[entry.status]
                            ? { bgcolor: STATUS_BG[entry.status] }
                            : undefined
                        }
                        secondaryAction={
                          entry.status && (
                            <Chip
                              size="small"
                              label={t(`queue.statuses.${entry.status}`, {
                                defaultValue: entry.status,
                              })}
                            />
                          )
                        }
                      >
                        <ListItemText
                          primary={
                            <Stack direction="row" spacing={1} alignItems="center">
                              <Chip size="small" label={rowPosition} />
                              <Typography component="span" variant="body2">
                                {entryName(entry) || t("queue.unnamedEntry")}
                              </Typography>
                            </Stack>
                          }
                          secondary={entryMeta(entry, t) || undefined}
                          secondaryTypographyProps={{ variant: "caption", color: "text.secondary" }}
                        />
                      </ListItem>
                    );
                  })}
                </List>
              )}
            </Box>
          </>
        )}
      </Stack>
    </Paper>
  );
}
