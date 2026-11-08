import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  MenuItem,
  Stack,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  TextField,
  Typography,
} from "@mui/material";
import { fetchGroups, fetchDefenseQueue } from "../../../api";

const STATUS_COLOR = {
  "ждёт": "info",
  "сдал": "success",
  "не_сдал": "error",
  "нет": "warning",
  "отозван": "default",
};

/**
 * Read-only defense queue viewer for teachers.
 *
 * The teacher picks a group (required by the backend URL) and sees the
 * full daily queue sheet — all groups, all statuses. No action buttons:
 * status changes happen in the Google Sheet directly.
 */
// eslint-disable-next-line react/prop-types
export const QueueViewDialog = ({ courseId, lab, onClose }) => {
  const { t } = useTranslation();
  const [groups, setGroups] = useState([]);
  const [groupId, setGroupId] = useState("");
  const [queue, setQueue] = useState([]);
  const [queueSize, setQueueSize] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchGroups(courseId)
      .then((data) => {
        setGroups(data);
        if (data.length > 0) setGroupId(data[0]);
      })
      .catch(() => setError(t("adminQueue.errors.groupsFailed")));
  }, [courseId, t]);

  const loadQueue = useCallback(() => {
    if (!groupId) return;
    setLoading(true);
    setError(null);
    fetchDefenseQueue(courseId, groupId, lab.id)
      .then((data) => {
        setQueue(Array.isArray(data?.queue) ? data.queue : []);
        setQueueSize(data?.size ?? (Array.isArray(data?.queue) ? data.queue.length : 0));
      })
      .catch((err) => {
        setError(err.code || err.message || t("adminQueue.errors.loadFailed"));
        setQueue([]);
        setQueueSize(0);
      })
      .finally(() => setLoading(false));
  }, [courseId, groupId, lab.id, t]);

  useEffect(() => {
    if (groupId) loadQueue();
  }, [groupId, loadQueue]);

  const labLabel = lab.short_name || lab.shortName || lab.id;

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {t("adminQueue.title", { lab: labLabel })}
      </DialogTitle>
      <DialogContent>
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2, mt: 1 }}>
          <TextField
            select
            size="small"
            label={t("adminQueue.group")}
            value={groupId}
            onChange={(e) => setGroupId(e.target.value)}
            sx={{ minWidth: 200 }}
          >
            {groups.map((g) => (
              <MenuItem key={g} value={g}>{g}</MenuItem>
            ))}
          </TextField>
          <Button
            variant="outlined"
            size="small"
            disabled={!groupId || loading}
            onClick={loadQueue}
          >
            {t("adminQueue.refresh")}
          </Button>
          {queueSize > 0 && (
            <Typography variant="body2" color="text.secondary">
              {t("adminQueue.total", { count: queueSize })}
            </Typography>
          )}
        </Stack>

        {loading && (
          <Stack direction="row" spacing={1} alignItems="center">
            <CircularProgress size={20} />
            <Typography variant="body2">{t("adminQueue.loading")}</Typography>
          </Stack>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>
        )}

        {!loading && !error && groupId && queue.length === 0 && (
          <Typography color="text.secondary">{t("adminQueue.empty")}</Typography>
        )}

        {!loading && !error && queue.length > 0 && (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("adminQueue.columns.position")}</TableCell>
                <TableCell>{t("adminQueue.columns.group")}</TableCell>
                <TableCell>{t("adminQueue.columns.name")}</TableCell>
                <TableCell>{t("adminQueue.columns.lab")}</TableCell>
                <TableCell>{t("adminQueue.columns.status")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {queue.map((entry, index) => (
                <TableRow key={`${entry.fio}-${entry.lab}-${index}`}>
                  <TableCell>{entry.position ?? index + 1}</TableCell>
                  <TableCell>{entry.group}</TableCell>
                  <TableCell>{entry.fio}</TableCell>
                  <TableCell>{entry.lab}</TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      color={STATUS_COLOR[entry.status] || "default"}
                      label={t(`queue.statuses.${entry.status}`, { defaultValue: entry.status })}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("adminQueue.close")}</Button>
      </DialogActions>
    </Dialog>
  );
};
