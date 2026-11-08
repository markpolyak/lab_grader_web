import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert } from "@mui/material";
import { Spin } from "antd";
import { fetchCourseLessons } from "../../api";
import { Breadcrumb } from "../breadcrumb";
import { LanguageSwitcher } from "../language-switcher";
import { FixedAdminButton } from "../course-list/styled";
import { Page, Navigation, BackButton, Title } from "../course-actions/styled";
import {
  Content,
  Description,
  LessonList,
  LessonCard,
  LessonDate,
  LessonTime,
  LessonDetails,
  Subject,
  Detail,
  SelectLessonButton,
  Status,
  RetryButton,
} from "./styled";

const TIME_ZONE = "Europe/Moscow";

export function QueueLessons({ courseId, onBack }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [schedule, setSchedule] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setSchedule(null);
    setError(null);
    fetchCourseLessons(courseId, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setSchedule(data);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause.status === 404 ? "notConfigured" : cause.status === 429 ? "rateLimit" : "loadFailed");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [courseId, attempt]);

  const dateFormatter = new Intl.DateTimeFormat(i18n.language, {
    timeZone: TIME_ZONE, weekday: "long", day: "numeric", month: "long",
  });
  const timeFormatter = new Intl.DateTimeFormat(i18n.language, {
    timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  const weekFormatter = new Intl.DateTimeFormat(i18n.language, {
    timeZone: TIME_ZONE, day: "numeric", month: "long", year: "numeric",
  });

  return (
    <Page>
      <FixedAdminButton onClick={() => navigate("/admin")}>
        {t("courseActions.forTeachers")}
      </FixedAdminButton>
      <LanguageSwitcher />
      <Navigation>
        <BackButton onClick={onBack}>{t("queueLessons.back")}</BackButton>
        <Breadcrumb courseId={courseId} />
      </Navigation>
      <Title>{t("queueLessons.title")}</Title>
      <Content>
        {loading && (
          <Status role="status" aria-live="polite">
            <Spin size="default" />
            <Description>{t("queueLessons.loading")}</Description>
          </Status>
        )}
        {error && (
          <Status role="alert">
            <Alert severity={error === "notConfigured" ? "info" : "error"}>
              {t(`queueLessons.${error}`)}
            </Alert>
            <RetryButton type="button" onClick={() => setAttempt((value) => value + 1)}>
              {t("queueLessons.retry")}
            </RetryButton>
          </Status>
        )}
        {schedule && (
          <>
            <Description>
              {t("queueLessons.week", {
                dates: weekFormatter.formatRange(
                  new Date(`${schedule.week_start}T12:00:00Z`),
                  new Date(`${schedule.week_end}T12:00:00Z`)
                ),
              })}
            </Description>
            {schedule.lessons.length === 0 ? (
              <Status role="status">{t("queueLessons.empty")}</Status>
            ) : (
              <LessonList aria-label={t("queueLessons.title")}>
                {schedule.lessons.map((lesson) => (
                  <LessonCard key={lesson.id}>
                    <LessonDate>
                      <time dateTime={lesson.starts_at}>
                        {dateFormatter.format(new Date(lesson.starts_at))}
                      </time>
                      <LessonTime>
                        {timeFormatter.format(new Date(lesson.starts_at))}
                        {"–"}
                        {timeFormatter.format(new Date(lesson.ends_at))}
                      </LessonTime>
                    </LessonDate>
                    <LessonDetails>
                      <Subject>{schedule.course_name}</Subject>
                      <Detail>{lesson.kind}</Detail>
                      <Detail>{t("queueLessons.groups")}: {lesson.groups.join(", ") || "—"}</Detail>
                      {lesson.room && <Detail>{t("queueLessons.room")}: {lesson.room}</Detail>}
                    </LessonDetails>
                    <SelectLessonButton type="button" onClick={() => navigate(`/course/${courseId}/queue/${lesson.id}`)}>
                      {t("select")}
                    </SelectLessonButton>
                  </LessonCard>
                ))}
              </LessonList>
            )}
          </>
        )}
      </Content>
    </Page>
  );
}
