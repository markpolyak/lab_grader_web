import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { load, dump } from "js-yaml";

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

export const SourceDialog = ({ courseId, open, onClose, onSaved }) => {
  const { t } = useTranslation();
  const [groups, setGroups] = useState([]);
  const [defaultSource, setDefaultSource] = useState("guap");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const loadSources = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    try {
      const scheduleYaml = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`);
      const parsed = load(scheduleYaml.content);
      
      const groupList = parsed.groups ? Object.entries(parsed.groups).map(([id, cfg]) => ({
        id,
        source: cfg.source || parsed.source || "guap",
        guap_group_id: cfg.guap_group_id || id,
      })) : [];
      
      setGroups(groupList);
      setDefaultSource(parsed.source || "guap");
    } catch (err) {
      setError(err.message || t("schedule.source.errors.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [courseId, t]);

  useEffect(() => {
    if (open) loadSources();
  }, [open, loadSources]);

  const handleSourceChange = (groupId, newSource) => {
    setGroups(prev => prev.map(g => g.id === groupId ? { ...g, source: newSource } : g));
  };

  const handleDefaultSourceChange = (newSource) => {
    setDefaultSource(newSource);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const scheduleYaml = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`);
      const parsed = load(scheduleYaml.content);
      
      if (!parsed.groups) parsed.groups = {};
      
      // Update course-level source
      parsed.source = defaultSource;
      
      // Update group sources
      groups.forEach(g => {
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
      
      if (onSaved) onSaved();
      onClose();
    } catch (err) {
      setError(err.message || t("schedule.source.errors.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="lg">
      <DialogTitle>{t("schedule.source.title")}</DialogTitle>
      <DialogContent style={{ maxHeight: "80vh", overflow: "auto" }}>
        {error && <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2, mb: 1 }}>{error}</Alert>}
        
        <Stack spacing={2} sx={{ p: 2, mt: 1 }}>
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
          {loading ? (
            <Typography variant="body2" color="text.secondary">{t("schedule.source.loading")}</Typography>
          ) : groups.length === 0 ? (
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
                {groups.map((group) => (
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
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("cancel")}</Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? t("schedule.source.saving") : t("schedule.source.saveChanges")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default SourceDialog;