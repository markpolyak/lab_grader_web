import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Dialog, DialogTitle, DialogContent, DialogActions } from "@mui/material";
import { createAdminLesson } from "../../../api/adminQueues";
import { DateTimeField } from "../../date-time-field";
import { Form, Label, Input, PrimaryButton, SecondaryButton } from "../../queue-detail/styled";

export function CreateLessonForm({ courseId, onClose, onCreated }) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  async function submit(event) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const groups = [...new Set(String(values.get("groups")).split(/[\s,;]+/).filter(Boolean))];
    if (!values.get("starts") || !values.get("ends")) { setError(t("adminQueues.dateTimeRequired")); return; }
    const starts = `${values.get("starts")}:00+03:00`;
    const ends = `${values.get("ends")}:00+03:00`;
    if (!groups.length) { setError(t("adminQueues.groupsRequired")); return; }
    if (new Date(ends) <= new Date(starts)) { setError(t("adminQueues.invalidTimes")); return; }
    setPending(true);
    setError(null);
    try {
      const result = await createAdminLesson(courseId, {
        group_ids: groups, lesson_starts_at: starts, lesson_ends_at: ends, closed: true,
      });
      onCreated(result.queue_id);
    } catch (cause) {
      setError(t(cause.status === 401 ? "adminQueues.loginRequired" : cause.status === 422 ? "adminQueues.invalidTimes" : "adminQueues.saveFailed"));
    } finally {
      setPending(false);
    }
  }

  return <Dialog open onClose={() => { if (!pending) onClose(); }} fullWidth maxWidth="sm"
    sx={{ zIndex: 3400 }} aria-labelledby="create-lesson-title">
    <DialogTitle id="create-lesson-title">{t("adminQueues.createLesson")}</DialogTitle>
    <DialogContent>
      {error && <Alert severity="error">{error}</Alert>}
      <Form id="create-lesson-form" onSubmit={submit}>
        <Label htmlFor="create-lesson-groups">{t("adminQueues.groupIds")}</Label>
        <Input id="create-lesson-groups" name="groups" placeholder="4431, 4432" required disabled={pending} autoComplete="off" />
        <Label htmlFor="create-lesson-start">{t("adminQueues.starts")}</Label>
        <DateTimeField id="create-lesson-start" name="starts" disabled={pending} />
        <Label htmlFor="create-lesson-end">{t("adminQueues.ends")}</Label>
        <DateTimeField id="create-lesson-end" name="ends" disabled={pending} />
      </Form>
    </DialogContent>
    <DialogActions sx={{ p: 3, gap: 1, flexWrap: "wrap" }}>
      <PrimaryButton type="submit" form="create-lesson-form" disabled={pending}>{t("adminQueues.createLesson")}</PrimaryButton>
      <SecondaryButton disabled={pending} onClick={onClose}>{t("cancel")}</SecondaryButton>
    </DialogActions>
  </Dialog>;
}
