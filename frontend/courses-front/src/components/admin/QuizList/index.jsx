import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Snackbar,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
  TextField,
  Checkbox,
  FormControlLabel,
} from "@mui/material";
import { QuizMarkdown } from "../../quiz-markdown";
import { BackButton, Container, PageTitle, Panel, TableWrapper } from "../LabList/styled";
import { nextSort, readSortCookie, sortCookie, sortQuizzes } from "./sort";

// Квизы курса (docs/QUIZ_PLAN.md §10.2): список, загрузка банка вопросов с
// параметрами, предпросмотр с правильными ответами, удаление, запуск.

const LETTERS = "ABCD";

async function api(url, options) {
  const response = await fetch(url, { credentials: "include", ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error((data && (data.message || data.detail)) || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

// Значения формы из front matter файла, если он уже есть (§3.2).
function readFrontMatter(text) {
  const match = text.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---/);
  const values = {};
  for (const line of match ? match[1].split(/\r?\n/) : []) {
    const m = line.match(/^([a-z-]+):\s*(.*?)\s*(#.*)?$/);
    if (m) values[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
  const heading = text.match(/^#\s+(.+)$/m);
  if (!values.title && heading) values.title = heading[1].trim();
  return values;
}

const EMPTY_FORM = {
  title: "",
  "short-name": "",
  "max-points": "",
  "seconds-per-question": "20",
  "reveal-seconds": "10",
  "variants-per-student": false,
};

// The edit form from a quiz summary (GET .../quizzes).
const formOf = (quiz) => ({
  title: quiz.title,
  "short-name": quiz.short_name,
  "max-points": quiz.max_points,
  "seconds-per-question": String(quiz.seconds_per_question),
  "reveal-seconds": String(quiz.reveal_seconds),
  "variants-per-student": !!quiz.variants_per_student,
});

export const QuizList = () => {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const base = `/api/v1/admin/courses/${encodeURIComponent(courseId)}/quizzes`;

  const [quizzes, setQuizzes] = useState(null);
  const [file, setFile] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [uploadErrors, setUploadErrors] = useState([]);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const [toDelete, setToDelete] = useState(null);
  const [toRun, setToRun] = useState(null); // quiz summary
  const [editing, setEditing] = useState(null); // quiz summary
  const [editForm, setEditForm] = useState(EMPTY_FORM);
  const [editErrors, setEditErrors] = useState([]);
  const [inRoom, setInRoom] = useState(false);
  const [leaderboard, setLeaderboard] = useState(true);
  const [snackbar, setSnackbar] = useState(null);
  // Chosen column and direction, remembered in a cookie for the next visit.
  const [sort, setSort] = useState(() => readSortCookie(document.cookie));
  const sortedQuizzes = useMemo(() => quizzes && sortQuizzes(quizzes, sort), [quizzes, sort]);

  const changeSort = (column) => {
    const next = nextSort(sort, column);
    setSort(next);
    document.cookie = sortCookie(next);
  };

  const sortableHead = (column, label) => (
    <TableCell sortDirection={sort && sort.column === column ? sort.direction : false}>
      <TableSortLabel
        active={!!sort && sort.column === column}
        direction={sort && sort.column === column ? sort.direction : "asc"}
        onClick={() => changeSort(column)}
      >
        {label}
      </TableSortLabel>
    </TableCell>
  );

  const load = useCallback(() => {
    api(base)
      .then(setQuizzes)
      .catch((err) =>
        setSnackbar({
          severity: "error",
          message: err.status === 404 ? t("adminAuth.courseNotFoundOrNoAccess") : err.message,
        })
      );
  }, [base, t]);

  useEffect(load, [load]);

  const pickFile = async (event) => {
    const picked = event.target.files[0];
    event.target.value = "";
    if (!picked) return;
    setFile(picked);
    setUploadErrors([]);
    const found = readFrontMatter(await picked.text());
    setForm({
      ...EMPTY_FORM,
      ...found,
      "variants-per-student": String(found["variants-per-student"] || "").toLowerCase() === "true",
    });
  };

  const upload = async () => {
    setBusy(true);
    setUploadErrors([]);
    const body = new FormData();
    body.append("file", file);
    Object.entries(form).forEach(([key, value]) => {
      if (typeof value === "boolean") {
        if (value) body.append(key, "true");
      } else {
        body.append(key, value);
      }
    });
    try {
      await api(base, { method: "POST", body });
      setFile(null);
      setForm(EMPTY_FORM);
      setSnackbar({ severity: "success", message: t("quizAdmin.uploaded") });
      load();
    } catch (err) {
      setUploadErrors(err.data && err.data.errors ? err.data.errors : [err.message]);
    } finally {
      setBusy(false);
    }
  };

  const openPreview = (quizId) =>
    api(`${base}/${encodeURIComponent(quizId)}`)
      .then(setPreview)
      .catch((err) => setSnackbar({ severity: "error", message: err.message }));

  const remove = () =>
    api(`${base}/${encodeURIComponent(toDelete)}`, { method: "DELETE" })
      .then(() => {
        setToDelete(null);
        load();
      })
      .catch((err) => setSnackbar({ severity: "error", message: err.message }));

  const run = async (quizId) => {
    setToRun(null);
    try {
      const data = await api(`${base}/${encodeURIComponent(quizId)}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ in_room: inRoom, leaderboard }),
      });
      navigate(`/admin/quiz-runs/${data.run_id}`);
    } catch (err) {
      if (err.status === 409 && err.data && err.data.run_id) {
        navigate(`/admin/quiz-runs/${err.data.run_id}`);
      } else {
        setSnackbar({ severity: "error", message: err.message });
      }
    }
  };

  // The parameter fields, shared by the upload form and the edit dialog.
  const settingsFields = (values, setValues) => {
    const f = (name, label, props = {}) => (
      <TextField
        size="small"
        label={label}
        value={values[name]}
        onChange={(e) => setValues((prev) => ({ ...prev, [name]: e.target.value }))}
        {...props}
      />
    );
    return (
      <>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
          {f("title", t("quizAdmin.fields.title"), { style: { minWidth: 240, flex: "2 1 240px" } })}
          {f("short-name", t("quizAdmin.fields.shortName"), {
            helperText: t("quizAdmin.fields.shortNameHint"),
            style: { flex: "1 1 160px" },
          })}
          {f("max-points", t("quizAdmin.fields.maxPoints"), { style: { flex: "1 1 120px" } })}
          {f("seconds-per-question", t("quizAdmin.fields.seconds"), { type: "number", style: { flex: "1 1 120px" } })}
          {f("reveal-seconds", t("quizAdmin.fields.revealSeconds"), {
            type: "number",
            helperText: t("quizAdmin.fields.revealSecondsHint"),
            style: { flex: "1 1 160px" },
          })}
        </div>
        <FormControlLabel
          control={
            <Checkbox
              checked={!!values["variants-per-student"]}
              onChange={(e) => setValues((prev) => ({ ...prev, "variants-per-student": e.target.checked }))}
            />
          }
          label={t("quizAdmin.fields.variantsPerStudent")}
        />
      </>
    );
  };

  const saveEdit = async () => {
    setEditErrors([]);
    try {
      await api(`${base}/${encodeURIComponent(editing.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...editForm, version: editing.version }),
      });
      setEditing(null);
      setSnackbar({ severity: "success", message: t("quizAdmin.saved") });
      load();
    } catch (err) {
      setEditErrors(
        err.status === 409
          ? [t("courseStore.conflict")]
          : err.data && err.data.errors
            ? err.data.errors
            : [err.message]
      );
    }
  };

  return (
    <Container>
      <Panel>
        <BackButton onClick={() => navigate(`/admin/courses/${courseId}/labs`)}>
          {t("quizAdmin.back")}
        </BackButton>
        <PageTitle>{t("quizAdmin.title")}</PageTitle>

        <div style={{ display: "flex", flexDirection: "column", gap: 12, margin: "8px 0 24px" }}>
          <div>
            <Button variant="outlined" component="label">
              {t("quizAdmin.chooseFile")}
              <input type="file" accept=".md,text/markdown" hidden onChange={pickFile} />
            </Button>{" "}
            {file && <span>{file.name}</span>}
          </div>
          {file && (
            <>
              {settingsFields(form, setForm)}
              {uploadErrors.length > 0 && (
                <Alert severity="error">
                  {uploadErrors.map((e) => (
                    <div key={e}>{e}</div>
                  ))}
                </Alert>
              )}
              <div>
                <Button variant="contained" onClick={upload} disabled={busy}>
                  {t("quizAdmin.upload")}
                </Button>
              </div>
            </>
          )}
        </div>

        {quizzes && quizzes.length === 0 && <p>{t("quizAdmin.empty")}</p>}
        {quizzes && quizzes.length > 0 && (
          <TableWrapper>
            <Table size="small">
              <TableHead>
                <TableRow>
                  {sortableHead("title", t("quizAdmin.columns.title"))}
                  {sortableHead("short_name", t("quizAdmin.columns.shortName"))}
                  {sortableHead("questions", t("quizAdmin.columns.questions"))}
                  {sortableHead("max_points", t("quizAdmin.columns.points"))}
                  {sortableHead("seconds_per_question", t("quizAdmin.columns.seconds"))}
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {sortedQuizzes.map((quiz) => (
                  <TableRow key={quiz.id}>
                    <TableCell>
                      {quiz.title || quiz.id}
                      {quiz.variants_per_student && (
                        <div style={{ color: "#777", fontSize: 12 }}>
                          {quiz.max_variants > 1
                            ? t("quizAdmin.variantsPerStudentMark", { count: quiz.max_variants })
                            : t("quizAdmin.variantsPerStudentNoVariants")}
                        </div>
                      )}
                      {quiz.error && <Alert severity="error">{quiz.error}</Alert>}
                    </TableCell>
                    <TableCell>{quiz.short_name}</TableCell>
                    <TableCell>{quiz.questions}</TableCell>
                    <TableCell>{quiz.max_points}</TableCell>
                    <TableCell>{quiz.seconds_per_question}</TableCell>
                    <TableCell style={{ whiteSpace: "nowrap" }}>
                      {!quiz.error && (
                        <>
                          <Button size="small" variant="contained" onClick={() => {
                              setInRoom(false);
                              setLeaderboard(true);
                              setToRun(quiz);
                            }}>
                            {t("quizAdmin.run")}
                          </Button>{" "}
                          <Button size="small" onClick={() => openPreview(quiz.id)}>
                            {t("quizAdmin.preview")}
                          </Button>{" "}
                        </>
                      )}
                      {!quiz.error && (
                        <>
                          <Button
                            size="small"
                            onClick={() => {
                              setEditForm(formOf(quiz));
                              setEditErrors([]);
                              setEditing(quiz);
                            }}
                          >
                            {t("quizAdmin.edit")}
                          </Button>{" "}
                        </>
                      )}
                      <Button size="small" color="error" onClick={() => setToDelete(quiz.id)}>
                        {t("quizAdmin.delete")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableWrapper>
        )}
      </Panel>

      <Dialog open={!!preview} onClose={() => setPreview(null)} maxWidth="md" fullWidth>
        <DialogTitle>{preview && preview.title}</DialogTitle>
        <DialogContent dividers>
          {preview &&
            preview.items.map((question, qi) => (
              <div key={qi} style={{ marginBottom: 24 }}>
                <h3 style={{ margin: "0 0 4px" }}>
                  {question.label} · {t("quizAdmin.points", { points: question.points })}
                </h3>
                {question.subtitle && <div style={{ color: "#777" }}>{question.subtitle}</div>}
                {question.variants.map((variant, vi) => (
                  <div key={vi} style={{ margin: "8px 0 8px 12px" }}>
                    {variant.label && <strong>{variant.label}</strong>}
                    <QuizMarkdown>{variant.text}</QuizMarkdown>
                    <ol type="A" style={{ margin: 0 }}>
                      {variant.options.map((option, oi) => (
                        <li
                          key={oi}
                          style={{ fontWeight: oi === variant.correct ? 700 : 400 }}
                          title={LETTERS[oi]}
                        >
                          <QuizMarkdown inline>{option}</QuizMarkdown>
                          {oi === variant.correct && " ✓"}
                        </li>
                      ))}
                    </ol>
                  </div>
                ))}
              </div>
            ))}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPreview(null)}>{t("quizAdmin.close")}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!editing} onClose={() => setEditing(null)} maxWidth="md" fullWidth>
        <DialogTitle>{editing && t("quizAdmin.editTitle", { id: editing.id })}</DialogTitle>
        <DialogContent>
          <div style={{ display: "flex", flexDirection: "column", gap: 12, paddingTop: 8 }}>
            {settingsFields(editForm, setEditForm)}
            <div style={{ color: "#666", fontSize: 14 }}>{t("quizAdmin.editHint")}</div>
            {editErrors.length > 0 && (
              <Alert severity="error">
                {editErrors.map((e) => (
                  <div key={e}>{e}</div>
                ))}
              </Alert>
            )}
          </div>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>{t("cancel")}</Button>
          <Button variant="contained" onClick={saveEdit}>
            {t("save")}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!toRun} onClose={() => setToRun(null)}>
        <DialogTitle>{toRun && (toRun.title || toRun.id)}</DialogTitle>
        <DialogContent>
          <FormControlLabel
            control={
              <Checkbox
                checked={inRoom}
                disabled={!!(toRun && toRun.variants_per_student)}
                onChange={(e) => setInRoom(e.target.checked)}
              />
            }
            label={t("quizAdmin.inRoom")}
          />
          <div style={{ color: "#666", fontSize: 14, maxWidth: 480 }}>
            {toRun && toRun.variants_per_student ? t("quizAdmin.inRoomNoVariants") : t("quizAdmin.inRoomHint")}
          </div>
          <FormControlLabel
            style={{ marginTop: 12 }}
            control={<Checkbox checked={leaderboard} onChange={(e) => setLeaderboard(e.target.checked)} />}
            label={t("quizAdmin.leaderboardOn")}
          />
          <div style={{ color: "#666", fontSize: 14, maxWidth: 480 }}>{t("quizAdmin.leaderboardHint")}</div>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setToRun(null)}>{t("cancel")}</Button>
          <Button variant="contained" onClick={() => run(toRun.id)}>
            {t("quizAdmin.open")}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!toDelete} onClose={() => setToDelete(null)}>
        <DialogTitle>{t("quizAdmin.confirmDelete", { id: toDelete })}</DialogTitle>
        <DialogActions>
          <Button onClick={() => setToDelete(null)}>{t("cancel")}</Button>
          <Button color="error" onClick={remove}>
            {t("quizAdmin.delete")}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar open={!!snackbar} autoHideDuration={6000} onClose={() => setSnackbar(null)}>
        {snackbar ? (
          <Alert severity={snackbar.severity} onClose={() => setSnackbar(null)}>
            {snackbar.message}
          </Alert>
        ) : (
          <span />
        )}
      </Snackbar>
    </Container>
  );
};
