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
import { linter, lintGutter } from "@codemirror/lint";
import { load } from "js-yaml";

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

const REQUIRED_TOP_KEYS = ["course-id", "source", "timezone", "groups", "manual-groups"];
const VALID_SOURCES = ["guap", "manual"];

function validateYaml(content) {
  const diagnostics = [];
  
  if (!content.trim()) {
    return diagnostics;
  }

  let parsed;
  try {
    parsed = load(content);
  } catch (e) {
    diagnostics.push({
      from: 0,
      to: content.length,
      severity: "error",
      message: `YAML parse error: ${e.message}`,
    });
    return diagnostics;
  }

  if (!parsed || typeof parsed !== "object") {
    diagnostics.push({
      from: 0,
      to: content.length,
      severity: "error",
      message: "Top-level must be a mapping (object)",
    });
    return diagnostics;
  }

  for (const key of REQUIRED_TOP_KEYS) {
    if (!(key in parsed)) {
      diagnostics.push({
        from: 0,
        to: content.indexOf("\n") > 0 ? content.indexOf("\n") : content.length,
        severity: "error",
        message: `Missing required field: ${key}`,
      });
    }
  }

  if (parsed["course-id"] !== undefined) {
    if (!parsed["course-id"] || typeof parsed["course-id"] !== "string") {
      diagnostics.push({
        from: 0,
        to: content.length,
        severity: "error",
        message: "`course-id` must be a non-empty string",
      });
    }
  }

  if (parsed.source !== undefined) {
    if (!VALID_SOURCES.includes(parsed.source)) {
      diagnostics.push({
        from: 0,
        to: content.length,
        severity: "warning",
        message: `Unknown source: ${parsed.source}. Valid: ${VALID_SOURCES.join(", ")}`,
      });
    }
  }

  if (parsed.timezone !== undefined) {
    if (!parsed.timezone || typeof parsed.timezone !== "string") {
      diagnostics.push({
        from: 0,
        to: content.length,
        severity: "warning",
        message: "`timezone` should be a string (e.g., 'UTC+3')",
      });
    }
  }

  if (parsed.groups !== undefined) {
    if (typeof parsed.groups !== "object" || parsed.groups === null) {
      diagnostics.push({
        from: 0,
        to: content.length,
        severity: "error",
        message: "`groups` must be a mapping (object)",
      });
    } else {
      for (const [groupId, groupConfig] of Object.entries(parsed.groups)) {
        if (!/^\d+$/.test(groupId)) {
          diagnostics.push({
            from: 0,
            to: content.length,
            severity: "error",
            message: `Group ID "${groupId}" must contain only digits`,
          });
        }

        if (groupConfig && typeof groupConfig === "object") {
          if (groupConfig.group_id !== undefined && String(groupConfig.group_id) !== groupId) {
            diagnostics.push({
              from: 0,
              to: content.length,
              severity: "error",
              message: `Group "${groupId}": group_id must match key (${groupId})`,
            });
          }

          if (groupConfig.source !== undefined && !VALID_SOURCES.includes(groupConfig.source)) {
            diagnostics.push({
              from: 0,
              to: content.length,
              severity: "warning",
              message: `Group "${groupId}": unknown source "${groupConfig.source}"`,
            });
          }

          if (groupConfig.slots !== undefined) {
            if (!Array.isArray(groupConfig.slots)) {
              diagnostics.push({
                from: 0,
                to: content.length,
                severity: "error",
                message: `Group "${groupId}": slots must be an array`,
              });
            } else {
              const seenIds = new Set();
              for (let i = 0; i < groupConfig.slots.length; i++) {
                const slot = groupConfig.slots[i];
                if (!slot || typeof slot !== "object") continue;

                if (!slot.id) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" slot ${i}: missing required 'id'`,
                  });
                } else if (seenIds.has(slot.id)) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}": duplicate slot id "${slot.id}"`,
                  });
                } else {
                  seenIds.add(slot.id);
                }

                const hasDate = slot.date !== undefined;
                const hasRecurrence = slot.recurrence !== undefined;
                if (!hasDate && !hasRecurrence) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" slot "${slot.id}": must have either 'date' or 'recurrence'`,
                  });
                }
                if (hasDate && hasRecurrence) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" slot "${slot.id}": cannot have both 'date' and 'recurrence'`,
                  });
                }

                // Validate required start/end for all slots
                if (!slot.start) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" slot "${slot.id}": start is required`,
                  });
                }
                if (!slot.end) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" slot "${slot.id}": end is required`,
                  });
                }

                if (slot.recurrence) {
                  if (typeof slot.recurrence !== "object") {
                    diagnostics.push({
                      from: 0,
                      to: content.length,
                      severity: "error",
                      message: `Group "${groupId}" slot "${slot.id}": recurrence must be an object`,
                    });
                  } else {
                    const validDays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mon", "tue", "wed", "thu", "fri", "sat", "sun"];
                    if (slot.recurrence.day && !validDays.includes(slot.recurrence.day)) {
                      diagnostics.push({
                        from: 0,
                        to: content.length,
                        severity: "error",
                        message: `Group "${groupId}" slot "${slot.id}": invalid recurrence day "${slot.recurrence.day}"`,
                      });
                    }
                    const validWeeks = ["all", "odd", "even"];
                    if (slot.recurrence.week && !validWeeks.includes(slot.recurrence.week)) {
                      diagnostics.push({
                        from: 0,
                        to: content.length,
                        severity: "error",
                        message: `Group "${groupId}" slot "${slot.id}": recurrence.week must be 'all', 'odd', or 'even'`,
                      });
                    }
                  }
                }

                const timeRegex = /^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$/;
                if (slot.start && !timeRegex.test(slot.start)) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" slot "${slot.id}": start time must be HH:MM or HH:MM:SS`,
                  });
                }
                if (slot.end && !timeRegex.test(slot.end)) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" slot "${slot.id}": end time must be HH:MM or HH:MM:SS`,
                  });
                }

                if (slot.start && slot.end && timeRegex.test(slot.start) && timeRegex.test(slot.end)) {
                  const [sh, sm] = slot.start.split(":").map(Number);
                  const [eh, em] = slot.end.split(":").map(Number);
                  if (eh * 60 + em <= sh * 60 + sm) {
                    diagnostics.push({
                      from: 0,
                      to: content.length,
                      severity: "error",
                      message: `Group "${groupId}" slot "${slot.id}": end must be after start`,
                    });
                  }
                }

                if (slot.labs !== undefined) {
                  if (!Array.isArray(slot.labs)) {
                    diagnostics.push({
                      from: 0,
                      to: content.length,
                      severity: "error",
                      message: `Group "${groupId}" slot "${slot.id}": labs must be an array or null`,
                    });
                  }
                }
              }
            }
          }

          if (groupConfig.corrections !== undefined) {
            if (!Array.isArray(groupConfig.corrections)) {
              diagnostics.push({
                from: 0,
                to: content.length,
                severity: "error",
                message: `Group "${groupId}": corrections must be an array`,
              });
            } else {
              for (let i = 0; i < groupConfig.corrections.length; i++) {
                const corr = groupConfig.corrections[i];
                if (!corr || typeof corr !== "object") continue;

                if (!corr.id) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" correction ${i}: missing 'id'`,
                  });
                }
                if (!corr.date) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" correction ${i}: missing 'date'`,
                  });
                }
                const dateRegex = /^\d{4}-\d{2}-\d{2}$/;
                if (corr.date && !dateRegex.test(corr.date)) {
                  diagnostics.push({
                    from: 0,
                    to: content.length,
                    severity: "error",
                    message: `Group "${groupId}" correction ${i}: date must be YYYY-MM-DD`,
                  });
                }
                if (corr.remove_slots !== undefined) {
                  if (!Array.isArray(corr.remove_slots)) {
                    diagnostics.push({
                      from: 0,
                      to: content.length,
                      severity: "error",
                      message: `Group "${groupId}" correction ${i}: remove_slots must be an array`,
                    });
                  } else {
                    for (let j = 0; j < corr.remove_slots.length; j++) {
                      const rem = corr.remove_slots[j];
                      if (!rem || typeof rem !== "object") continue;
                      if (!rem.slot_id) {
                        diagnostics.push({
                          from: 0,
                          to: content.length,
                          severity: "error",
                          message: `Group "${groupId}" correction ${i} remove_slots[${j}]: slot_id is required`,
                        });
                      }
                      if (rem.labs !== undefined) {
                        if (!Array.isArray(rem.labs)) {
                          diagnostics.push({
                            from: 0,
                            to: content.length,
                            severity: "error",
                            message: `Group "${groupId}" correction ${i} remove_slots[${j}]: labs must be an array or null`,
                          });
                        }
                      }
                    }
                  }
                }
                if (corr.add_slots !== undefined) {
                  if (!Array.isArray(corr.add_slots)) {
                    diagnostics.push({
                      from: 0,
                      to: content.length,
                      severity: "error",
                      message: `Group "${groupId}" correction ${i}: add_slots must be an array`,
                    });
                  } else {
                    for (let j = 0; j < corr.add_slots.length; j++) {
                      const add = corr.add_slots[j];
                      if (!add || typeof add !== "object") continue;
                      
                      // Validate add_slot as a slot (allow_no_date = true)
                      if (!add.id) {
                        diagnostics.push({
                          from: 0,
                          to: content.length,
                          severity: "error",
                          message: `Group "${groupId}" correction ${i} add_slots[${j}]: id is required`,
                        });
                      }
                      
                      if (!add.start) {
                        diagnostics.push({
                          from: 0,
                          to: content.length,
                          severity: "error",
                          message: `Group "${groupId}" correction ${i} add_slots[${j}]: start is required`,
                        });
                      }
                      if (!add.end) {
                        diagnostics.push({
                          from: 0,
                          to: content.length,
                          severity: "error",
                          message: `Group "${groupId}" correction ${i} add_slots[${j}]: end is required`,
                        });
                      }
                      
                      const timeRegex = /^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$/;
                      if (add.start && !timeRegex.test(add.start)) {
                        diagnostics.push({
                          from: 0,
                          to: content.length,
                          severity: "error",
                          message: `Group "${groupId}" correction ${i} add_slots[${j}]: start time must be HH:MM or HH:MM:SS`,
                        });
                      }
                      if (add.end && !timeRegex.test(add.end)) {
                        diagnostics.push({
                          from: 0,
                          to: content.length,
                          severity: "error",
                          message: `Group "${groupId}" correction ${i} add_slots[${j}]: end time must be HH:MM or HH:MM:SS`,
                        });
                      }
                      if (add.start && add.end && timeRegex.test(add.start) && timeRegex.test(add.end)) {
                        const [sh, sm] = add.start.split(":").map(Number);
                        const [eh, em] = add.end.split(":").map(Number);
                        if (eh * 60 + em <= sh * 60 + sm) {
                          diagnostics.push({
                            from: 0,
                            to: content.length,
                            severity: "error",
                            message: `Group "${groupId}" correction ${i} add_slots[${j}]: end must be after start`,
                          });
                        }
                      }
                      
                      if (add.recurrence) {
                        if (typeof add.recurrence !== "object") {
                          diagnostics.push({
                            from: 0,
                            to: content.length,
                            severity: "error",
                            message: `Group "${groupId}" correction ${i} add_slots[${j}]: recurrence must be an object`,
                          });
                        } else {
                          const validDays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mon", "tue", "wed", "thu", "fri", "sat", "sun"];
                          if (add.recurrence.day && !validDays.includes(add.recurrence.day)) {
                            diagnostics.push({
                              from: 0,
                              to: content.length,
                              severity: "error",
                              message: `Group "${groupId}" correction ${i} add_slots[${j}]: invalid recurrence day "${add.recurrence.day}"`,
                            });
                          }
                          const validWeeks = ["all", "odd", "even"];
                          if (add.recurrence.week && !validWeeks.includes(add.recurrence.week)) {
                            diagnostics.push({
                              from: 0,
                              to: content.length,
                              severity: "error",
                              message: `Group "${groupId}" correction ${i} add_slots[${j}]: recurrence.week must be 'all', 'odd', or 'even'`,
                            });
                          }
                        }
                      }
                      
                      if (add.labs !== undefined) {
                        if (!Array.isArray(add.labs)) {
                          diagnostics.push({
                            from: 0,
                            to: content.length,
                            severity: "error",
                            message: `Group "${groupId}" correction ${i} add_slots[${j}]: labs must be an array or null`,
                          });
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  if (parsed["manual-groups"] !== undefined) {
    if (!Array.isArray(parsed["manual-groups"])) {
      diagnostics.push({
        from: 0,
        to: content.length,
        severity: "error",
        message: "`manual-groups` must be an array",
      });
    }
  }

  return diagnostics;
}

const yamlLinter = linter((view) => {
  const content = view.state.doc.toString();
  return validateYaml(content);
});

const ScheduleEditorContent = ({ courseId, onSaved, onCancel, embedded = false }) => {
  const { t } = useTranslation();
  const [content, setContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasLintErrors, setHasLintErrors] = useState(false);
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
      if (onSaved) onSaved();
    } catch (err) {
      setError(err.message || t("schedule.editor.saveFailed"));
    } finally {
      setSaving(false);
    }
  }, [courseId, content, t]);

  const handleCancel = useCallback(() => {
    setContent(originalContent);
    setError(null);
    if (onCancel) onCancel();
  }, [originalContent, onCancel]);

  const showSnackbar = (message, severity) => {
    setSnackbar({ open: true, message, severity });
  };

  useEffect(() => {
    loadContent();
  }, [loadContent]);

  // Clear backend error when user modifies content (fixing the YAML)
  useEffect(() => {
    if (error && content !== originalContent) {
      setError(null);
    }
  }, [content, originalContent, error]);

  // Check for lint errors on content change
  useEffect(() => {
    const diagnostics = validateYaml(content);
    setHasLintErrors(diagnostics.some(d => d.severity === "error"));
  }, [content]);

  const handleSnackbarClose = (event, reason) => {
    if (reason === "clickaway") return;
    setSnackbar({ ...snackbar, open: false });
  };

  const renderEditor = () => (
    <CodeMirror
      ref={editorRef}
      value={content}
      onChange={setContent}
      extensions={[
        yaml(),
        yamlLinter,
        lintGutter(),
      ]}
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
  );

  const headerActions = (
    <>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2, mb: 1 }}>
          {error}
        </Alert>
      )}
      {renderEditor()}
    </>
  );

  const footerActions = (
    <DialogActions style={{ padding: "16px 24px", borderTop: "1px solid #e0e0e0" }}>
      <Button onClick={handleCancel} disabled={saving}>
        {t("cancel")}
      </Button>
      <Button
        variant="contained"
        onClick={handleSave}
        disabled={saving || (!embedded && content === originalContent)}
        style={{ marginLeft: "auto" }}
      >
        {saving ? t("schedule.editor.saving") : t("save")}
      </Button>
    </DialogActions>
  );

  if (embedded) {
    return (
      <Stack direction="column" style={{ height: "100%", minHeight: "500px" }}>
        <Stack direction="row" spacing={2} alignItems="center" justifyContent="space-between" sx={{ mb: 2, px: 2, pt: 2 }}>
          <Typography variant="h6">{t("schedule.editor.title")}</Typography>
          <Stack direction="row" spacing={1} sx={{ ml: "auto" }}>
            <Button onClick={handleCancel} disabled={saving} variant="outlined">
              {t("cancel")}
            </Button>
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={saving || hasLintErrors || (!embedded && content === originalContent)}
            >
              {saving ? t("schedule.editor.saving") : t("save")}
            </Button>
          </Stack>
        </Stack>
        <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          {error && <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2, mb: 1 }}>{error}</Alert>}
          <div style={{ flex: 1, minHeight: 400 }}>
            {renderEditor()}
          </div>
        </div>
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
      </Stack>
    );
  }

  if (!open) return null;

  return (
    <Dialog open={open} onClose={handleCancel} fullWidth maxWidth="xl" style={{ maxWidth: "1200px" }}>
      <DialogTitle>
        {t("schedule.editor.title")}
        {loading && <Typography variant="caption" style={{ marginLeft: "16px", color: "#888" }}>{t("schedule.editor.loading")}</Typography>}
      </DialogTitle>
      <DialogContent style={{ padding: 0, maxHeight: "85vh", overflow: "auto" }}>
        {headerActions}
      </DialogContent>
      {footerActions}
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

export const ScheduleEditor = ({ courseId, open, onClose, embedded = false, onSaved }) => {
  return <ScheduleEditorContent courseId={courseId} open={open} onClose={onClose} embedded={embedded} onSaved={onSaved} />;
};

export default ScheduleEditor;