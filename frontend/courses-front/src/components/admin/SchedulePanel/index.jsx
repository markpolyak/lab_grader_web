import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  CircularProgress,
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

/**
 * Блок расписания на странице лабораторных курса: обновить все группы
 * из ГУАП и показать результаты обновления для каждой группы.
 */
// eslint-disable-next-line react/prop-types
export const SchedulePanel = ({ courseId }) => {
  const { t } = useTranslation();

  const [groups, setGroups] = useState([]);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshResult, setRefreshResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!courseId) return undefined;
    let cancelled = false;
    fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`)
      .then((data) => {
        if (cancelled) return;
        const courseGroups = Array.isArray(data.groups) ? data.groups : [];
        setGroups(courseGroups);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || t("schedule.errors.loadGroupsFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [courseId, t]);

  const handleRefresh = () => {
    setRefreshing(true);
    setError(null);
    setRefreshResult(null);
    fetchJson(
      `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/refresh-all`,
      { method: "POST" }
    )
      .then((data) => setRefreshResult(data))
      .catch((err) => setError(err.message || t("schedule.errors.refreshFailed")))
      .finally(() => setRefreshing(false));
  };

  const refreshedCount = refreshResult ? Object.keys(refreshResult.refreshed || {}).length : 0;
  const refreshErrorCount = refreshResult ? Object.keys(refreshResult.errors || {}).length : 0;

  return (
    <Panel>
      <Header>
        <Typography variant="h6">{t("schedule.title")}</Typography>
        <Actions>
          <Button
            variant="contained"
            color="primary"
            onClick={handleRefresh}
            disabled={refreshing || !courseId}
            startIcon={refreshing ? <CircularProgress size={16} /> : null}
          >
            {refreshing ? t("schedule.refreshing") : t("schedule.refreshButton")}
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
          <Alert
            severity={
              refreshErrorCount === 0
                ? "success"
                : refreshedCount === 0
                  ? "error"
                  : "warning"
            }
          >
            {t("schedule.refreshedOk", { ok: refreshedCount, err: refreshErrorCount })}
          </Alert>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("schedule.columns.group")}</TableCell>
                <TableCell>{t("schedule.columns.slotsCount")}</TableCell>
                <TableCell>{t("schedule.columns.status")}</TableCell>
                <TableCell>{t("schedule.columns.error")}</TableCell>
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
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

    </Panel>
  );
};

export default SchedulePanel;
