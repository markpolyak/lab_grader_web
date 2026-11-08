import { useEffect, useState } from "react";
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
import { queueErrorKey } from "./state";
import { Content, Panel, Heading, Muted, Participants, Participant, Position, StudentName, Badge } from "./styled";

export function QueueDetail({ courseId, lessonId, onBack }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [revision, setRevision] = useState(0);
  const [ownEntryId, setOwnEntryId] = useState(null);

  useEffect(() => {
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
        if (!controller.signal.aborted) timer = setTimeout(load, 10000);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [courseId, lessonId, attempt]);

  const dateTime = new Intl.DateTimeFormat(i18n.language, {
    timeZone: "Europe/Moscow", weekday: "long", day: "numeric", month: "long",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });

  function participant(item, isCalled) {
    return (
      <Participant key={item.id} $own={item.id === ownEntryId}>
        <Position>{isCalled ? "→" : item.position}</Position>
        <div><StudentName>{item.full_name}</StudentName><Muted>{item.lab_name || "—"}</Muted></div>
        {isCalled && <Badge>{t("queueDetail.calledBadge")}</Badge>}
        {item.id === ownEntryId && <Badge>{t("queueDetail.you")}</Badge>}
      </Participant>
    );
  }

  return (
    <Page>
      <FixedAdminButton onClick={() => navigate("/admin")}>{t("courseActions.forTeachers")}</FixedAdminButton>
      <LanguageSwitcher />
      <Navigation><BackButton onClick={onBack}>{t("queueDetail.back")}</BackButton><Breadcrumb courseId={courseId} /></Navigation>
      <Title>{t("queueDetail.title")}</Title>
      <Content>
        {error && <Alert severity="error" action={<RetryButton onClick={() => setAttempt((value) => value + 1)}>{t("queueLessons.retry")}</RetryButton>}>{t(`queueDetail.errors.${error}`)}</Alert>}
        {!snapshot && !error && <Status role="status"><Spin /><Muted>{t("queueDetail.loading")}</Muted></Status>}
        {snapshot && (
          <>
            <Description>{snapshot.course_name}</Description>
            <Description>{dateTime.format(new Date(snapshot.lesson.starts_at))} · {t("queueDetail.moscowTime")}</Description>
            <Description>{snapshot.lesson.kind} · {t("queueLessons.groups")}: {snapshot.lesson.groups.join(", ")}{snapshot.lesson.room ? ` · ${snapshot.lesson.room}` : ""}</Description>
            {snapshot.lesson.cancelled && <Alert severity="warning">{t("adminQueues.cancelled")}</Alert>}
            {snapshot.lesson.original_starts_at && <Description>{t("adminQueues.movedFrom", { date: dateTime.format(new Date(snapshot.lesson.original_starts_at)) })}</Description>}
            <Panel aria-live="polite">
              <Heading>{t("queueDetail.participants")}</Heading>
              <Muted>{t("queueDetail.autoRefresh")}</Muted>
              {snapshot.called.length > 0 && <><Heading as="h3">{t("queueDetail.called")}</Heading><Participants>{snapshot.called.map((item) => participant(item, true))}</Participants></>}
              <Heading as="h3">{t("queueDetail.waiting", { count: snapshot.waiting.length })}</Heading>
              {snapshot.waiting.length ? <Participants>{snapshot.waiting.map((item) => participant(item, false))}</Participants> : <Muted>{t("queueDetail.empty")}</Muted>}
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
