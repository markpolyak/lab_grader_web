import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert, Dialog, DialogTitle, DialogContent, DialogActions } from "@mui/material";
import { fetchStudentQueue, joinLessonQueue } from "../../api";
import { normalizeStudentName, queueErrorKey } from "./state";
import { Panel, Heading, Form, Label, Input, Select, Actions, PrimaryButton, SecondaryButton, Muted } from "./styled";

export function JoinQueueForm({ courseId, snapshot, revision, onChanged, onOwnEntry }) {
  const { t } = useTranslation();
  const [github, setGithub] = useState("");
  const [fullName, setFullName] = useState("");
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState(snapshot.lesson.groups[0] || "");
  const [lab, setLab] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const [student, setStudent] = useState(null);
  const [checkedRevision, setCheckedRevision] = useState(-1);

  const entry = student?.entry;
  const called = snapshot.called.find((item) => item.id === entry?.id);
  const waiting = snapshot.waiting.find((item) => item.id === entry?.id);
  const ownEntry = entry && (
    entry.lesson_id !== snapshot.queue_id || revision <= checkedRevision
      ? entry
      : called ? { ...entry, status: "called", position: null }
        : waiting ? { ...entry, status: "waiting", position: waiting.position } : null
  );

  useEffect(() => {
    onOwnEntry(ownEntry?.id || null);
  }, [ownEntry?.id, onOwnEntry]);

  function resetIdentity() {
    setStudent(null);
    setError(null);
  }

  async function submit(join) {
    if (!fullName.trim() || !github.trim() || !group || (join && !lab)) {
      setError("required");
      return;
    }
    setPending(true);
    setError(null);
    try {
      let result = await fetchStudentQueue(courseId, { github: github.trim(), group_id: group });
      if (normalizeStudentName(fullName) !== normalizeStudentName(result.full_name)) {
        setError("nameMismatch");
        return;
      }
      if (join && !result.entry) {
        result = await joinLessonQueue(courseId, {
          github: github.trim(), full_name: fullName.trim(), lesson_id: snapshot.queue_id, lab_id: lab,
        });
      }
      setStudent(result);
      setCheckedRevision(revision);
      onChanged();
      setOpen(false);
    } catch (cause) {
      setError(queueErrorKey(cause));
      // A failed command can mean that somebody just closed the queue.
      onChanged();
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel>
      <Heading>{t("queueDetail.joinTitle")}</Heading>
      {snapshot.closed && <Alert severity="info">{t("queueDetail.closed")}</Alert>}
      {ownEntry && (
        <Alert severity={ownEntry.status === "called" ? "success" : "info"}>
          {ownEntry.lesson_id !== snapshot.queue_id ? (
            <>{t("queueDetail.otherLesson")} <Link to={`/course/${courseId}/queue/${ownEntry.lesson_id}`}>{t("queueDetail.openMyQueue")}</Link></>
          ) : ownEntry.status === "called" ? t("queueDetail.youAreCalled") : t("queueDetail.yourPosition", { position: ownEntry.position })}
        </Alert>
      )}
      {student && !ownEntry && <Muted role="status">{t("queueDetail.noActiveEntry")}</Muted>}
      <Actions>
        <PrimaryButton type="button" disabled={snapshot.closed || !snapshot.labs.length || !!ownEntry} onClick={() => setOpen(true)}>
          {t("queueDetail.join")}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={() => setOpen(true)}>{t("queueDetail.checkEntry")}</SecondaryButton>
      </Actions>
      <Dialog open={open} onClose={() => { if (!pending) setOpen(false); }} fullWidth maxWidth="sm"
        aria-labelledby="queue-dialog-title"
        sx={{ zIndex: 3400 }}
        PaperProps={{ sx: { borderRadius: "12px", fontFamily: '"Source Code Pro", monospace' } }}>
        <DialogTitle id="queue-dialog-title">{t("queueDetail.joinTitle")}</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{t(`queueDetail.errors.${error}`)}</Alert>}
          <Form id="join-queue-form" onSubmit={(event) => { event.preventDefault(); submit(true); }}>
            <Label htmlFor="queue-full-name">{t("queueDetail.fullName")}</Label>
            <Input id="queue-full-name" value={fullName} disabled={pending} autoComplete="name" required
              onChange={(event) => { setFullName(event.target.value); resetIdentity(); }} />
            <Label htmlFor="queue-github">{t("queueDetail.github")}</Label>
            <Input id="queue-github" value={github} disabled={pending} autoComplete="off" autoCapitalize="none" spellCheck={false} required
              onChange={(event) => { setGithub(event.target.value); resetIdentity(); }} />
            <Label htmlFor="queue-group">{t("queueDetail.group")}</Label>
            <Select id="queue-group" value={group} disabled={pending} required
              onChange={(event) => { setGroup(event.target.value); resetIdentity(); }}>
              {snapshot.lesson.groups.map((value) => <option key={value} value={value}>{value}</option>)}
            </Select>
            <Label htmlFor="queue-lab">{t("queueDetail.lab")}</Label>
            <Select id="queue-lab" value={lab} disabled={pending || !snapshot.labs.length} required
              onChange={(event) => { setLab(event.target.value); setError(null); }}>
              <option value="">{t("queueDetail.chooseLab")}</option>
              {snapshot.labs.map((item) => <option key={item.id} value={item.id}>{item.id} — {item.name}</option>)}
            </Select>
            {!snapshot.labs.length && <Muted>{t("queueDetail.noLabs")}</Muted>}
            <Muted>{t("queueDetail.eligibility")}</Muted>
          </Form>
        </DialogContent>
        <DialogActions sx={{ p: 3, flexWrap: "wrap", gap: 1 }}>
          <PrimaryButton type="submit" form="join-queue-form" disabled={pending || snapshot.closed || !snapshot.labs.length || !!ownEntry}>
            {pending ? t("queueDetail.sending") : t("queueDetail.join")}
          </PrimaryButton>
          <SecondaryButton type="button" disabled={pending || !fullName.trim() || !github.trim() || !group} onClick={() => submit(false)}>
            {t("queueDetail.checkEntry")}
          </SecondaryButton>
          <SecondaryButton type="button" disabled={pending} onClick={() => setOpen(false)}>{t("cancel")}</SecondaryButton>
        </DialogActions>
      </Dialog>
    </Panel>
  );
}
