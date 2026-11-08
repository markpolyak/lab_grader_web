import { useCallback, useEffect, useMemo, useState } from "react";
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
  fetchDefenseQueueStatus,
  getJoinStartUrl,
  registerForDefenseQueue,
  shiftDefenseQueue,
  withdrawFromDefenseQueue,
} from "../../api";

const normalizeQueue = (data) => {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.queue)) return data.queue;
  if (Array.isArray(data?.entries)) return data.entries;
  if (Array.isArray(data?.rows)) return data.rows;
  const error = new Error("Queue endpoint returned an unexpected response");
  error.code = "INVALID_RESPONSE";
  throw error;
};

const validateStatus = (data) => {
  if (!data || typeof data !== "object" || typeof data.in_queue !== "boolean") {
    const error = new Error("Queue status endpoint returned an unexpected response");
    error.code = "INVALID_RESPONSE";
    throw error;
  }
  return data;
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

// eslint-disable-next-line react/prop-types
export function QueueSection({ courseId, groupId, labId }) {
  const { t, i18n } = useTranslation();
  const [status, setStatus] = useState(null);
  const [queue, setQueue] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [shiftBy, setShiftBy] = useState("1");

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [currentStatus, queueData] = await Promise.all([
        fetchDefenseQueueStatus(courseId, groupId, labId),
        fetchDefenseQueue(courseId, groupId, labId),
      ]);
      setStatus(validateStatus(currentStatus));
      setQueue(normalizeQueue(queueData));
    } catch (requestError) {
      setStatus(null);
      setQueue([]);
      setError(requestError);
    } finally {
      setLoading(false);
    }
  }, [courseId, groupId, labId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchDefenseQueueStatus(courseId, groupId, labId),
      fetchDefenseQueue(courseId, groupId, labId),
    ])
      .then(([currentStatus, queueData]) => {
        if (!active) return;
        setStatus(validateStatus(currentStatus));
        setQueue(normalizeQueue(queueData));
      })
      .catch((requestError) => {
        if (!active) return;
        setStatus(null);
        setQueue([]);
        setError(requestError);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [courseId, groupId, labId]);

  const performAction = async (action, successKey) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(t(successKey));
      await reload();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setBusy(false);
    }
  };

  const nextSessionLabel = useMemo(
    () => sessionLabel(status?.next_session, i18n.language, t),
    [i18n.language, status?.next_session, t]
  );
  const currentSessionLabel = useMemo(
    () => sessionLabel(status?.session, i18n.language, t),
    [i18n.language, status?.session, t]
  );

  const needsSignIn =
    error?.code === "SESSION_REQUIRED" || error?.status === 401;
  const errorMessage = error
    ? needsSignIn
      ? null
      : error.code
        ? t(`queue.errors.${error.code}`, {
            defaultValue: error.message || t("queue.errors.generic"),
          })
        : error.message || t("queue.errors.generic")
    : null;
  const team = status?.team;
  const teamMembers = Array.isArray(team?.members) ? team.members : [];
  const validShift = /^[1-9]\d*$/.test(shiftBy) && Number.isSafeInteger(Number(shiftBy));

  const renderQueueState = () => {
    if (status.already_passed) {
      return <Alert severity="success">{t("queue.alreadyPassed")}</Alert>;
    }
    if (status.in_queue) {
      return (
        <Alert severity="success">
          {t("queue.position", {
            position: status.position,
            total: status.queue_size,
          })}
        </Alert>
      );
    }
    if (status.queue_open === false) {
      return (
        <Alert severity="warning">
          {t("queue.closed", { nextSession: nextSessionLabel })}
        </Alert>
      );
    }
    if (status.ci_status === "not_run") {
      return <Alert severity="info">{t("queue.ciNotRun")}</Alert>;
    }
    if (status.ci_status === "failed") {
      return <Alert severity="warning">{t("queue.ciFailed")}</Alert>;
    }
    if (status.can_register && status.queue_open) {
      return (
        <Button
          variant="contained"
          disabled={busy}
          onClick={() =>
            performAction(
              () => registerForDefenseQueue(courseId, groupId, labId),
              "queue.registered"
            )
          }
        >
          {t(busy ? "queue.working" : "queue.register")}
        </Button>
      );
    }
    return <Alert severity="info">{t("queue.cannotRegister")}</Alert>;
  };

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

        {!loading && needsSignIn && (
          <Alert
            severity="info"
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => window.location.assign(getJoinStartUrl(courseId, labId))}
              >
                {t("queue.signIn")}
              </Button>
            }
          >
            {t("queue.signInRequired")}
          </Alert>
        )}

        {!loading && errorMessage && <Alert severity="error">{errorMessage}</Alert>}
        {notice && <Alert severity="success">{notice}</Alert>}

        {!loading && status && (
          <>
            {status.is_team_lab && team && (
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

            {renderQueueState()}

            {status.in_queue && (
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems="flex-start">
                <Button
                  variant="outlined"
                  color="error"
                  disabled={busy}
                  onClick={() =>
                    performAction(
                      () => withdrawFromDefenseQueue(courseId, groupId, labId),
                      "queue.withdrawn"
                    )
                  }
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
                    onClick={() =>
                      performAction(
                        () =>
                          shiftDefenseQueue(
                            courseId,
                            groupId,
                            labId,
                            Number(shiftBy)
                          ),
                        "queue.shifted"
                      )
                    }
                  >
                    {t(busy ? "queue.working" : "queue.shift")}
                  </Button>
                </Stack>
              </Stack>
            )}

            {status.session && (
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
              {queue.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  {t("queue.empty")}
                </Typography>
              ) : (
                <List disablePadding>
                  {queue.map((entry, index) => {
                    const position = entry.position ?? index + 1;
                    const registeredAt = formatSessionMoment(
                      entry.registered_at || entry.registeredAt,
                      i18n.language
                    );
                    return (
                      <ListItem
                        key={
                          entry.id ||
                          `${entry.identifier || entry.github || entryName(entry)}-${index}`
                        }
                        divider={index < queue.length - 1}
                        disableGutters
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
                              <Chip size="small" label={position} />
                              <Typography component="span" variant="body2">
                                {entryName(entry) || t("queue.unnamedEntry")}
                              </Typography>
                            </Stack>
                          }
                          secondary={registeredAt}
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
