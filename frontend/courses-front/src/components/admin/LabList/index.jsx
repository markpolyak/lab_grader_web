import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  Tooltip,
  Chip,
  LinearProgress,
  Snackbar,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button as MuiButton,
  Checkbox,
} from "@mui/material";
import {
  Container,
  Panel,
  PageTitle,
  BackButton,
  TableWrapper,
  SelectableTableWrapper,
  HintText,
} from "./styled";

// Опрос статуса фоновой работы - см. main.py GET /admin/propagate-jobs/{job_id}
const JOB_POLL_INTERVAL_MS = 2000;

const RESULT_STATUS_COLOR = {
  will_process: "default",
  pr_created: "success",
  up_to_date: "info",
  pr_exists: "info",
  not_a_fork: "warning",
  error: "error",
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

export const LabList = ({ courseId, onBack }) => {
  const { t } = useTranslation();

  const [labs, setLabs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [snackbar, setSnackbar] = useState({ open: false, message: "", severity: "info" });

  const [selectedLab, setSelectedLab] = useState(null);
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [dryRunLoading, setDryRunLoading] = useState(false);
  const [dryRunResult, setDryRunResult] = useState(null);
  // Имена репозиториев, которым уйдёт обновление. Заполняется всеми
  // will_process при получении предпросмотра; выбор живёт только пока
  // открыт диалог - при повторном открытии список перезапрашивается.
  const [selectedRepos, setSelectedRepos] = useState([]);
  const [starting, setStarting] = useState(false);

  const [job, setJob] = useState(null);
  const pollRef = useRef(null);

  const showSnackbar = (message, severity = "info") => setSnackbar({ open: true, message, severity });

  const loadLabs = useCallback(() => {
    setLoading(true);
    fetchJson(`/api/v1/admin/courses/${courseId}/labs`)
      .then((data) => {
        setLabs(data);
        setLoading(false);
      })
      .catch((err) => {
        setLoading(false);
        showSnackbar(err.message || t("adminLabs.errorLoadingLabs"), "error");
      });
  }, [courseId, t]);

  useEffect(() => {
    loadLabs();
  }, [loadLabs]);

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
      fetchJson(`/api/v1/admin/propagate-jobs/${jobId}`)
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

  const handleUpdateClick = (lab) => {
    setSelectedLab(lab);
    setDryRunResult(null);
    setDryRunOpen(true);
    setDryRunLoading(true);
    fetchJson(`/api/v1/admin/courses/${courseId}/labs/${lab.id}/propagate-template-update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: true }),
    })
      .then((data) => {
        setDryRunResult(data);
        setSelectedRepos(
          data.results.filter((r) => r.status === "will_process").map((r) => r.repo)
        );
        setDryRunLoading(false);
      })
      .catch((err) => {
        setDryRunLoading(false);
        setDryRunOpen(false);
        showSnackbar(err.message || t("adminLabs.errors.dryRunFailed"), "error");
      });
  };

  const handleCloseDryRun = () => {
    setDryRunOpen(false);
    setSelectedLab(null);
    setDryRunResult(null);
    setSelectedRepos([]);
  };

  const toggleRepo = (repo) =>
    setSelectedRepos((current) =>
      current.includes(repo) ? current.filter((r) => r !== repo) : [...current, repo]
    );

  const handleConfirmRun = () => {
    if (!selectedLab) return;
    setStarting(true);
    fetchJson(`/api/v1/admin/courses/${courseId}/labs/${selectedLab.id}/propagate-template-update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: false, repos: selectedRepos }),
    })
      .then((data) => {
        setStarting(false);
        setDryRunOpen(false);
        setJob({
          job_id: data.job_id,
          status: "running",
          total: selectedRepos.length,
          processed: 0,
          results: [],
        });
        pollJob(data.job_id);
      })
      .catch((err) => {
        setStarting(false);
        if (err.status === 409) {
          showSnackbar(t("adminLabs.errors.alreadyRunning"), "error");
        } else {
          showSnackbar(err.message || t("adminLabs.errors.startFailed"), "error");
        }
      });
  };

  const handleCloseJob = () => {
    stopPolling();
    setJob(null);
    setSelectedLab(null);
  };

  const willProcess = dryRunResult
    ? dryRunResult.results.filter((r) => r.status === "will_process")
    : [];
  const notAFork = dryRunResult
    ? dryRunResult.results.filter((r) => r.status === "not_a_fork")
    : [];
  const allSelected = willProcess.length > 0 && selectedRepos.length === willProcess.length;
  const someSelected = selectedRepos.length > 0 && !allSelected;
  const toggleAll = () =>
    setSelectedRepos(allSelected ? [] : willProcess.map((r) => r.repo));

  return (
    <Container>
      <Panel>
        <BackButton onClick={onBack}>{t("adminLabs.back")}</BackButton>
        <PageTitle>{t("adminLabs.title")}</PageTitle>

        {loading ? (
          <HintText>{t("adminLabs.loading")}</HintText>
        ) : (
          <TableWrapper>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("adminLabs.columns.number")}</TableCell>
                  <TableCell>{t("adminLabs.columns.shortName")}</TableCell>
                  <TableCell>{t("adminLabs.columns.githubPrefix")}</TableCell>
                  <TableCell>{t("adminLabs.columns.templateRepo")}</TableCell>
                  <TableCell>{t("adminLabs.columns.provisioning")}</TableCell>
                  <TableCell>{t("adminLabs.columns.actions")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {labs.map((lab) => (
                  <TableRow key={lab.id}>
                    <TableCell>{lab.id}</TableCell>
                    <TableCell>{lab.short_name}</TableCell>
                    <TableCell>{lab.github_prefix || "—"}</TableCell>
                    <TableCell>{lab.template_repo || "—"}</TableCell>
                    <TableCell>
                      {lab.repo_provisioning === "fork"
                        ? t("adminLabs.provisioningFork")
                        : t("adminLabs.provisioningTemplate")}
                    </TableCell>
                    <TableCell>
                      {lab.can_propagate ? (
                        <MuiButton size="small" variant="outlined" onClick={() => handleUpdateClick(lab)}>
                          {t("adminLabs.updateButton")}
                        </MuiButton>
                      ) : (
                        <Tooltip title={t("adminLabs.disabledReason")}>
                          <span>
                            <MuiButton size="small" variant="outlined" disabled>
                              {t("adminLabs.updateButton")}
                            </MuiButton>
                          </span>
                        </Tooltip>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableWrapper>
        )}
      </Panel>

      {/* Диалог предпросмотра (dry-run) перед реальной рассылкой */}
      <Dialog open={dryRunOpen} onClose={handleCloseDryRun} maxWidth="sm" fullWidth>
        <DialogTitle>{t("adminLabs.dryRun.title")}</DialogTitle>
        <DialogContent>
          {dryRunLoading ? (
            <LinearProgress />
          ) : dryRunResult ? (
            <>
              <p>{t("adminLabs.dryRun.summary", { count: willProcess.length })}</p>
              <p>{t("adminLabs.dryRun.selectedCount", { selected: selectedRepos.length, total: willProcess.length })}</p>
              {notAFork.length > 0 && (
                <p>{t("adminLabs.dryRun.notAFork", { count: notAFork.length })}</p>
              )}
              <SelectableTableWrapper>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell padding="checkbox">
                        <Checkbox
                          size="small"
                          checked={allSelected}
                          indeterminate={someSelected}
                          onChange={toggleAll}
                          disabled={willProcess.length === 0}
                          inputProps={{ "aria-label": t("adminLabs.dryRun.selectAll") }}
                        />
                      </TableCell>
                      <TableCell>{t("adminLabs.dryRun.selectAll")}</TableCell>
                      <TableCell />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {willProcess.map((r) => (
                      <TableRow key={r.repo} hover onClick={() => toggleRepo(r.repo)}>
                        <TableCell padding="checkbox">
                          <Checkbox
                            size="small"
                            checked={selectedRepos.includes(r.repo)}
                            inputProps={{ "aria-label": r.repo }}
                          />
                        </TableCell>
                        <TableCell>{r.repo}</TableCell>
                        <TableCell>
                          <Chip size="small" label={t("adminLabs.dryRun.statusWillProcess")} />
                        </TableCell>
                      </TableRow>
                    ))}
                    {notAFork.map((r) => (
                      <TableRow key={r.repo}>
                        {/* Форк-связи нет, рассылать нечего - строка без чекбокса */}
                        <TableCell padding="checkbox" />
                        <TableCell>{r.repo}</TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            color="warning"
                            label={t("adminLabs.dryRun.statusNotAFork")}
                          />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </SelectableTableWrapper>
            </>
          ) : null}
        </DialogContent>
        <DialogActions>
          <MuiButton onClick={handleCloseDryRun}>{t("adminLabs.dryRun.cancel")}</MuiButton>
          <MuiButton
            variant="contained"
            disabled={dryRunLoading || starting || selectedRepos.length === 0}
            onClick={handleConfirmRun}
          >
            {t("adminLabs.dryRun.confirm", { count: selectedRepos.length })}
          </MuiButton>
        </DialogActions>
      </Dialog>

      {/* Прогресс и итоги фоновой рассылки */}
      <Dialog open={!!job} onClose={job && job.status !== "running" ? handleCloseJob : undefined} maxWidth="sm" fullWidth>
        <DialogTitle>{t("adminLabs.progress.title")}</DialogTitle>
        <DialogContent>
          {job && (
            <>
              <p>
                {job.status === "running" && t("adminLabs.progress.inProgress", { processed: job.processed, total: job.total })}
                {job.status === "done" && t("adminLabs.progress.done")}
                {job.status === "failed" && `${t("adminLabs.progress.failed")}${job.error ? `: ${job.error}` : ""}`}
              </p>
              {job.status === "running" && (
                <LinearProgress
                  variant={job.total ? "determinate" : "indeterminate"}
                  value={job.total ? (job.processed / job.total) * 100 : 0}
                />
              )}
              {job.results && job.results.length > 0 && (
                <TableWrapper>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>{t("adminLabs.progress.columns.repo")}</TableCell>
                        <TableCell>{t("adminLabs.progress.columns.status")}</TableCell>
                        <TableCell>{t("adminLabs.progress.columns.link")}</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {job.results.map((r) => (
                        <TableRow key={r.repo}>
                          <TableCell>{r.repo}</TableCell>
                          <TableCell>
                            <Chip
                              size="small"
                              color={RESULT_STATUS_COLOR[r.status] || "default"}
                              label={t(`adminLabs.progress.statuses.${r.status}`, r.status)}
                            />
                          </TableCell>
                          <TableCell>
                            {r.pr_url && (
                              <a href={r.pr_url} target="_blank" rel="noopener noreferrer">
                                {t("adminLabs.progress.openPr")}
                              </a>
                            )}
                          </TableCell>
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
          <MuiButton onClick={handleCloseJob} disabled={job && job.status === "running"}>
            {t("adminLabs.progress.close")}
          </MuiButton>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          severity={snackbar.severity}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};
