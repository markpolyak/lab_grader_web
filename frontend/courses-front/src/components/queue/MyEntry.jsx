import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ButtonRow,
  CalledTitle,
  DangerButton,
  Description,
  Label,
  Meta,
  MyEntryPanel,
  PlaceNumber,
  PlaceRow,
  SecondaryButton,
} from "./styled";
import { sessionTitle } from "./state";


// Своя запись студента. «Выйти» и «Пропустить вперёд» необратимы (встать
// обратно можно только в конец), поэтому оба просят подтверждения.
export function MyEntry({ entry, session, formatTime, busy, onLeave, onSkip }) {
  const { t } = useTranslation();
  // Подтверждение привязано к записи: после выхода и новой записи оно не
  // должно всплыть само.
  const [confirm, setConfirm] = useState(null);
  const pending = confirm && confirm.entryId === entry.id ? confirm.kind : null;

  if (entry.status === "called") {
    return (
      <MyEntryPanel $called role="status">
        <CalledTitle>{t("queue.me.calledTitle")}</CalledTitle>
        <Description>{t("queue.me.calledText", { lab: entry.lab })}</Description>
        {entry.called_at && <Meta>{t("queue.me.calledAt", { time: formatTime(entry.called_at) })}</Meta>}
      </MyEntryPanel>
    );
  }

  const confirmAction = () => {
    setConfirm(null);
    if (pending === "leave") onLeave();
    else if (pending === "skip") onSkip();
  };

  return (
    <MyEntryPanel>
      <Label>{t("queue.me.title")}</Label>
      <PlaceRow>
        <PlaceNumber>{entry.place}</PlaceNumber>
        <Description>{entry.place === 1 ? t("queue.me.placeFirst") : t("queue.me.placeCaption")}</Description>
      </PlaceRow>
      <Meta>
        {t("queue.me.details", { lab: entry.lab, time: formatTime(entry.created_at) })}
        {session ? ` · ${sessionTitle(session, t)}` : ""}
      </Meta>

      {pending ? (
        <>
          <Description>
            {pending === "leave" ? t("queue.me.leaveConfirm") : t("queue.me.skipConfirm")}
          </Description>
          <ButtonRow>
            <DangerButton type="button" onClick={confirmAction} disabled={Boolean(busy)}>
              {pending === "leave" ? t("queue.me.leaveYes") : t("queue.me.skipYes")}
            </DangerButton>
            <SecondaryButton type="button" onClick={() => setConfirm(null)}>
              {t("queue.me.cancel")}
            </SecondaryButton>
          </ButtonRow>
        </>
      ) : (
        <ButtonRow>
          {entry.can_skip && (
            <SecondaryButton
              type="button"
              onClick={() => setConfirm({ kind: "skip", entryId: entry.id })}
              disabled={Boolean(busy)}
            >
              {busy === "skip" ? t("queue.me.working") : t("queue.me.skip")}
            </SecondaryButton>
          )}
          <DangerButton
            type="button"
            onClick={() => setConfirm({ kind: "leave", entryId: entry.id })}
            disabled={Boolean(busy)}
          >
            {busy === "leave" ? t("queue.me.working") : t("queue.me.leave")}
          </DangerButton>
        </ButtonRow>
      )}
    </MyEntryPanel>
  );
}
