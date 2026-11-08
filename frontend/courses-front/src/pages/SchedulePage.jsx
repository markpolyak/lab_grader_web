import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Container,
  FormControl,
  FormHelperText,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Tab,
  TabList as MuiTabList,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Toolbar,
  Tooltip,
  Typography,
} from "@mui/material";
import { TabContext, TabList, TabPanel } from "@mui/lab";
import { ScheduleEditor } from "../components/admin/ScheduleEditor";
import { CorrectionsDialog } from "../components/admin/CorrectionsDialog";
import { load, dump } from "js-yaml";

// Inline SVG icons to avoid @mui/icons-material dependency
function ArrowBackIcon() {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true" focusable="false">
      <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2Z" />
    </svg>
  );
}

function SaveIcon() {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true" focusable="false">
      <path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4Z" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true" focusable="false">
      <path d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35Z" />
    </svg>
  );
}

async function fetchJson(url, options) {
  const response = await fetch(url, { credentials: "include", ...options });
  let data = null;
  try {
    data = await response.json();
  } catch {
  }
  if (!response.ok) {
    const error = new Error(data?.detail || (typeof data === "string" ? data : `HTTP ${response.status}`));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

export const SchedulePage = () => {
  const { t } = useTranslation();
  const { courseId } = useParams();
  const navigate = useNavigate();

  const [groups, setGroups] = useState([]);
  const [manualGroups, setManualGroups] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshResult, setRefreshResult] = useState(null);
  const [tab, setTab] = useState(0);
  const [correctionsOpen, setCorrectionsOpen] = useState(false);
  const [correctionsGroupId, setCorrectionsGroupId] = useState("");
  // Source tab state
  const [sourceGroups, setSourceGroups] = useState([]);
  const [defaultSource, setDefaultSource] = useState("guap");
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceSaving, setSourceSaving] = useState(false);
  const [sourceError, setSourceError] = useState(null);

  const loadGroups = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`);
      setGroups(Array.isArray(data.groups) ? data.groups : []);
      setManualGroups(Array.isArray(data.manual_groups) ? data.manual_groups : []);
      setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
    } catch (err) {
      setError(err.message || t("schedule.errors.loadGroupsFailed"));
    } finally {
      setLoading(false);
    }
  }, [courseId, t]);

  useEffect(() => {
    loadGroups();
  }, [loadGroups]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    setRefreshResult(null);
    try {
      const data = await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/refresh-all`,
        { method: "POST" }
      );
      setRefreshResult(data);
      setWarnings(data.warnings || []);
      await loadGroups();
    } catch (err) {
      setError(err.message || t("schedule.errors.refreshFailed"));
    } finally {
      setRefreshing(false);
    }
  }, [courseId, t, loadGroups]);

  const handleAddManualGroup = async (newGroup) => {
    const updatedGroups = [...new Set([...manualGroups, newGroup.trim()].filter(Boolean))];
    try {
      await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ groups: updatedGroups }),
        }
      );
      await loadGroups();
    } catch (err) {
      setError(err.message || t("schedule.errors.saveGroupsFailed"));
    }
  };

  const handleRemoveManualGroup = async (groupId) => {
    try {
      await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/groups`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ groups: manualGroups.filter((id) => id !== groupId) }),
        }
      );
      await loadGroups();
    } catch (err) {
      setError(err.message || t("schedule.errors.saveGroupsFailed"));
    }
  };

  const handleCorrectionsSaved = useCallback(() => {
    loadGroups();
  }, [loadGroups]);

  const loadSources = useCallback(async () => {
    if (!courseId) return;
    setSourceLoading(true);
    setSourceError(null);
    try {
      const scheduleYaml = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`);
      const parsed = load(scheduleYaml.content);
      
      const groupList = parsed.groups ? Object.entries(parsed.groups).map(([id, cfg]) => ({
        id,
        source: cfg.source || parsed.source || "guap",
        guap_group_id: cfg.guap_group_id || id,
      })) : [];
      
      setSourceGroups(groupList);
      setDefaultSource(parsed.source || "guap");
    } catch (err) {
      setSourceError(err.message || t("schedule.source.errors.loadFailed"));
    } finally {
      setSourceLoading(false);
    }
  }, [courseId, t]);

  useEffect(() => {
    if (tab === 2) loadSources();
  }, [tab, loadSources]);

  const handleSourceChange = (groupId, newSource) => {
    setSourceGroups(prev => prev.map(g => g.id === groupId ? { ...g, source: newSource } : g));
  };

  const handleDefaultSourceChange = (newSource) => {
    setDefaultSource(newSource);
  };

  const handleSourceSave = async () => {
    setSourceSaving(true);
    setSourceError(null);
    try {
      const scheduleYaml = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`);
      const parsed = load(scheduleYaml.content);
      
      if (!parsed.groups) parsed.groups = {};
      
      parsed.source = defaultSource;
      
      sourceGroups.forEach(g => {
        if (parsed.groups[g.id]) {
          parsed.groups[g.id].source = g.source;
        }
      });
      
      const newContent = dump(parsed, { lineWidth: -1, noRefs: true });
      
      await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: newContent }),
        }
      );
      
      await loadGroups();
    } catch (err) {
      setSourceError(err.message || t("schedule.source.errors.saveFailed"));
    } finally {
      setSourceSaving(false);
    }
  };

  const refreshedCount = refreshResult ? Object.keys(refreshResult.refreshed || {}).length : 0;
  const refreshErrorCount = refreshResult ? Object.keys(refreshResult.errors || {}).length : 0;

  const handleBack = () => {
    navigate(`/admin/courses`);
  };

  if (loading) {
    return (
      <Container maxWidth="xl">
        <Toolbar />
        <Typography variant="h5" gutterBottom>{t("schedule.title")}</Typography>
        <Alert severity="info">{t("schedule.loading")}</Alert>
      </Container>
    );
  }

  return (
    <Container maxWidth="xl">
      <Toolbar />
      <Stack direction="row" spacing={2} alignItems="center" justifyContent="space-between" sx={{ mb: 2, flexWrap: "wrap" }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Tooltip title={t("back")}>
            <IconButton onClick={handleBack}><ArrowBackIcon /></IconButton>
          </Tooltip>
          <Typography variant="h5" gutterBottom>{t("schedule.title", { courseId })}</Typography>
        </Stack>
        <Stack direction="row" spacing={1}>
          <Tooltip title={t("schedule.refreshButton")}>
            <Button variant="contained" startIcon={<RefreshIcon />} onClick={handleRefresh} disabled={refreshing}>
              {t("schedule.refreshButton")}
            </Button>
          </Tooltip>
        </Stack>
      </Stack>

      {error && <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>{error}</Alert>}
      {warnings.map((w) => <Alert key={w} severity="warning" sx={{ mb: 1 }}>{w}</Alert>)}

      {refreshResult && (
        <Alert
          severity={
            refreshErrorCount === 0 && warnings.length === 0
              ? "success"
              : refreshedCount === 0
                ? "error"
                : "warning"
          }
          sx={{ mb: 2 }}
        >
          {t("schedule.refreshedOk", { ok: refreshedCount, err: refreshErrorCount })}
        </Alert>
      )}
      {refreshing && <Alert severity="info" sx={{ mb: 2 }}>{t("schedule.refreshing")}</Alert>}

      <Paper elevation={1} sx={{ p: 2 }}>
        <TabContext value={tab}>
          <TabList
            onChange={(e, v) => setTab(v)}
            aria-label="Schedule tabs"
            sx={{ borderBottom: 1, borderColor: "divider", mb: 2 }}
          >
            <Tab value={0} label={t("schedule.editor.title")} />
            <Tab value={1} label={t("schedule.corrections.listTitle")} />
            <Tab value={2} label={t("schedule.source.title")} />
          </TabList>

          <TabPanel value={0}>
            <ScheduleEditor courseId={courseId} embedded={true} />
          </TabPanel>

          <TabPanel value={1}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
              <Typography variant="h6">{t("schedule.corrections.listTitle")}</Typography>
              <TextField
                placeholder={t("schedule.manualGroupInput")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && e.target.value.trim()) {
                    handleAddManualGroup(e.target.value.trim());
                    e.target.value = "";
                  }
                }}
                size="small"
                sx={{ minWidth: 200 }}
              />
            </Stack>

            {manualGroups.length > 0 && (
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
                {manualGroups.map((id) => (
                  <Tooltip title={t("schedule.corrections.addButton")} key={id}>
                    <Button
                      variant="outlined"
                      startIcon={<span>+</span>}
                      onClick={() => {
                        setCorrectionsGroupId(id);
                        setCorrectionsOpen(true);
                      }}
                    >
                      {id}
                    </Button>
                  </Tooltip>
                ))}
              </Stack>
            )}

            {groups.map((group) => (
              <Paper key={group.id} elevation={1} sx={{ p: 2, mb: 2 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1, flexWrap: "wrap" }}>
                  <Typography variant="h6">{t("schedule.columns.group")}: {group.id}</Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => {
                      setCorrectionsGroupId(group.id);
                      setCorrectionsOpen(true);
                    }}
                  >
                    {t("schedule.corrections.addButton")}
                  </Button>
                </Stack>
              </Paper>
            ))}

            {groups.length === 0 && manualGroups.length === 0 && (
              <Alert severity="info">{t("schedule.corrections.empty")}</Alert>
            )}
          </TabPanel>

          <TabPanel value={2}>
            {sourceLoading ? (
              <Typography variant="body2" color="text.secondary">{t("schedule.source.loading")}</Typography>
            ) : (
              <Stack spacing={2} sx={{ pt: 1 }}>
                {sourceError && <Alert severity="error" onClose={() => setSourceError(null)} sx={{ mb: 1 }}>{sourceError}</Alert>}
                
                <Typography variant="h6" gutterBottom>{t("schedule.source.defaultSource")}</Typography>
                <FormControl fullWidth sx={{ maxWidth: 300 }}>
                  <InputLabel id="default-source-label">{t("schedule.source.defaultSourceLabel")}</InputLabel>
                  <Select
                    labelId="default-source-label"
                    value={defaultSource}
                    label={t("schedule.source.defaultSourceLabel")}
                    onChange={e => handleDefaultSourceChange(e.target.value)}
                  >
                    <MenuItem value="guap">{t("schedule.source.types.guap")}</MenuItem>
                    <MenuItem value="manual">{t("schedule.source.types.manual")}</MenuItem>
                  </Select>
                  <FormHelperText>{t("schedule.source.defaultSourceHint")}</FormHelperText>
                </FormControl>
                
                <Typography variant="h6" gutterBottom>{t("schedule.source.groupSources")}</Typography>
                {sourceGroups.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">{t("schedule.source.empty")}</Typography>
                ) : (
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>{t("schedule.source.columns.group")}</TableCell>
                        <TableCell>{t("schedule.source.columns.currentSource")}</TableCell>
                        <TableCell>{t("schedule.source.columns.guapGroupId")}</TableCell>
                        <TableCell>{t("schedule.source.columns.newSource")}</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {sourceGroups.map((group) => (
                        <TableRow key={group.id}>
                          <TableCell>{group.id}</TableCell>
                          <TableCell>
                            <span style={{
                              background: group.source === "guap" ? "#e3f2fd" : "#fff3e0",
                              padding: "2px 8px",
                              borderRadius: 4,
                              fontSize: "0.85em"
                            }}>
                              {group.source === "guap" ? t("schedule.source.types.guap") : t("schedule.source.types.manual")}
                            </span>
                          </TableCell>
                          <TableCell>{group.guap_group_id}</TableCell>
                          <TableCell>
                            <FormControl sx={{ minWidth: 150 }}>
                              <Select
                                value={group.source}
                                label={t("schedule.source.columns.newSource")}
                                onChange={e => handleSourceChange(group.id, e.target.value)}
                              >
                                <MenuItem value="guap">{t("schedule.source.types.guap")}</MenuItem>
                                <MenuItem value="manual">{t("schedule.source.types.manual")}</MenuItem>
                              </Select>
                            </FormControl>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
                
                <Alert severity="info" sx={{ mt: 2 }}>
                  {t("schedule.source.hint")}
                </Alert>
                
                <Stack direction="row" justifyContent="flex-end" sx={{ mt: 2 }}>
                  <Button variant="contained" onClick={handleSourceSave} disabled={sourceSaving}>
                    {sourceSaving ? t("schedule.source.saving") : t("schedule.source.saveChanges")}
                  </Button>
                </Stack>
              </Stack>
            )}
          </TabPanel>
        </TabContext>
      </Paper>

      <CorrectionsDialog
        courseId={courseId}
        groupId={correctionsGroupId}
        open={correctionsOpen}
        onClose={() => setCorrectionsOpen(false)}
        onSaved={() => loadGroups()}
      />
    </Container>
  );
};

export default SchedulePage;