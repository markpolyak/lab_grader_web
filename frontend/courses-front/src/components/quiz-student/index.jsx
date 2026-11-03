import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { QuizMarkdown } from "../quiz-markdown";
import {
  AnswerButton,
  Card,
  Countdown,
  Hint,
  OfflineBadge,
  Page,
  PrimaryButton,
  QuestionText,
  Status,
  Title,
} from "./styled";

// Квиз на телефоне студента (docs/QUIZ_PLAN.md §10.1). Состояние приходит по
// SSE полным снимком; отсчёт только отображается - время решает сервер.

const API = "/api/v1/quiz";

// Ошибки backend приходят стабильными кодами - переводим сами.
const errorKey = (code) => `quiz.errors.${code}`;

export const QuizStudent = () => {
  const { code } = useParams();
  const [searchParams] = useSearchParams();
  // In the room only: the short-lived code from the projector's QR (?t=...)
  const entryToken = searchParams.get("t") || "";
  const { t } = useTranslation();

  const [info, setInfo] = useState(null);       // GET /quiz/{code}
  const [snapshot, setSnapshot] = useState(null); // SSE
  const [error, setError] = useState(searchParams.get("error") ? "oauth_failed" : null);
  const [notInCourse, setNotInCourse] = useState(false);
  const [offline, setOffline] = useState(false);
  const [pending, setPending] = useState(false);
  const [localAnswer, setLocalAnswer] = useState(null); // {question, option}
  const [remaining, setRemaining] = useState(0);
  const deadlineRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const query = entryToken ? `?t=${encodeURIComponent(entryToken)}` : "";
      const response = await fetch(`${API}/${encodeURIComponent(code)}${query}`, { credentials: "include" });
      if (entryToken) {
        // The server has turned it into a pass; a copied address must not carry it.
        window.history.replaceState(null, "", window.location.pathname);
      }
      if (response.status === 404) {
        setError("not_found");
        return null;
      }
      const data = await response.json();
      setInfo(data);
      return data;
    } catch {
      setError("network");
      return null;
    }
  }, [code, entryToken]);

  // 1. Who am I, what is the quiz; 2. join if signed in.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const data = await load();
      if (cancelled || !data || !data.logged_in || data.joined) return;
      if (data.state === "finished" || data.state === "cancelled") return;
      const response = await fetch(`${API}/${encodeURIComponent(code)}/join`, {
        method: "POST",
        credentials: "include",
      });
      if (cancelled) return;
      if (response.ok) {
        setInfo((prev) => ({ ...prev, joined: true }));
      } else {
        const body = await response.json().catch(() => ({}));
        if (body.detail === "NOT_IN_COURSE") setNotInCourse(true);
        else setError(body.detail || "unknown");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, load]);

  // 3. Live state over SSE once joined; EventSource reconnects by itself.
  const joined = info && info.joined;
  useEffect(() => {
    if (!joined) return undefined;
    const source = new EventSource(`${API}/${encodeURIComponent(code)}/events`, { withCredentials: true });
    let version = -1;
    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setOffline(false);
      if (data.version < version) return;
      version = data.version;
      setSnapshot(data);
      deadlineRef.current = data.state === "question" ? Date.now() + data.remaining_ms : null;
      setRemaining(data.remaining_ms);
      if (data.state === "finished" || data.state === "cancelled") source.close();
    };
    source.onerror = () => setOffline(true);
    return () => source.close();
  }, [code, joined]);

  // Countdown display only.
  useEffect(() => {
    const timer = setInterval(() => {
      if (deadlineRef.current) setRemaining(Math.max(0, deadlineRef.current - Date.now()));
    }, 200);
    return () => clearInterval(timer);
  }, []);

  const answer = async (option) => {
    if (!snapshot || pending) return;
    setPending(true);
    try {
      const response = await fetch(`${API}/${encodeURIComponent(code)}/answer`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: snapshot.question, option }),
      });
      if (response.ok) {
        setLocalAnswer({ question: snapshot.question, option });
      } else {
        const body = await response.json().catch(() => ({}));
        setError(body.detail || "unknown");
        setTimeout(() => setError(null), 4000);
      }
    } catch {
      setError("network");
    } finally {
      setPending(false);
    }
  };

  // ---------------------------------------------------------------------

  if (error === "not_found") {
    return (
      <Page>
        <Card>
          <Status>{t("quiz.errors.not_found")}</Status>
        </Card>
      </Page>
    );
  }
  if (!info) return <Page><Card><Hint>{t("quiz.loading")}</Hint></Card></Page>;

  const header = (
    <>
      <Hint>{info.course}</Hint>
      <Title>{info.title}</Title>
    </>
  );

  if (!info.joined && info.in_room && (!info.entry_ok || !info.join_open)) {
    return (
      <Page>
        <Card>
          {header}
          <Status>{t(info.join_open ? "quiz.errors.ENTRY_REQUIRED" : "quiz.errors.JOIN_CLOSED")}</Status>
        </Card>
      </Page>
    );
  }

  if (!info.logged_in) {
    return (
      <Page>
        <Card>
          {header}
          {error && <Hint $error>{t(errorKey(error))}</Hint>}
          <Hint>{t("quiz.signInHint")}</Hint>
          <PrimaryButton
            onClick={() => {
              window.location.href = `${API}/login/start?code=${encodeURIComponent(code)}`;
            }}
          >
            {t("quiz.signIn")}
          </PrimaryButton>
        </Card>
      </Page>
    );
  }

  if (notInCourse) {
    return (
      <Page>
        <Card>
          {header}
          <Status>{t("quiz.errors.NOT_IN_COURSE")}</Status>
          <Hint>{t("quiz.notInCourseHint", { login: info.login })}</Hint>
          <PrimaryButton as="a" href={`/course/${encodeURIComponent(info.course_id)}`}>
            {t("quiz.register")}
          </PrimaryButton>
        </Card>
      </Page>
    );
  }

  const s = snapshot || info;
  const answered =
    s.answer !== undefined && s.answer !== null
      ? s.answer
      : localAnswer && localAnswer.question === s.question
        ? localAnswer.option
        : null;

  return (
    <Page>
      <Card>
        {offline && <OfflineBadge>{t("quiz.reconnecting")}</OfflineBadge>}
        {header}
        {error && error !== "oauth_failed" && <Hint $error>{t(errorKey(error), { defaultValue: error })}</Hint>}

        {s.state === "lobby" && <Status>{t("quiz.waiting")}</Status>}

        {(s.state === "question" || s.state === "reveal") && s.options && (
          <>
            <Hint>{t("quiz.questionOf", { n: s.question + 1, total: s.total })}</Hint>
            {s.state === "question" && <Countdown>{Math.ceil(remaining / 1000)}</Countdown>}
            {s.text ? (
              <QuestionText>
                <QuizMarkdown>{s.text}</QuizMarkdown>
              </QuestionText>
            ) : (
              <Hint>{t("quiz.questionOnScreen")}</Hint>
            )}
            {s.options.map((option) => {
              const isCorrect = s.state === "reveal" && option.id === s.correct;
              const isMine = answered === option.id;
              return (
                <AnswerButton
                  key={option.id}
                  disabled={s.state !== "question" || answered !== null || pending}
                  $mine={isMine}
                  $correct={isCorrect}
                  $wrong={s.state === "reveal" && isMine && !isCorrect}
                  onClick={() => answer(option.id)}
                >
                  <QuizMarkdown inline>{option.text}</QuizMarkdown>
                </AnswerButton>
              );
            })}
            {s.state === "question" && answered !== null && <Status>{t("quiz.accepted")}</Status>}
            {s.state === "reveal" && (
              <Status>
                {answered === null
                  ? t("quiz.noAnswer")
                  : s.answered_correctly
                    ? t("quiz.correct")
                    : t("quiz.wrong")}
              </Status>
            )}
          </>
        )}

        {s.state === "finished" && (
          <>
            <Status>{t("quiz.finished")}</Status>
            {s.score !== undefined && (
              <Title>{t("quiz.score", { score: s.score, max: s.max_points })}</Title>
            )}
          </>
        )}
        {s.state === "cancelled" && <Status>{t("quiz.cancelled")}</Status>}
      </Card>
    </Page>
  );
};
