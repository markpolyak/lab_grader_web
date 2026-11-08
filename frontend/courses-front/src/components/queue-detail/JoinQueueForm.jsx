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
  const [surname, setSurname] = useState("");
  const [name, setName] = useState("");
  const [patronymic, setPatronymic] = useState("");
  const fullName = [surname, name, patronymic].map((part) => part.trim()).filter(Boolean).join(" ");
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
  const currentEntry = entry && (
    entry.lesson_id !== snapshot.queue_id || revision <= checkedRevision
      ? entry
      : called ? { ...entry, status: "called", position: null }
        : waiting ? { ...entry, status: "waiting", position: waiting.position } : null
  );
  const row = snapshot.entries.find((item) => item.id === currentEntry?.id);
  const ownEntry = currentEntry && row ? { ...currentEntry, position: row.position } : currentEntry;

  useEffect(() => {
    onOwnEntry(ownEntry?.id || null);
  }, [ownEntry?.id, onOwnEntry]);

  function resetIdentity() {
    setStudent(null);
    setError(null);
  }

  async function submit(join) {
    if (!surname.trim() || !name.trim() || !github.trim() || !group || (join && !lab)) {
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
          github: github.trim(), surname: surname.trim(), name: name.trim(), patronymic: patronymic.trim(), lesson_id: snapshot.queue_id, lab_id: lab,
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
            <Label htmlFor="queue-surname">{t("queueDetail.surname")}</Label>
            <Input id="queue-surname" value={surname} disabled={pending} autoComplete="family-name" required
              onChange={(event) => { setSurname(event.target.value); resetIdentity(); }} />
            <Label htmlFor="queue-name">{t("queueDetail.name")}</Label>
            <Input id="queue-name" value={name} disabled={pending} autoComplete="given-name" required
              onChange={(event) => { setName(event.target.value); resetIdentity(); }} />
            <Label htmlFor="queue-patronymic">{t("queueDetail.patronymic")}</Label>
            <Input id="queue-patronymic" value={patronymic} disabled={pending} autoComplete="additional-name"
              onChange={(event) => { setPatronymic(event.target.value); resetIdentity(); }} />
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
          </Form>
        </DialogContent>
        <DialogActions sx={{ p: 3, flexWrap: "wrap", gap: 1 }}>
          <PrimaryButton type="submit" form="join-queue-form" disabled={pending || snapshot.closed || !snapshot.labs.length || !!ownEntry}>
            {pending ? t("queueDetail.sending") : t("queueDetail.join")}
          </PrimaryButton>
          <SecondaryButton type="button" disabled={pending || !surname.trim() || !name.trim() || !github.trim() || !group} onClick={() => submit(false)}>
            {t("queueDetail.checkEntry")}
          </SecondaryButton>
          <SecondaryButton type="button" disabled={pending} onClick={() => setOpen(false)}>{t("cancel")}</SecondaryButton>
        </DialogActions>
      </Dialog>
    </Panel>
  );
}
