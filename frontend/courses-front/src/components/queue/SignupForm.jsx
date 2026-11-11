import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ActionButton,
  Description,
  ErrorPanel,
  Field,
  Form,
  Hint,
  LinkButton,
  Meta,
  SectionTitle,
  Select,
  Spinner,
} from "./styled";
import {
  errorKey,
  isLabSelectable,
  labsView,
  openSessionsForMe,
  resolveSignupChoice,
  sessionTitle,
  upcomingSessionsForMe,
} from "./state";


function labOptionText(lab, t) {
  if (lab.status === "defended") return t("queue.form.labDefended", { lab: lab.short_name });
  if (lab.status === "not_passed") return t("queue.form.labNotPassed", { lab: lab.short_name });
  return lab.short_name;
}


// Форма записи: открытая сессия своей группы и работа, прошедшая проверку.
export function SignupForm({
  state,
  labs,
  labsError,
  labsLoading,
  labParam,
  formatTime,
  busy,
  onReloadLabs,
  onSubmit,
}) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState({ sessionId: null, labId: null });

  const openSessions = openSessionsForMe(state);
  const upcoming = upcomingSessionsForMe(state);
  const labList = labs && Array.isArray(labs.labs) ? labs.labs : null;
  const view = labsView({ labs, labsError, labsLoading });
  const choice = resolveSignupChoice({ openSessions, labs: labList, current, labParam });
  const selectedLab = labList && labList.find((lab) => String(lab.id) === choice.labId);

  if (!openSessions.length) {
    return (
      <Form as="div">
        <SectionTitle>{t("queue.form.title")}</SectionTitle>
        <Description>
          {upcoming.length
            ? t("queue.form.opensAt", { time: formatTime(upcoming[0].signup_opens_at) })
            : t("queue.form.closedForMe")}
        </Description>
      </Form>
    );
  }

  const handleSubmit = (event) => {
    event.preventDefault();
    if (choice.sessionId === null || choice.labId === null) return;
    onSubmit(choice);
  };

  const sessionLabel = (session) =>
    `${sessionTitle(session, t)} · ${t("queue.form.until", {
      time: formatTime(session.signup_closes_at),
    })}`;

  return (
    <Form onSubmit={handleSubmit}>
      <SectionTitle>{t("queue.form.title")}</SectionTitle>

      {openSessions.length > 1 ? (
        <Field>
          {t("queue.form.session")}
          <Select
            value={choice.sessionId ?? ""}
            onChange={(event) => setCurrent({ ...choice, sessionId: Number(event.target.value) })}
          >
            {openSessions.map((session) => (
              <option key={session.id} value={session.id}>
                {sessionLabel(session)}
              </option>
            ))}
          </Select>
        </Field>
      ) : (
        <Meta>{sessionLabel(openSessions[0])}</Meta>
      )}

      {view === "error" ? (
        <ErrorPanel role="alert">
          <span>{t(errorKey(labsError))}</span>
          <LinkButton type="button" onClick={onReloadLabs} disabled={labsLoading}>
            {t("queue.retry")}
          </LinkButton>
        </ErrorPanel>
      ) : view === "loading" ? (
        <Description role="status">
          <Spinner aria-hidden="true" />
          {t("queue.form.labsLoading")}
        </Description>
      ) : view === "idle" ? (
        <Description>
          {t("queue.form.labsNotLoaded")}{" "}
          <LinkButton type="button" onClick={onReloadLabs}>
            {t("queue.retry")}
          </LinkButton>
        </Description>
      ) : view === "empty" ? (
        <Description>{t("queue.form.noLabs")}</Description>
      ) : (
        <Field>
          {t("queue.form.lab")}
          <Select
            value={choice.labId ?? ""}
            onChange={(event) => setCurrent({ ...choice, labId: event.target.value || null })}
          >
            {choice.labId === null && <option value="">{t("queue.form.chooseLab")}</option>}
            {labList.map((lab) => (
              <option key={lab.id} value={String(lab.id)} disabled={!isLabSelectable(lab)}>
                {labOptionText(lab, t)}
              </option>
            ))}
          </Select>
        </Field>
      )}

      {selectedLab && selectedLab.status === "not_passed" && (
        <Hint>{t("queue.form.notPassedHint", { time: formatTime(labs.fetched_at) })}</Hint>
      )}

      {labList && labList.length > 0 && (
        <LinkButton type="button" onClick={onReloadLabs} disabled={labsLoading}>
          {labsLoading ? t("queue.form.labsLoading") : t("queue.form.labsRefresh")}
        </LinkButton>
      )}

      <ActionButton
        type="submit"
        disabled={Boolean(busy) || choice.sessionId === null || choice.labId === null}
      >
        {busy === "signup" ? t("queue.form.submitting") : t("queue.form.submit")}
      </ActionButton>
    </Form>
  );
}
