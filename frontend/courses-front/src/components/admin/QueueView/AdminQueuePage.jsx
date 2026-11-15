import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Box,
  Button,
  Chip,
  Container,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  Toolbar,
  Tooltip,
  Typography,
} from "@mui/material";
import { fetchGroups, fetchQueueState, openQueue, closeQueue, autoQueue } from "../../../api";

const STATUS_CALLED = "вызван";
const STATUS_WAITING = "ждёт";

const STATUS_COLOR = {
  "ждёт": "info",
  "сдал": "success",
  "не_сдал": "error",
  "нет": "warning",
  "отозван": "default",
  "вызван": "secondary",
};

function ArrowBackIcon() {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true" focusable="false">
      <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2Z" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true" focusable="false">
      <path d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35Z" />
    </svg>
  );
}

export const AdminQueuePage = () => {
  const { t } = useTranslation();
  const { courseId } = useParams();
  const navigate = useNavigate();

  const [queue, setQueue] = useState([]);
  const [date, setDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Manual queue override (issue #10): per-group open/close/auto state.
  const [groups, setGroups] = useState([]);
  const [overrideMap, setOverrideMap] = useState({});
  const [overrideBusy, setOverrideBusy] = useState(false);
  const [overrideError, setOverrideError] = useState(null);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/queue/today`,
        { credentials: "include" }
      );
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setQueue(data.queue || []);
      setDate(data.date || "");
    } catch (err) {
      setError(err.message || t("adminQueue.errors.loadFailed"));
      setQueue([]);
    } finally {
      setLoading(false);
    }
  }, [courseId, t]);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  // Admin action: POST to a queue admin endpoint, then reload the list.
  // Parallel calls are allowed: each call marks the first waiting entry
  // ("вызван") without touching previously called ones, so the teacher
  // can call the next participant before finishing the previous defense.
  const postAction = useCallback(
    async (path, body) => {
      setBusy(true);
      setError(null);
      try {
        const resp = await fetch(
          `/api/v1/admin/courses/${encodeURIComponent(courseId)}${path}`,
          {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }
        );
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          throw new Error(
            typeof data.detail === "string" ? data.detail : `HTTP ${resp.status}`
          );
        }
        await loadQueue();
      } catch (err) {
        setError(err.message || t("adminQueue.errors.loadFailed"));
      } finally {
        setBusy(false);
      }
    },
    [courseId, loadQueue, t]
  );

  const scopePath = (scope) =>
    `/groups/${encodeURIComponent(scope.group)}/labs/${encodeURIComponent(scope.lab)}`;

  const callNext = useCallback(
    (scope) => postAction(`${scopePath(scope)}/queue/call`, {}),
    [postAction]
  );

  const callEntry = useCallback(
    (entry) =>
      postAction(`${scopePath(entry)}/queue/call`, { identifier: entry.fio }),
    [postAction]
  );

  const uncallEntry = useCallback(
    (entry) =>
      postAction(`${scopePath(entry)}/queue/uncall`, { identifier: entry.fio }),
    [postAction]
  );

  const finishDefense = useCallback(
    (entry, status) =>
      postAction(`${scopePath(entry)}/queue/set-status`, {
        identifier: entry.fio,
        status,
      }),
    [postAction]
  );

  // --- Manual queue override (issue #10): per-group open/close/auto ---
  // The panel lists every group of the course (fetchGroups), not just those
  // with queue entries, so the teacher can open an unscheduled session even
  // when the queue is empty. fetchQueueState returns only groups with an
  // active override; groups absent from it are "auto" (schedule decides).
  const loadGroups = useCallback(() => {
    fetchGroups(courseId)
      .then((data) => setGroups(Array.isArray(data) ? data : []))
      .catch(() => setGroups([]));
  }, [courseId]);

  const loadOverrideState = useCallback(async () => {
    try {
      const data = await fetchQueueState(courseId);
      const map = {};
      for (const g of data?.groups || []) map[g.group] = g;
      setOverrideMap(map);
    } catch (err) {
      // Non-fatal: the panel falls back to "auto" for every group.
      setOverrideError(
        err.code === "SESSION_REQUIRED"
          ? t("adminQueue.errors.authRequired", { defaultValue: "Требуется вход администратора" })
          : err.message || t("adminQueue.errors.loadFailed")
      );
    }
  }, [courseId, t]);

  useEffect(() => {
    loadGroups();
  }, [loadGroups]);

  useEffect(() => {
    loadOverrideState();
  }, [loadOverrideState]);

  const postOverride = useCallback(
    async (groupId, action) => {
      setOverrideBusy(true);
      setOverrideError(null);
      try {
        if (action === "open") await openQueue(courseId, groupId);
        else if (action === "close") await closeQueue(courseId, groupId);
        else if (action === "auto") await autoQueue(courseId, groupId);
        await loadOverrideState();
        // An openness change affects the queue view too: the closed branch
        // stops creating today's sheet, the open branch starts it. Reload so
        // the teacher sees the new state right away.
        await loadQueue();
      } catch (err) {
        setOverrideError(
          err.code === "SESSION_REQUIRED"
            ? t("adminQueue.errors.authRequired", { defaultValue: "Требуется вход администратора" })
            : err.message || t("adminQueue.errors.loadFailed")
        );
      } finally {
        setOverrideBusy(false);
      }
    },
    [courseId, loadOverrideState, loadQueue, t]
  );

  const called = queue.filter((entry) => entry.status === STATUS_CALLED);

  // One pair = one queue (issue #62 review): scope sections by (group, pair),
  // not by lab. The lab of the first entry is only used to build the API path.
  const scopes = useMemo(() => {
    const seen = new Map();
    for (const entry of queue) {
      const key = `${entry.group}\u0000${entry.session_no ?? ""}`;
      if (!seen.has(key)) {
        seen.set(key, {
          group: entry.group,
          session_no: entry.session_no ?? null,
          lab: entry.lab,
        });
      }
    }
    return [...seen.values()];
  }, [queue]);

  return (
    <Container maxWidth="xl">
      <Toolbar />
      <Stack direction="row" spacing={2} alignItems="center" justifyContent="space-between" sx={{ mb: 2, flexWrap: "wrap" }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Tooltip title={t("back")}>
            <Button onClick={() => navigate(`/admin/courses`)}>
              <ArrowBackIcon />
            </Button>
          </Tooltip>
          <Typography variant="h5">
            {t("adminQueue.titleToday", { date })}
          </Typography>
        </Stack>
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={loadQueue}
          disabled={loading}
        >
          {t("adminQueue.refresh")}
        </Button>
      </Stack>

      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          {t("adminQueue.manage.title", { defaultValue: "Управление записью" })}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("adminQueue.manage.hint", {
            defaultValue:
              "Открыть запись на внештатное занятие или досрочно закрыть её, не удаляя ожидающих. Действует до конца дня, затем — снова по расписанию.",
          })}
        </Typography>
        {overrideError && (
          <Alert
            severity="error"
            onClose={() => setOverrideError(null)}
            sx={{ mb: 2 }}
          >
            {overrideError}
          </Alert>
        )}
        {groups.length === 0 ? (
          <Typography color="text.secondary">
            {t("adminQueue.manage.noGroups", { defaultValue: "Нет групп" })}
          </Typography>
        ) : (
          <Stack spacing={1}>
            {groups.map((g) => {
              const st = overrideMap[g]?.state || "auto";
              const chipColor =
                st === "open" ? "success" : st === "closed" ? "error" : "default";
              const chipLabel = t(`adminQueue.manage.state.${st}`, {
                defaultValue:
                  st === "open" ? "Открыто" : st === "closed" ? "Закрыто" : "Авто",
              });
              return (
                <Stack
                  key={g}
                  direction={{ xs: "column", sm: "row" }}
                  spacing={1}
                  alignItems={{ sm: "center" }}
                  justifyContent="space-between"
                  sx={{ flexWrap: "wrap" }}
                >
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography>{g}</Typography>
                    <Chip size="small" color={chipColor} label={chipLabel} />
                  </Stack>
                  <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
                    <Button
                      size="small"
                      variant="contained"
                      color="success"
                      disabled={overrideBusy || st === "open"}
                      onClick={() => postOverride(g, "open")}
                    >
                      {t("adminQueue.manage.open", { defaultValue: "Открыть" })}
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      color="error"
                      disabled={overrideBusy || st === "closed"}
                      onClick={() => postOverride(g, "close")}
                    >
                      {t("adminQueue.manage.close", { defaultValue: "Закрыть" })}
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={overrideBusy || st === "auto"}
                      onClick={() => postOverride(g, "auto")}
                    >
                      {t("adminQueue.manage.auto", { defaultValue: "Авто" })}
                    </Button>
                  </Stack>
                </Stack>
              );
            })}
          </Stack>
        )}
      </Paper>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Stack direction="row" spacing={1} alignItems="center">
          <CircularProgress size={24} />
          <Typography variant="body2">{t("adminQueue.loading")}</Typography>
        </Stack>
      ) : queue.length === 0 ? (
        <Alert severity="info">{t("adminQueue.empty")}</Alert>
      ) : (
        <>
          {called.length > 0 && (
            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
              <Typography variant="subtitle1" sx={{ mb: 1 }}>
                {t("adminQueue.calledTitle")}
              </Typography>
              <Stack spacing={1}>
                {called.map((entry, index) => (
                  <Stack
                    key={`${entry.fio}-${entry.lab}-${index}`}
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1}
                    alignItems={{ sm: "center" }}
                    justifyContent="space-between"
                    sx={{ flexWrap: "wrap" }}
                  >
                    <Typography>
                      {entry.fio} · {entry.group} · {entry.lab}
                    </Typography>
                    <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
                      <Button
                        size="small"
                        variant="contained"
                        color="success"
                        disabled={busy}
                        onClick={() => finishDefense(entry, "сдал")}
                      >
                        {t("queue.statuses.сдал")}
                      </Button>
                      <Button
                        size="small"
                        variant="outlined"
                        color="error"
                        disabled={busy}
                        onClick={() => finishDefense(entry, "не_сдал")}
                      >
                        {t("queue.statuses.не_сдал")}
                      </Button>
                      <Button
                        size="small"
                        variant="outlined"
                        color="warning"
                        disabled={busy}
                        onClick={() => finishDefense(entry, "нет")}
                      >
                        {t("queue.statuses.нет")}
                      </Button>
                      <Button
                        size="small"
                        disabled={busy}
                        onClick={() => uncallEntry(entry)}
                      >
                        {t("adminQueue.returnToQueue")}
                      </Button>
                    </Stack>
                  </Stack>
                ))}
              </Stack>
            </Paper>
          )}

          {scopes.map((scope) => {
            const entries = queue.filter(
              (e) =>
                e.group === scope.group &&
                (e.session_no ?? null) === scope.session_no
            );
            const hasWaiting = entries.some((e) => e.status === STATUS_WAITING);
            return (
              <Box key={`${scope.group}-${scope.session_no ?? "x"}`} sx={{ mb: 3 }}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                  sx={{ mb: 1, flexWrap: "wrap" }}
                >
                  <Typography variant="subtitle1">
                    {scope.group} · {t("adminQueue.pair", { no: scope.session_no ?? "—" })}
                  </Typography>
                  <Button
                    variant="contained"
                    disabled={busy || !hasWaiting}
                    onClick={() => callNext(scope)}
                  >
                    {t("adminQueue.callNext")}
                  </Button>
                </Stack>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>{t("adminQueue.columns.position")}</TableCell>
                      <TableCell>{t("adminQueue.columns.group")}</TableCell>
                      <TableCell>{t("adminQueue.columns.name")}</TableCell>
                      <TableCell>{t("adminQueue.columns.lab")}</TableCell>
                      <TableCell>{t("adminQueue.columns.github")}</TableCell>
                      <TableCell>{t("adminQueue.columns.time")}</TableCell>
                      <TableCell>{t("adminQueue.columns.status")}</TableCell>
                      <TableCell>{t("adminQueue.actions")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {entries.map((entry, index) => (
                      <TableRow key={`${entry.fio}-${entry.lab}-${index}`}>
                        <TableCell>{entry.position ?? "—"}</TableCell>
                        <TableCell>{entry.group}</TableCell>
                        <TableCell>{entry.fio}</TableCell>
                        <TableCell>{entry.lab}</TableCell>
                        <TableCell>{entry.github || "—"}</TableCell>
                        <TableCell>{entry.signup_time || "—"}</TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            color={STATUS_COLOR[entry.status] || "default"}
                            label={t(`queue.statuses.${entry.status}`, { defaultValue: entry.status })}
                          />
                        </TableCell>
                        <TableCell>
                          {entry.status === STATUS_WAITING && (
                            <Button
                              size="small"
                              disabled={busy}
                              onClick={() => callEntry(entry)}
                            >
                              {t("adminQueue.call")}
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            );
          })}
        </>
      )}
    </Container>
  );
};

export default AdminQueuePage;
