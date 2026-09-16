import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  Autocomplete,
  Checkbox,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  FormControlLabel,
  LinearProgress,
  MenuItem,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  TextField,
  Button as MuiButton,
} from "@mui/material";
import { fetchGroups } from "../../../api";
import { TableWrapper, HintText, StatusChipRow } from "./styled";

const JOB_POLL_INTERVAL_MS = 2000;

const RESULT_STATUS_COLOR = {
  updated: "success",
  rejected: "warning",
  pending: "info",
  error: "error",
  conflict: "error",
  unmatched: "warning",
  ambiguous: "warning",
  no_team: "warning",
};

async function fetchJson(url, options) {
  const response = await fetch(url, { credentials: "include", ...options });
  let data = null;
  try {
    data = await response.json();
  } catch {
    // no body
  }
  if (!response.ok) {
    const error = new Error((data && data.detail) || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

/**
 * Запуск массовой проверки одной лабораторной для одной группы и показ отчёта.
 *
 * Группа спрашивается здесь, а не на странице: список лабораторных общий для
 * курса, а проверка идёт по листу конкретной группы.
 */
export const BulkGradeDialog = ({ courseId, lab, onClose, onError }) => {
  const { t } = useTranslation();

  const [groups, setGroups] = useState([]);
  const [groupId, setGroupId] = useState("");
  const [nameFile, setNameFile] = useState(lab.name_file || "");
  const [dryRun, setDryRun] = useState(false);
  const [starting, setStarting] = useState(false);

  const [job, setJob] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    fetchGroups(courseId)
      .then(setGroups)
      .catch((err) => onError(err.message || t("adminLabs.bulk.errors.groupsFailed")));
  }, [courseId, onError, t]);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => stopPolling, []);

  const pollJob = useCallback((jobId) => {
    stopPolling();
    const poll = () => {
      fetchJson(`/api/v1/admin/bulk-grade-jobs/${jobId}`)
        .then((data) => {
          setJob(data);
          if (data.status !== "running") {
            stopPolling();
          }
        })
        .catch(() => {
          stopPolling();
        });
    };
    poll();
    pollRef.current = setInterval(poll, JOB_POLL_INTERVAL_MS);
  }, []);

  const handleStart = () => {
    setStarting(true);
    fetchJson(
      `/api/v1/admin/courses/${courseId}/groups/${encodeURIComponent(groupId)}/labs/${encodeURIComponent(lab.id)}/bulk-grade`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name_file: nameFile || null, dry_run: dryRun }),
      }
    )
      .then((data) => {
        setStarting(false);
        setJob({ job_id: data.job_id, status: "running", total: 0, processed: 0, results: [] });
        pollJob(data.job_id);
      })
      .catch((err) => {
        setStarting(false);
        if (err.status === 409) {
          onError(t("adminLabs.bulk.errors.alreadyRunning"));
        } else {
          onError(err.message || t("adminLabs.bulk.errors.startFailed"));
        }
      });
  };

  const handleCancelJob = () => {
    if (!job) return;
    fetchJson(`/api/v1/admin/bulk-grade-jobs/${job.job_id}/cancel`, { method: "POST" })
      .then(setJob)
      .catch((err) => onError(err.message || t("adminLabs.bulk.errors.cancelFailed")));
  };

  const handleClose = () => {
    stopPolling();
    onClose();
  };

  const running = job && job.status === "running";
  const results = (job && job.results) || [];
  // Колонка команды появляется только у командной лабы: у индивидуальной
  // поле team пустое у всех строк, и лишний столбец только мешает.
  const hasTeams = results.some((result) => result.team);

  return (
    <Dialog open onClose={running ? undefined : handleClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("adminLabs.bulk.title", { lab: lab.short_name })}</DialogTitle>
      <DialogContent>
        {!job && (
          <>
            <TextField
              select
              fullWidth
              size="small"
              margin="dense"
              label={t("adminLabs.bulk.group")}
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
              disabled={groups.length === 0}
            >
              {groups.map((group) => (
                <MenuItem key={group} value={group}>
                  {group}
                </MenuItem>
              ))}
            </TextField>

            <Autocomplete
              freeSolo
              size="small"
              options={lab.files || []}
              value={nameFile}
              onChange={(_, value) => setNameFile(value || "")}
              onInputChange={(_, value) => setNameFile(value || "")}
              renderInput={(params) => (
                <TextField {...params} margin="dense" label={t("adminLabs.bulk.nameFile")} />
              )}
            />

            <HintText>
              {nameFile ? t("adminLabs.bulk.modeByFile") : t("adminLabs.bulk.modeBySheet")}
            </HintText>

            <FormControlLabel
              control={
                <Checkbox size="small" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              }
              label={t("adminLabs.bulk.dryRun")}
            />
          </>
        )}

        {job && (
          <>
            <p>
              {running && t("adminLabs.bulk.inProgress", { processed: job.processed, total: job.total })}
              {job.status === "done" && t("adminLabs.bulk.done")}
              {job.status === "cancelled" && t("adminLabs.bulk.cancelled")}
              {job.status === "failed" &&
                `${t("adminLabs.bulk.failed")}${job.error ? `: ${job.error}` : ""}`}
            </p>

            {job.dry_run && <Chip size="small" label={t("adminLabs.bulk.dryRunBadge")} />}

            {running && (
              <LinearProgress
                variant={job.total ? "determinate" : "indeterminate"}
                value={job.total ? (job.processed / job.total) * 100 : 0}
              />
            )}

            {job.counts && Object.keys(job.counts).length > 0 && (
              <StatusChipRow style={{ marginTop: 12 }}>
                {Object.entries(job.counts).map(([status, count]) => (
                  <Chip
                    key={status}
                    size="small"
                    color={RESULT_STATUS_COLOR[status] || "default"}
                    label={`${t(`adminLabs.bulk.statuses.${status}`, status)}: ${count}`}
                  />
                ))}
              </StatusChipRow>
            )}

            {results.length > 0 && (
              <TableWrapper>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>{t("adminLabs.bulk.columns.student")}</TableCell>
                      <TableCell>{t("adminLabs.bulk.columns.github")}</TableCell>
                      {hasTeams && <TableCell>{t("adminLabs.bulk.columns.team")}</TableCell>}
                      <TableCell>{t("adminLabs.bulk.columns.status")}</TableCell>
                      <TableCell>{t("adminLabs.bulk.columns.grade")}</TableCell>
                      <TableCell>{t("adminLabs.bulk.columns.message")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {results.map((r, index) => (
                      <TableRow key={`${r.repo || r.github || index}-${index}`}>
                        <TableCell>{r.student_name || "—"}</TableCell>
                        <TableCell>
                          {r.github || "—"}
                          {r.registered && ` (${t("adminLabs.bulk.registered")})`}
                        </TableCell>
                        {hasTeams && <TableCell>{r.team || "—"}</TableCell>}
                        <TableCell>
                          <Chip
                            size="small"
                            color={RESULT_STATUS_COLOR[r.status] || "default"}
                            label={t(`adminLabs.bulk.statuses.${r.status}`, r.status)}
                          />
                        </TableCell>
                        <TableCell>{r.grade || "—"}</TableCell>
                        <TableCell>{r.message}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableWrapper>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        {running ? (
          <MuiButton onClick={handleCancelJob} color="error">
            {t("adminLabs.bulk.cancelRun")}
          </MuiButton>
        ) : (
          <MuiButton onClick={handleClose}>{t("adminLabs.bulk.close")}</MuiButton>
        )}
        {!job && (
          <MuiButton variant="contained" disabled={!groupId || starting} onClick={handleStart}>
            {t("adminLabs.bulk.start")}
          </MuiButton>
        )}
      </DialogActions>
    </Dialog>
  );
};
