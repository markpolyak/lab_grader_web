import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { ScheduleEditor } from "../ScheduleEditor";
import { CorrectionsDialog } from "../CorrectionsDialog";
import { SourceDialog } from "../SourceDialog";

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

/**
 * Course schedule settings dialog: manage manual groups and refresh all groups.
 */
// eslint-disable-next-line react/prop-types
export const SchedulePanel = ({ courseId, open, onClose, autoRefresh = false }) => {
  const { t } = useTranslation();

  const [groups, setGroups] = useState([]);
  const [manualGroups, setManualGroups] = useState([]);
  const [newGroup, setNewGroup] = useState("");
  const [warnings, setWarnings] = useState([]);
  const [savingGroups, setSavingGroups] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshResult, setRefreshResult] = useState(null);
  const [error, setError] = useState(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [correctionsOpen, setCorrectionsOpen] = useState(false);
  const [correctionsGroupId, setCorrectionsGroupId] = useState("");
  const [sourceOpen, setSourceOpen] = useState(false);
  const startedAutoRefresh = useRef(false);

  useEffect(() => {
    if (!courseId || !open) return undefined;
    let cancelled = false;
    fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`)
      .then((data) => {
        if (cancelled) return;
        const courseGroups = Array.isArray(data.groups) ? data.groups : [];
        setGroups(courseGroups);
        setManualGroups(Array.isArray(data.manual_groups) ? data.manual_groups : []);
        setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || t("schedule.errors.loadGroupsFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [courseId, open, t]);

  const handleSaveManualGroups = async () => {
    const updatedGroups = [...new Set([...manualGroups, newGroup.trim()].filter(Boolean))];
    setSavingGroups(true);
    setError(null);
    try {
      const data = await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ groups: updatedGroups }),
        }
      );
      setManualGroups(data.manual_groups || []);
      setNewGroup("");
      const updated = await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`
      );
      setGroups(updated.groups || []);
      setWarnings(updated.warnings || []);
    } catch (err) {
      setError(err.message || t("schedule.errors.saveGroupsFailed"));
    } finally {
      setSavingGroups(false);
    }
  };

  const handleRemoveManualGroup = async (groupId) => {
    setSavingGroups(true);
    setError(null);
    try {
      const data = await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ groups: manualGroups.filter((id) => id !== groupId) }),
        }
      );
      setManualGroups(data.manual_groups || []);
      const updated = await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`
      );
      setGroups(updated.groups || []);
      setWarnings(updated.warnings || []);
    } catch (err) {
      setError(err.message || t("schedule.errors.saveGroupsFailed"));
    } finally {
      setSavingGroups(false);
    }
  };

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    setError(null);
    setRefreshResult(null);
    fetchJson(
      `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/refresh-all`,
      { method: "POST" }
    )
      .then((data) => {
        setRefreshResult(data);
        setWarnings(data.warnings || []);
      })
      .catch((err) => setError(err.message || t("schedule.errors.refreshFailed")))
      .finally(() => setRefreshing(false));
  }, [courseId, t]);

  useEffect(() => {
    if (!open || !autoRefresh || startedAutoRefresh.current) return;
    startedAutoRefresh.current = true;
    handleRefresh();
  }, [autoRefresh, handleRefresh, open]);

  const refreshedCount = refreshResult ? Object.keys(refreshResult.refreshed || {}).length : 0;
  const refreshErrorCount = refreshResult ? Object.keys(refreshResult.errors || {}).length : 0;

  return (
    <>
      <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
        <DialogTitle>{t("schedule.title")}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            {t("schedule.groupSourcesDescription")}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ mb: 2 }}>
            <TextField
              size="small"
              label={t("schedule.manualGroupInput")}
              value={newGroup}
              onChange={(event) => setNewGroup(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  handleSaveManualGroups();
                }
              }}
            />
            <Button
              variant="outlined"
              onClick={handleSaveManualGroups}
              disabled={savingGroups || !newGroup.trim()}
            >
              {t("schedule.addManualGroup")}
            </Button>
          </Stack>
          {manualGroups.length > 0 && (
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
              {manualGroups.map((id) => (
                <Chip
                  key={id}
                  label={id}
                  onDelete={() => handleRemoveManualGroup(id)}
                  disabled={savingGroups}
                />
              ))}
            </Stack>
          )}
          {warnings.map((warning) => (
            <Alert key={warning} severity="warning" sx={{ mb: 1 }}>
              {warning}
            </Alert>
          ))}
          {error && (
            <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1 }}>
              {error}
            </Alert>
          )}
          {refreshResult && (
            <Alert
              severity={
                refreshErrorCount === 0 && warnings.length === 0
                  ? "success"
                  : refreshedCount === 0
                    ? "error"
                    : "warning"
              }
              sx={{ mb: 1 }}
            >
              {t("schedule.refreshedOk", { ok: refreshedCount, err: refreshErrorCount })}
            </Alert>
          )}
          {refreshing && <Alert severity="info" sx={{ mb: 1 }}>{t("schedule.refreshing")}</Alert>}
          {refreshResult && (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("schedule.columns.group")}</TableCell>
                  <TableCell>{t("schedule.columns.slotsCount")}</TableCell>
                  <TableCell>{t("schedule.columns.status")}</TableCell>
                  <TableCell>{t("schedule.columns.error")}</TableCell>
                  <TableCell>{t("schedule.columns.corrections")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {groups.map((group) => {
                  const id = String(group.id);
                  const groupError = refreshResult.errors?.[id];
                  const slotCount = refreshResult.refreshed?.[id];
                  return (
                    <TableRow key={id}>
                      <TableCell>{id}</TableCell>
                      <TableCell>{slotCount ?? "—"}</TableCell>
                      <TableCell>
                        {groupError ? t("schedule.status.error") : t("schedule.status.ok")}
                      </TableCell>
                      <TableCell>{groupError || "—"}</TableCell>
                      <TableCell>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => {
                            setCorrectionsGroupId(id);
                            setCorrectionsOpen(true);
                          }}
                        >
                          {t("schedule.columns.corrections")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSourceOpen(true)} variant="outlined">
            {t("schedule.columns.source")}
          </Button>
          <Button onClick={() => setEditorOpen(true)} variant="outlined" sx={{ ml: "auto" }}>
            {t("schedule.editor.title")}
          </Button>
          <Button onClick={onClose}>{t("schedule.status.ok")}</Button>
        </DialogActions>
      </Dialog>
      <ScheduleEditor courseId={courseId} open={editorOpen} onClose={() => setEditorOpen(false)} />
      <CorrectionsDialog
        courseId={courseId}
        groupId={correctionsGroupId}
        open={correctionsOpen}
        onClose={() => setCorrectionsOpen(false)}
        onSaved={() => {
          // Refresh groups list after corrections saved
          fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`)
            .then((data) => {
              const courseGroups = Array.isArray(data.groups) ? data.groups : [];
              setGroups(courseGroups);
              setManualGroups(Array.isArray(data.manual_groups) ? data.manual_groups : []);
              setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
            })
            .catch((err) => setError(err.message || t("schedule.errors.loadGroupsFailed")));
        }}
      />
      <SourceDialog
        courseId={courseId}
        open={sourceOpen}
        onClose={() => setSourceOpen(false)}
        onSaved={() => {
          fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`)
            .then((data) => {
              const courseGroups = Array.isArray(data.groups) ? data.groups : [];
              setGroups(courseGroups);
              setManualGroups(Array.isArray(data.manual_groups) ? data.manual_groups : []);
              setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
            })
            .catch((err) => setError(err.message || t("schedule.errors.loadGroupsFailed")));
        }}
      />
    </>
  );
};

export default SchedulePanel;