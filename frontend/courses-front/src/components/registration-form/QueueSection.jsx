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
  registerAndCheck,
  registerForDefenseQueue,
  shiftDefenseQueue,
  withdrawFromDefenseQueue,
} from "../../api";

const normalizeQueue = (data) => {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.queue)) return data.queue;
  if (Array.isArray(data?.entries)) return data.entries;
  if (Array.isArray(data?.rows)) return data.rows;
  return [];
};

const validateStatus = (data) => {
  if (!data || typeof data !== "object" || typeof data.in_queue !== "boolean") {
    return null;
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
export function QueueSection({ courseId, groupId, labId, formData }) {
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

  const handleJoinQueue = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (status?.session_required && formData) {
        await registerAndCheck(courseId, groupId, formData);
      }
      await registerForDefenseQueue(courseId, groupId, labId);
      setNotice(t("queue.registered"));
      await reload();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setBusy(false);
    }
  };

  const handleWithdraw = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withdrawFromDefenseQueue(courseId, groupId, labId);
      setNotice(t("queue.withdrawn"));
      await reload();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setBusy(false);
    }
  };

  const handleShift = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await shiftDefenseQueue(courseId, groupId, labId, Number(shiftBy));
      setNotice(t("queue.shifted"));
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

  const errorMessage = error
    ? error.code
      ? t(`queue.errors.${error.code}`, {
          defaultValue: error.message || t("queue.errors.generic"),
        })
      : error.message || t("queue.errors.generic")
    : null;

  const team = status?.team;
  const teamMembers = Array.isArray(team?.members) ? team.members : [];
  const validShift = /^[1-9]\d*$/.test(shiftBy) && Number.isSafeInteger(Number(shiftBy));
  const formReady = formData && formData.surname && formData.name && formData.github;

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
            {status?.is_team_lab && team && (
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

            {status && status.already_passed && (
              <Alert severity="success">{t("queue.alreadyPassed")}</Alert>
            )}

            {status && status.in_queue && (
              <Alert severity="success">
                {t("queue.position", {
                  position: status.position,
                  total: status.queue_size,
                })}
              </Alert>
            )}

            {status && !status.in_queue && !status.already_passed && status.queue_open === false && (
              <Alert severity="warning">
                {t("queue.closed", { nextSession: nextSessionLabel })}
              </Alert>
            )}

            {status && !status.in_queue && !status.already_passed && status.queue_open !== false && (
              <Button
                variant="contained"
                color="primary"
                size="large"
                disabled={
                  busy ||
                  !formReady ||
                  (!status.session_required && status.can_register !== true)
                }
                onClick={handleJoinQueue}
              >
                {t(busy ? "queue.working" : "queue.register")}
              </Button>
            )}

            {status && status.in_queue && (
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems="flex-start">
                <Button
                  variant="outlined"
                  color="error"
                  disabled={busy}
                  onClick={handleWithdraw}
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
                    onClick={handleShift}
                  >
                    {t(busy ? "queue.working" : "queue.shift")}
                  </Button>
                </Stack>
              </Stack>
            )}

            {status?.session && (
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
                    return (
                      <ListItem
                        key={`${entry.fio}-${entry.lab}-${index}`}
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
