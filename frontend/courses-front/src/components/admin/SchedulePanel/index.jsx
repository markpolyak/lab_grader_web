import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { Panel, Header, Actions } from "./styled";

function errorDetail(data, fallback) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "string" ? item : item.msg || JSON.stringify(item)))
      .join("; ");
  }
  return fallback;
}

async function fetchJson(url, options) {
  const response = await fetch(url, { credentials: "include", ...options });
  let data = null;
  try {
    data = await response.json();
  } catch {
    // empty body
  }
  if (!response.ok) {
    const error = new Error(errorDetail(data, `HTTP ${response.status}`));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

const timeOptions = { hour: "2-digit", minute: "2-digit" };

function formatTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString([], timeOptions);
}

function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString();
}

/**
 * Блок расписания на странице лабораторных курса: обновить слоты группы
 * из ГУАП и показать сессии на ближайшие 7 дней.
 */
export const SchedulePanel = ({ courseId }) => {
  const { t } = useTranslation();

  const [groups, setGroups] = useState([]);
  const [groupId, setGroupId] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [refreshResult, setRefreshResult] = useState(null);
  const [previewResult, setPreviewResult] = useState(null);
  const [error, setError] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);

  useEffect(() => {
    if (!courseId) return undefined;
    let cancelled = false;
    fetchJson(`/api/v1/courses/${courseId}/groups`)
      .then((list) => {
        if (cancelled) return;
        const names = Array.isArray(list) ? list.map(String) : [];
        setGroups(names);
        setGroupId((current) => {
          if (current && names.includes(current)) return current;
          if (names.includes("4432")) return "4432";
          return names[0] || "";
        });
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || t("schedule.errors.groupsFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [courseId, t]);

  const handleRefresh = () => {
    if (!groupId) {
      setError(t("schedule.errors.noGroup"));
      return;
    }
    setRefreshing(true);
    setError(null);
    fetchJson(`/api/v1/admin/courses/${courseId}/groups/${encodeURIComponent(groupId)}/schedule/refresh`, {
      method: "POST",
    })
      .then((data) => setRefreshResult(data))
      .catch((err) => setError(err.message || t("schedule.errors.refreshFailed")))
      .finally(() => setRefreshing(false));
  };

  const handlePreview = () => {
    if (!groupId) {
      setError(t("schedule.errors.noGroup"));
      return;
    }
    setPreviewing(true);
    setError(null);
    fetchJson(
      `/api/v1/admin/courses/${courseId}/groups/${encodeURIComponent(groupId)}/schedule/sessions?days=7`
    )
      .then((data) => {
        setPreviewResult(data);
        setPreviewOpen(true);
      })
      .catch((err) => setError(err.message || t("schedule.errors.previewFailed")))
      .finally(() => setPreviewing(false));
  };

  const labLabel = (labs) => {
    if (labs === null || labs === undefined) return t("schedule.labs.all");
    if (Array.isArray(labs) && labs.length === 0) return t("schedule.labs.closed");
    if (Array.isArray(labs)) return labs.join(", ");
    return String(labs);
  };

  const sessions = (previewResult && previewResult.sessions) || [];
  const queueSettings = previewResult && previewResult.queue_settings;

  return (
    <Panel>
      <Header>
        <Typography variant="h6">
          {t("schedule.title")}
          {groupId ? ` · ${groupId}` : ""}
        </Typography>
        <Actions>
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel id="schedule-group-label">{t("schedule.selectGroup")}</InputLabel>
            <Select
              labelId="schedule-group-label"
              label={t("schedule.selectGroup")}
              value={groupId}
              onChange={(event) => {
                setGroupId(event.target.value);
                setRefreshResult(null);
              }}
              disabled={groups.length === 0}
            >
              {groups.map((name) => (
                <MenuItem key={name} value={name}>
                  {name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Button
            variant="contained"
            color="primary"
            onClick={handleRefresh}
            disabled={refreshing || !groupId}
            startIcon={refreshing ? <CircularProgress size={16} /> : null}
          >
            {refreshing ? t("schedule.refreshing") : t("schedule.refreshButton")}
          </Button>
          <Button
            variant="outlined"
            onClick={handlePreview}
            disabled={previewing || !groupId}
            startIcon={previewing ? <CircularProgress size={16} /> : null}
          >
            {t("schedule.previewButton")}
          </Button>
        </Actions>
      </Header>

      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {refreshResult && (
        <div>
          <Alert severity="success">
            {t("schedule.refreshedOk", { count: refreshResult.slots_count })}
          </Alert>
          {Array.isArray(refreshResult.slots) && refreshResult.slots.length > 0 && (
            <>
              <Typography variant="subtitle2" sx={{ mt: 1, mb: 0.5 }}>
                {t("schedule.fetchedSlots")}:
              </Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>{t("schedule.columns.id")}</TableCell>
                    <TableCell>{t("schedule.columns.day")}</TableCell>
                    <TableCell>{t("schedule.columns.time")}</TableCell>
                    <TableCell>{t("schedule.columns.subject")}</TableCell>
                    <TableCell>{t("schedule.columns.room")}</TableCell>
                    <TableCell>{t("schedule.columns.teacher")}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {refreshResult.slots.slice(0, 50).map((slot, index) => (
                    <TableRow key={slot.id || index}>
                      <TableCell>{slot.id || "—"}</TableCell>
                      <TableCell>
                        {slot.recurrence
                          ? `${slot.recurrence.day} (${slot.recurrence.week})`
                          : slot.date || "—"}
                      </TableCell>
                      <TableCell>
                        {slot.start}—{slot.end}
                      </TableCell>
                      <TableCell>{slot.subject || "—"}</TableCell>
                      <TableCell>{slot.room || "—"}</TableCell>
                      <TableCell>{slot.teacher || "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          )}
        </div>
      )}

      <Dialog open={previewOpen} onClose={() => setPreviewOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{t("schedule.previewTitle", { group: groupId })}</DialogTitle>
        <DialogContent>
          {queueSettings && Object.keys(queueSettings).length > 0 && (
            <div style={{ marginBottom: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Chip
                size="small"
                label={`${t("schedule.maxStudents")}: ${queueSettings.max_students_per_slot ?? "—"}`}
              />
              <Chip
                size="small"
                label={`${t("schedule.multiLabs")}: ${queueSettings.allow_multiple_labs ? "✓" : "✗"}`}
              />
            </div>
          )}
          {sessions.length === 0 ? (
            <Typography>{t("schedule.noSessions")}</Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("schedule.columns.date")}</TableCell>
                  <TableCell>{t("schedule.columns.time")}</TableCell>
                  <TableCell>{t("schedule.columns.slot")}</TableCell>
                  <TableCell>{t("schedule.columns.subject")}</TableCell>
                  <TableCell>{t("schedule.columns.labs")}</TableCell>
                  <TableCell>{t("schedule.columns.room")}</TableCell>
                  <TableCell>{t("schedule.columns.window")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {sessions.map((session, index) => (
                  <TableRow key={`${session.slot_id || ""}-${index}`}>
                    <TableCell>{formatDate(session.starts_at)}</TableCell>
                    <TableCell>
                      {formatTime(session.starts_at)}—{formatTime(session.ends_at)}
                    </TableCell>
                    <TableCell>{session.slot_id || "—"}</TableCell>
                    <TableCell>{session.subject || "—"}</TableCell>
                    <TableCell>{labLabel(session.allowed_lab_ids)}</TableCell>
                    <TableCell>{session.room || "—"}</TableCell>
                    <TableCell>
                      <Typography variant="caption" color="textSecondary">
                        {formatTime(session.auto_open_at)}—{formatTime(session.auto_close_at)}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPreviewOpen(false)}>{t("schedule.close")}</Button>
        </DialogActions>
      </Dialog>
    </Panel>
  );
};

export default SchedulePanel;
