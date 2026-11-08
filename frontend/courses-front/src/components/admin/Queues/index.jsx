import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert, Dialog, DialogTitle, DialogContent, DialogActions } from "@mui/material";
import { Spin } from "antd";
import { fetchAdminQueues, fetchAdminQueue, setQueueRegistration, updateAdminQueue, callNextStudent, finishStudent } from "../../../api/adminQueues";
import { QueueTable } from "../../queue-table";
import { Breadcrumb } from "../../breadcrumb";
import { Page, Navigation, BackButton, Title } from "../../course-actions/styled";
import { Content, Panel, Heading, Muted, Badge, Actions, PrimaryButton, SecondaryButton, Form, Label, Input } from "../../queue-detail/styled";
import { Status } from "../../queue-lessons/styled";

function localInput(value) {
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(value));
  const get = (name) => parts.find((part) => part.type === name).value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function AdminQueues() {
  const { courseId, queueId } = useParams();
  return <QueueView key={`${courseId}/${queueId || "list"}`} courseId={courseId} queueId={queueId} />;
}

function QueueView({ courseId, queueId }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [dialog, setDialog] = useState(null);
  const [starts, setStarts] = useState("");
  const [ends, setEnds] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const result = queueId
          ? await fetchAdminQueue(courseId, queueId, { signal: controller.signal })
          : await fetchAdminQueues(courseId, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setData((current) => queueId && current?.version > result.version ? current : result);
          setError(null);
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (cause.status === 401) navigate("/admin", { replace: true });
          else setError(cause.status === 404 ? t("adminQueues.notFound") : t("adminQueues.loadFailed"));
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 10000);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [courseId, queueId, refresh, navigate, t]);

  const format = (value) => new Intl.DateTimeFormat(i18n.language, {
    timeZone: "Europe/Moscow", weekday: "short", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(value));

  async function mutate(action) {
    setPending(true);
    setError(null);
    try {
      await action();
      const result = await fetchAdminQueue(courseId, queueId);
      setData((current) => current?.version > result.version ? current : result);
      setDialog(null);
      setRefresh((value) => value + 1);
    } catch (cause) {
      if (cause.status === 401) navigate("/admin", { replace: true });
      else if (cause.code === "QUEUE_EMPTY") setError(t("queueTable.noWaiting"));
      else if (cause.code === "INVALID_STATE") setError(t("queueTable.invalidState"));
      else if (cause.code === "LESSON_CANCELLED") setError(t("adminQueues.cancelled"));
      else setError(cause.status === 422 ? t("adminQueues.invalidTimes") : t("adminQueues.saveFailed"));
    } finally {
      setPending(false);
    }
  }

  function badges(lesson, closed) {
    return <>
      <Badge>{t(lesson.cancelled ? "adminQueues.cancelled" : closed ? "adminQueues.closed" : "adminQueues.open")}</Badge>
      {lesson.original_starts_at && <Muted>{t("adminQueues.movedFrom", { date: format(lesson.original_starts_at) })}</Muted>}
    </>;
  }

  return <Page>
    <Navigation>
      <BackButton onClick={() => navigate(queueId ? `/admin/courses/${courseId}/queues` : "/admin/courses")}>{t("adminQueues.back")}</BackButton>
      <Breadcrumb courseId={courseId} />
    </Navigation>
    <Title>{t(queueId ? "adminQueues.queueTitle" : "adminQueues.lessons")}</Title>
    <Content>
      {error && <Alert severity="error">{error}</Alert>}
      {!data && !error && <Status role="status"><Spin /><Muted>{t("adminQueues.loading")}</Muted></Status>}
      {!data && error && <SecondaryButton onClick={() => setRefresh((value) => value + 1)}>{t("queueLessons.retry")}</SecondaryButton>}
      {data && !queueId && <>
        <Muted>{data.course_name} · {data.week_start} — {data.week_end} · {t("queueDetail.moscowTime")}</Muted>
        {data.queues.length === 0 && <Panel><Muted>{t("queueLessons.empty")}</Muted></Panel>}
        {data.queues.map((queue) => <Panel key={queue.queue_id}>
          <Heading>{format(queue.lesson.starts_at)}</Heading>
          <Muted>{queue.lesson.kind} · {t("queueLessons.groups")}: {queue.lesson.groups.join(", ")}</Muted>
          {badges(queue.lesson, queue.closed)}
          <Muted>{t("adminQueues.entries", { count: queue.entries_count })}</Muted>
          <Actions><PrimaryButton onClick={() => navigate(`/admin/courses/${courseId}/queues/${queue.queue_id}`)}>{t("adminQueues.manage")}</PrimaryButton></Actions>
        </Panel>)}
      </>}
      {data && queueId && <>
        <Panel>
          <Heading>{format(data.lesson.starts_at)} — {format(data.lesson.ends_at)}</Heading>
          <Muted>{data.lesson.kind} · {t("queueLessons.groups")}: {data.lesson.groups.join(", ")}</Muted>
          {data.lesson.room && <Muted>{t("queueLessons.room")}: {data.lesson.room}</Muted>}
          {badges(data.lesson, data.closed)}
          <Actions>
            <PrimaryButton disabled={pending || data.lesson.cancelled} onClick={() => mutate(() => setQueueRegistration(courseId, queueId, !data.closed))}>
              {t(data.closed ? "adminQueues.openQueue" : "adminQueues.closeQueue")}
            </PrimaryButton>
            <SecondaryButton disabled={pending} onClick={() => { setStarts(localInput(data.lesson.starts_at)); setEnds(localInput(data.lesson.ends_at)); setDialog("move"); }}>{t("adminQueues.move")}</SecondaryButton>
            <SecondaryButton disabled={pending} onClick={() => setDialog("cancel")}>{t(data.lesson.cancelled ? "adminQueues.restore" : "adminQueues.cancelLesson")}</SecondaryButton>
          </Actions>
        </Panel>
        <Panel>
          <Heading>{t("queueDetail.participants")}</Heading>
          <Actions><PrimaryButton disabled={pending || data.lesson.cancelled || !data.waiting.length}
            onClick={() => mutate(() => callNextStudent(courseId, queueId))}>{t("queueTable.callNext")}</PrimaryButton></Actions>
          <QueueTable entries={data.entries} pending={pending}
            onFinish={(entryId) => mutate(() => finishStudent(courseId, queueId, entryId))} />
        </Panel>
      </>}
    </Content>
    <Dialog open={!!dialog} onClose={() => { if (!pending) setDialog(null); }} fullWidth maxWidth="sm" sx={{ zIndex: 3400 }} aria-labelledby="admin-queue-dialog-title">
      <DialogTitle id="admin-queue-dialog-title">{t(dialog === "move" ? "adminQueues.move" : data?.lesson?.cancelled ? "adminQueues.restore" : "adminQueues.cancelLesson")}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error">{error}</Alert>}
        {dialog === "move" ? <Form id="admin-move-form" onSubmit={(event) => {
          event.preventDefault();
          const values = new FormData(event.currentTarget);
          mutate(() => updateAdminQueue(courseId, queueId, { lesson_starts_at: `${values.get("starts")}:00+03:00`, lesson_ends_at: `${values.get("ends")}:00+03:00` }));
        }}>
          <Label htmlFor="lesson-start">{t("adminQueues.starts")}</Label>
          <Input id="lesson-start" name="starts" type="datetime-local" required value={starts} onChange={(event) => setStarts(event.target.value)} />
          <Label htmlFor="lesson-end">{t("adminQueues.ends")}</Label>
          <Input id="lesson-end" name="ends" type="datetime-local" required value={ends} onChange={(event) => setEnds(event.target.value)} />
        </Form> : <Muted>{t(data?.lesson?.cancelled ? "adminQueues.restoreConfirm" : "adminQueues.cancelConfirm")}</Muted>}
      </DialogContent>
      <DialogActions sx={{ p: 3, gap: 1, flexWrap: "wrap" }}>
        {dialog === "move" ? <PrimaryButton type="submit" form="admin-move-form" disabled={pending}>{t("save")}</PrimaryButton> :
          <PrimaryButton disabled={pending} onClick={() => mutate(() => updateAdminQueue(courseId, queueId, { cancelled: !data.lesson.cancelled }))}>{t("yes")}</PrimaryButton>}
        <SecondaryButton disabled={pending} onClick={() => setDialog(null)}>{t("cancel")}</SecondaryButton>
      </DialogActions>
    </Dialog>
  </Page>;
}
