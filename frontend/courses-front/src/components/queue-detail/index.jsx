import { useEffect, useState } from "react";
import { useQueueEvents } from "../../hooks/useQueueEvents";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert } from "@mui/material";
import { Spin } from "antd";
import { fetchLessonQueue } from "../../api";
import { Breadcrumb } from "../breadcrumb";
import { LanguageSwitcher } from "../language-switcher";
import { FixedAdminButton } from "../course-list/styled";
import { Page, Navigation, BackButton, Title } from "../course-actions/styled";
import { Status, Description, RetryButton } from "../queue-lessons/styled";
import { JoinQueueForm } from "./JoinQueueForm";
import { QueueTable } from "../queue-table";
import { RegistrationStatus } from "../registration-status";
import { queueErrorKey } from "./state";
import { Content, Panel, Heading, Muted } from "./styled";

export function QueueDetail({ courseId, lessonId, onBack }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState(null);
  const [archived, setArchived] = useState(false);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [revision, setRevision] = useState(0);
  const [ownEntryId, setOwnEntryId] = useState(null);
  useQueueEvents(lessonId, (event) => {
    if (event.type === "queue.archived") {
      setSnapshot(null);
      setArchived(true);
      return;
    }
    if (event.type === "queue.deleted") setSnapshot(null);
    setAttempt((value) => value + 1);
  });

  useEffect(() => {
    if (archived) return;
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const result = await fetchLessonQueue(courseId, lessonId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setSnapshot((current) => current && current.version > result.version ? current : result);
        setRevision((value) => value + 1);
        setError(null);
      } catch (cause) {
        if (!controller.signal.aborted) setError(queueErrorKey(cause));
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 60000);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [courseId, lessonId, attempt, archived]);

  const dateTime = new Intl.DateTimeFormat(i18n.language, {
    timeZone: "Europe/Moscow", weekday: "long", day: "numeric", month: "long",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });

  return (
    <Page>
      <FixedAdminButton onClick={() => navigate("/admin")}>{t("courseActions.forTeachers")}</FixedAdminButton>
      <LanguageSwitcher />
      <Navigation><BackButton onClick={onBack}>{t("queueDetail.back")}</BackButton><Breadcrumb courseId={courseId} /></Navigation>
      <Title>{t("queueDetail.title")}</Title>
      <Content>
        {archived && <Alert severity="info">{t("queueDetail.archived")}</Alert>}
        {!archived && error && <Alert severity="error" action={<RetryButton onClick={() => setAttempt((value) => value + 1)}>{t("queueLessons.retry")}</RetryButton>}>{t(`queueDetail.errors.${error}`)}</Alert>}
        {!archived && !snapshot && !error && <Status role="status"><Spin /><Muted>{t("queueDetail.loading")}</Muted></Status>}
        {!archived && snapshot && (
          <>
            <Description>{snapshot.course_name}</Description>
            <Description>{dateTime.format(new Date(snapshot.lesson.starts_at))} · {t("queueDetail.moscowTime")}</Description>
            <Description>{snapshot.lesson.kind} · {t("queueLessons.groups")}: {snapshot.lesson.groups.join(", ")}{snapshot.lesson.room ? ` · ${snapshot.lesson.room}` : ""}</Description>
            <RegistrationStatus closed={snapshot.closed} cancelled={snapshot.lesson.cancelled} />
            {snapshot.lesson.original_starts_at && <Description>{t("adminQueues.movedFrom", { date: dateTime.format(new Date(snapshot.lesson.original_starts_at)) })}</Description>}
            <Panel aria-live="polite">
              <Heading>{t("queueDetail.participants")}</Heading>
              <QueueTable entries={snapshot.entries} ownEntryId={ownEntryId} />
            </Panel>
            <JoinQueueForm
              courseId={courseId} snapshot={snapshot} revision={revision}
              onChanged={() => setAttempt((value) => value + 1)} onOwnEntry={setOwnEntryId}
            />
          </>
        )}
      </Content>
    </Page>
  );
}
