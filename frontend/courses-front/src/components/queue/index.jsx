import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { API_BASE_URL } from "../../api";
import { createQueueApi } from "../../api/queue";
import { SUPPORTED_LANGUAGES } from "../../language";
import { MyEntry } from "./MyEntry";
import { SessionList } from "./SessionList";
import { SignupForm } from "./SignupForm";
import {
  ActionButton,
  BackButton,
  Description,
  Details,
  ErrorPanel,
  Hint,
  Label,
  LanguageControl,
  LanguageSelect,
  LinkButton,
  NoticePanel,
  QueueCard,
  QueuePage,
  SecondaryButton,
  SectionTitle,
  Spinner,
  Subtitle,
  Title,
  Value,
} from "./styled";
import {
  STOP_POLLING_CODES,
  actionErrorAfterPoll,
  canBeginLogin,
  createLatestGate,
  errorKey,
  formatDay,
  formatInCourseTz,
  lastResultToShow,
  loginErrorToShow,
  nextPollDelay,
  readCallback,
  statusKey,
} from "./state";
import { usePolling } from "./usePolling";

const api = createQueueApi({ baseUrl: API_BASE_URL });


// Страница очереди для студента: /course/:courseId/queue.
export function DefenseQueue() {
  const { courseId } = useParams();
  // Другой курс в адресе - другая страница с чистым состоянием.
  return <StudentQueue key={courseId} courseId={courseId} />;
}


function StudentQueue({ courseId }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const labParam = searchParams.get("lab");
  // Результат входа читается один раз: после перезагрузки страницы старое
  // «не удалось войти» показывать не нужно.
  const [callback] = useState(() => readCallback(searchParams));
  const callbackCleanedRef = useRef(false);

  const [gate] = useState(createLatestGate);
  const failuresRef = useRef(0);
  const loggedInRef = useRef(false);
  const busyRef = useRef(false);

  const [data, setData] = useState(null);
  const [pollError, setPollError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [labs, setLabs] = useState(null);
  const [labsError, setLabsError] = useState(null);
  const [labsLoading, setLabsLoading] = useState(false);

  useEffect(() => {
    if (!callback || callbackCleanedRef.current) return;
    callbackCleanedRef.current = true;
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("status");
        next.delete("reason");
        return next;
      },
      { replace: true }
    );
  }, [callback, setSearchParams]);

  const applyState = useCallback(
    (ticket, next) => {
      if (!gate.accept(ticket)) return;
      if (next && Array.isArray(next.sessions)) {
        loggedInRef.current = Boolean(next.viewer);
        setData(next);
      }
    },
    [gate]
  );

  const poll = useCallback(async () => {
    const ticket = gate.begin();
    const startedAt = Date.now();
    try {
      const next = await api.getState(courseId);
      applyState(ticket, next);
      failuresRef.current = 0;
      setPollError(null);
      // Связь вернулась - «Нет связи с сервером» от прошлого действия уже неправда.
      setActionError((current) => actionErrorAfterPoll(current, startedAt));
      return nextPollDelay({ loggedIn: loggedInRef.current });
    } catch (error) {
      const code = error.code || "unknown";
      failuresRef.current += 1;
      setPollError(code);
      if (STOP_POLLING_CODES.has(code)) return null;
      return nextPollDelay({
        loggedIn: loggedInRef.current,
        failures: failuresRef.current,
        rateLimited: code === "rate_limit",
      });
    }
  }, [courseId, gate, applyState]);

  const refresh = usePolling(poll);

  const viewerLogin = data && data.viewer ? data.viewer.github : null;
  const hasEntry = Boolean(data && data.my_entry);
  const needLabs = Boolean(viewerLogin) && !hasEntry;

  const loadLabs = useCallback(() => {
    setLabsLoading(true);
    return api
      .getLabs(courseId)
      .then((result) => {
        setLabs(result);
        setLabsError(null);
      })
      .catch((error) => setLabsError(error.code || "unknown"))
      .finally(() => setLabsLoading(false));
  }, [courseId]);

  // Статусы работ перечитываются при входе и каждый раз, когда запись
  // закончилась: после защиты работа уже «защищена». До отрисовки: иначе
  // на один кадр мелькнула бы форма без списка с кнопкой «Повторить».
  useLayoutEffect(() => {
    if (needLabs) loadLabs();
  }, [needLabs, viewerLogin, loadLabs]);

  const runAction = async (name, action) => {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(name);
    setActionError(null);
    const ticket = gate.begin();
    let ok = false;
    try {
      applyState(ticket, await action());
      ok = true;
    } catch (error) {
      // Ответы опросов, начатых до действия, уже устарели: свежее
      // состояние запросит refresh() ниже.
      gate.accept(ticket);
      setActionError({ code: error.code || "unknown", at: Date.now() });
      if (error.code === "LAB_NOT_PASSED" || error.code === "LAB_ALREADY_DEFENDED") loadLabs();
    } finally {
      busyRef.current = false;
      setBusy(null);
      refresh();
    }
    return ok;
  };

  const handleSignUp = ({ sessionId, labId }) =>
    runAction("signup", () => api.signUp(courseId, { sessionId, labId }));
  const handleLeave = () => runAction("leave", () => api.leave(courseId));
  const handleSkip = () => runAction("skip", () => api.skip(courseId));
  // Список работ - того, кто вышел. Если выйти не удалось, студент
  // остаётся вошедшим, и список ему ещё нужен.
  const handleLogout = () =>
    runAction("logout", () => api.logout(courseId)).then((ok) => {
      if (ok) setLabs(null);
    });

  const beginLogin = () => {
    setIsRedirecting(true);
    window.location.assign(api.loginUrl(courseId, labParam));
  };

  const showLogin = canBeginLogin(data, pollError);
  const loginError = loginErrorToShow(callback, pollError);
  const loginBlock = (
    <>
      <Description>{t("queue.login.description")}</Description>
      <ActionButton type="button" onClick={beginLogin} disabled={isRedirecting}>
        {isRedirecting ? t("queue.login.redirecting") : t("queue.login.button")}
      </ActionButton>
    </>
  );

  const offset = data ? data.utc_offset_minutes : null;
  const formatTime = (iso) => formatInCourseTz(iso, offset, lang);
  const lastResult = lastResultToShow(data);
  const schedule = data && data.schedule;

  return (
    <QueuePage>
      <BackButton onClick={() => navigate(`/course/${encodeURIComponent(courseId)}`)}>
        {t("queue.back")}
      </BackButton>
      <QueueCard>
        <LanguageControl>
          <Label as="label" htmlFor="queue-language">
            {t("queue.language")}
          </Label>
          <LanguageSelect
            id="queue-language"
            value={lang.split("-")[0]}
            onChange={(event) => i18n.changeLanguage(event.target.value)}
          >
            {SUPPORTED_LANGUAGES.map(({ code, label }) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </LanguageSelect>
        </LanguageControl>

        <div>
          <Title>{t("queue.title")}</Title>
          {data && (
            <Subtitle>
              {data.course_name}
              {data.day ? ` · ${t("queue.today", { date: formatDay(data.day, lang) })}` : ""}
            </Subtitle>
          )}
        </div>

        {loginError && (
          <ErrorPanel role="alert">
            <strong>{t("queue.loginFailed")}</strong>
            <span>{t(errorKey(loginError))}</span>
          </ErrorPanel>
        )}

        {!data && !pollError && (
          <Description role="status">
            <Spinner aria-hidden="true" />
            {t("queue.loading")}
          </Description>
        )}

        {!data && pollError && (
          <>
            <ErrorPanel role="alert">
              <strong>{t("queue.errorTitle")}</strong>
              <span>{t(errorKey(pollError))}</span>
            </ErrorPanel>
            {!STOP_POLLING_CODES.has(pollError) && (
              <SecondaryButton type="button" onClick={() => refresh()}>
                {t("queue.retry")}
              </SecondaryButton>
            )}
            {showLogin && loginBlock}
          </>
        )}

        {data && (
          <>
            {pollError && (
              <NoticePanel role="status">
                {t("queue.connectionLost", { reason: t(errorKey(pollError)) })}
              </NoticePanel>
            )}

            {schedule && schedule.state === "stale" && (
              <NoticePanel>
                {schedule.fetched_at
                  ? t("queue.schedule.stale", {
                      moment: formatInCourseTz(schedule.fetched_at, offset, lang, {
                        day: "2-digit",
                        month: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                        hourCycle: "h23",
                      }),
                    })
                  : t("queue.schedule.staleNoTime")}
              </NoticePanel>
            )}
            {schedule && schedule.state === "unavailable" && (
              <NoticePanel>{t("queue.schedule.unavailable")}</NoticePanel>
            )}

            {actionError && (
              <ErrorPanel role="alert">
                <strong>{t("queue.errorTitle")}</strong>
                <span>{t(errorKey(actionError.code))}</span>
              </ErrorPanel>
            )}

            {!data.viewer && showLogin && loginBlock}

            {/* Сначала своя запись или форма записи, потом «кто я»: на
                телефоне главное действие видно без прокрутки. */}
            {data.viewer && data.my_entry && (
              <MyEntry
                entry={data.my_entry}
                session={data.sessions.find((s) => s.id === data.my_entry.session_id)}
                formatTime={formatTime}
                busy={busy}
                onLeave={handleLeave}
                onSkip={handleSkip}
              />
            )}

            {data.viewer && !data.my_entry && (
              <SignupForm
                state={data}
                labs={labs}
                labsError={labsError}
                labsLoading={labsLoading}
                labParam={labParam}
                formatTime={formatTime}
                busy={busy}
                onReloadLabs={loadLabs}
                onSubmit={handleSignUp}
              />
            )}

            {lastResult && (
              <Description>
                {t("queue.lastResult", {
                  lab: lastResult.lab,
                  status: t(statusKey(lastResult.status)),
                  time: formatTime(lastResult.finished_at),
                })}
              </Description>
            )}

            {data.viewer && (
              <Details>
                <div>
                  <Label>{t("queue.viewer.name")}</Label>
                  <Value>{data.viewer.name}</Value>
                </div>
                <div>
                  <Label>{t("queue.viewer.group")}</Label>
                  <Value>{data.viewer.group}</Value>
                </div>
                <div>
                  <Label>{t("queue.viewer.github")}</Label>
                  <Value>{data.viewer.github}</Value>
                </div>
                <LinkButton type="button" onClick={handleLogout} disabled={Boolean(busy)}>
                  {t("queue.login.logout")}
                </LinkButton>
              </Details>
            )}

            <SectionTitle>{t("queue.list.title")}</SectionTitle>
            <SessionList
              sessions={data.sessions}
              signedIn={Boolean(data.viewer)}
              offset={offset}
              lang={lang}
            />

            <Hint>
              {t("queue.updatedAt", {
                time: formatInCourseTz(data.now, offset, lang, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                  hourCycle: "h23",
                }),
              })}
            </Hint>
          </>
        )}
      </QueueCard>
    </QueuePage>
  );
}
