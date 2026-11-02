import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemButton,
  ListItemText,
} from "@mui/material";

// История версий файла курса (docs/COURSE_STORAGE_PLAN.md §11): список
// версий, просмотр версии только для чтения, восстановление с подтверждением.
// Восстановление - новая версия поверх той, что была при открытии окна:
// если курс за это время изменили, backend ответит 409.

async function fetchJson(url, options) {
  const response = await fetch(url, { credentials: "include", ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error((data && data.detail) || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

const formatDate = (iso, language) => {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(language);
  } catch {
    return iso;
  }
};

export const CourseHistoryDialog = ({ courseId, onClose, onRestored, onError }) => {
  const { t, i18n } = useTranslation();
  const [revisions, setRevisions] = useState(null);
  const [unavailable, setUnavailable] = useState(false);
  const [currentVersion, setCurrentVersion] = useState(null);
  const [selected, setSelected] = useState(null); // {sha, content}
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const base = `/api/v1/admin/courses/${encodeURIComponent(courseId)}`;

  useEffect(() => {
    let current = true;
    Promise.all([
      fetchJson(`${base}/history`),
      fetchJson(`/api/v1/courses/${encodeURIComponent(courseId)}/edit`),
    ])
      .then(([history, edit]) => {
        if (!current) return;
        setRevisions(history);
        setCurrentVersion(edit.version);
      })
      .catch((err) => {
        if (!current) return;
        if (err.status === 404) {
          setUnavailable(true);
        } else {
          onError(err.status === 503 ? t("courseHistory.storeUnavailable") : err.message);
          onClose();
        }
      });
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId]);

  const openRevision = (sha) => {
    setBusy(true);
    fetchJson(`${base}/history/${sha}`)
      .then((data) => setSelected({ sha, content: data.content }))
      .catch((err) => onError(err.message))
      .finally(() => setBusy(false));
  };

  const restore = () => {
    setBusy(true);
    fetchJson(`${base}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sha: selected.sha, version: currentVersion }),
    })
      .then(() => onRestored())
      .catch((err) => {
        setConfirming(false);
        onError(err.status === 503 ? t("courseHistory.storeUnavailable") : err.message);
      })
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {selected
          ? t("courseHistory.versionTitle", { sha: selected.sha.slice(0, 7) })
          : t("courseHistory.title")}
      </DialogTitle>
      <DialogContent dividers>
        {unavailable && <p>{t("courseHistory.unavailable")}</p>}
        {!unavailable && revisions === null && <CircularProgress size={24} />}

        {!unavailable && revisions && !selected && (
          revisions.length === 0 ? (
            <p>{t("courseHistory.empty")}</p>
          ) : (
            <List dense>
              {revisions.map((revision, index) => (
                <ListItemButton
                  key={revision.sha}
                  disabled={busy}
                  onClick={() => openRevision(revision.sha)}
                >
                  <ListItemText
                    primary={`${revision.message.split("\n")[0]}${index === 0 ? ` · ${t("courseHistory.current")}` : ""}`}
                    secondary={`${formatDate(revision.date, i18n.language)} · ${revision.author} · ${revision.sha.slice(0, 7)}`}
                  />
                </ListItemButton>
              ))}
            </List>
          )
        )}

        {selected && (
          <CodeMirror
            value={selected.content}
            extensions={[yaml()]}
            editable={false}
            readOnly
            height="50vh"
            style={{ border: "1px solid #ddd", borderRadius: "8px", fontSize: "14px" }}
          />
        )}

        {confirming && <p style={{ marginTop: 12 }}>{t("courseHistory.confirmRestore")}</p>}
      </DialogContent>
      <DialogActions>
        {selected && !confirming && (
          <>
            <Button onClick={() => setSelected(null)} disabled={busy}>
              {t("courseHistory.back")}
            </Button>
            <Button
              onClick={() => setConfirming(true)}
              disabled={busy || !currentVersion}
              variant="contained"
            >
              {t("courseHistory.restore")}
            </Button>
          </>
        )}
        {confirming && (
          <>
            <Button onClick={() => setConfirming(false)} disabled={busy}>
              {t("cancel")}
            </Button>
            <Button onClick={restore} disabled={busy} variant="contained" color="warning">
              {t("courseHistory.restoreConfirmButton")}
            </Button>
          </>
        )}
        <Button onClick={onClose} disabled={busy}>
          {t("courseHistory.close")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
