import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Snackbar,
  Stack,
  Typography,
} from "@mui/material";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";

async function fetchJson(url, options) {
  const response = await fetch(url, { credentials: "include", ...options });
  let data = null;
  try {
    data = await response.json();
  } catch {
  }
  if (!response.ok) {
    const error = new Error(
      data?.detail || (typeof data === "string" ? data : `HTTP ${response.status}`)
    );
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

export const ScheduleEditor = ({ courseId, open, onClose }) => {
  const { t } = useTranslation();
  const [content, setContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: "", severity: "info" });
  const editorRef = useRef(null);

  const loadContent = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`);
      setContent(data.content || "");
      setOriginalContent(data.content || "");
    } catch (err) {
      setError(err.message || t("schedule.editor.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [courseId, t]);

  const handleSave = useCallback(async () => {
    if (!courseId) return;
    setSaving(true);
    setError(null);
    try {
      await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        }
      );
      setOriginalContent(content);
      setSnackbar({ open: true, message: t("schedule.editor.saved"), severity: "success" });
    } catch (err) {
      setError(err.message || t("schedule.editor.saveFailed"));
    } finally {
      setSaving(false);
    }
  }, [courseId, content, t]);

  const handleCancel = useCallback(() => {
    setContent(originalContent);
    setError(null);
    onClose();
  }, [originalContent, onClose]);

  const showSnackbar = (message, severity) => {
    setSnackbar({ open: true, message, severity });
  };

  useEffect(() => {
    if (open) {
      loadContent();
    }
  }, [open, loadContent]);

  const handleSnackbarClose = (event, reason) => {
    if (reason === "clickaway") return;
    setSnackbar({ ...snackbar, open: false });
  };

  if (!open) return null;

  return (
    <Dialog open={open} onClose={handleCancel} fullWidth maxWidth="xl" style={{ maxWidth: "1200px" }}>
      <DialogTitle>
        {t("schedule.editor.title")}
        {loading && <Typography variant="caption" style={{ marginLeft: "16px", color: "#888" }}>{t("schedule.editor.loading")}</Typography>}
      </DialogTitle>
      <DialogContent style={{ padding: 0, maxHeight: "85vh", overflow: "auto" }}>
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2, mb: 1 }}>
            {error}
          </Alert>
        )}
        <CodeMirror
          ref={editorRef}
          value={content}
          onChange={setContent}
          extensions={[yaml()]}
          height="70vh"
          minHeight="400px"
          style={{
            width: "100%",
            fontSize: "13px",
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
            border: "none",
            borderRadius: 0,
            backgroundColor: "#fff",
          }}
          placeholder={t("schedule.editor.placeholder")}
          basicSetup={{
            lineNumbers: true,
            highlightActiveLine: true,
            tabSize: 2,
            indentWithTab: false,
          }}
        />
      </DialogContent>
      <DialogActions style={{ padding: "16px 24px", borderTop: "1px solid #e0e0e0" }}>
        <Button onClick={handleCancel} disabled={saving}>
          {t("cancel")}
        </Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={saving || content === originalContent}
          style={{ marginLeft: "auto" }}
        >
          {saving ? t("schedule.editor.saving") : t("save")}
        </Button>
      </DialogActions>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={handleSnackbarClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      >
        <Alert severity={snackbar.severity} sx={{ width: "100%" }}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Dialog>
  );
};

export default ScheduleEditor;