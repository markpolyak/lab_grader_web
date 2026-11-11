import { useTranslation } from "react-i18next";

import {
  Badge,
  Description,
  EntryList,
  EntryPlace,
  EntryRow,
  Meta,
  SessionCard,
  SessionHeader,
  SessionName,
  SessionList as SessionCards,
} from "./styled";
import { groupsLabel, sessionTimeRange, sessionTitle, signupText } from "./state";


// Сессии дня: состояние записи и счётчики, а в сессиях своей группы - кто
// стоит в очереди. Порядок записей задаёт сервер: сначала вызванные, затем
// ждущие по месту (номер места есть только у ждущих).
export function SessionList({ sessions, signedIn, offset, lang }) {
  const { t } = useTranslation();

  if (!sessions.length) {
    return <Description>{t("queue.noSessions")}</Description>;
  }

  return (
    <SessionCards>
      {sessions.map((session) => {
        const mine = !signedIn || session.for_me;
        return (
          <SessionCard key={session.id} $mine={mine}>
            <SessionHeader>
              <SessionName>{sessionTitle(session, t)}</SessionName>
              <Badge $tone={session.signup}>{signupText(session, t, offset, lang)}</Badge>
            </SessionHeader>
            <Meta>
              {t("queue.session.groups", { groups: groupsLabel(session, t) })}
              {session.kind !== "all_day" && ` · ${sessionTimeRange(session, offset, lang)}`}
            </Meta>
            <Meta>
              {t("queue.session.counts", {
                waiting: session.waiting_count ?? 0,
                called: session.called_count ?? 0,
              })}
            </Meta>
            {signedIn && !session.for_me && <Meta>{t("queue.session.notForMe")}</Meta>}

            {Array.isArray(session.entries) &&
              (session.entries.length ? (
                <EntryList>
                  {session.entries.map((entry, index) => (
                    <EntryRow key={`${entry.status}-${entry.place ?? index}-${index}`} $me={entry.is_me}>
                      <EntryPlace>{entry.status === "waiting" ? entry.place : "→"}</EntryPlace>
                      <span>
                        {entry.name} · {entry.group} · {entry.lab}
                        {entry.is_me ? ` (${t("queue.list.you")})` : ""}
                      </span>
                      {entry.status === "called" && <Badge $tone="called">{t("queue.status.called")}</Badge>}
                    </EntryRow>
                  ))}
                </EntryList>
              ) : (
                <Meta>{t("queue.list.empty")}</Meta>
              ))}
          </SessionCard>
        );
      })}
    </SessionCards>
  );
}
