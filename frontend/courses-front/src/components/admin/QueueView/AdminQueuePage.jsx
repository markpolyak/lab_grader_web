import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Chip,
  Container,
  CircularProgress,
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

const STATUS_COLOR = {
  "ждёт": "info",
  "сдал": "success",
  "не_сдал": "error",
  "нет": "warning",
  "отозван": "default",
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
  const [error, setError] = useState(null);

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
    </Container>
  );
};

export default AdminQueuePage;
