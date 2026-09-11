import { useTranslation } from "react-i18next";

import {
  MemberChip,
  SecondaryButton,
  TeamCard,
  TeamCards,
  TeamCount,
  TeamHeader,
  TeamMembers,
  TeamName,
  Description,
  FieldHint,
} from "./styled";


// Логины участников показываются намеренно: это публичные идентификаторы
// GitHub, и именно по ним студент узнаёт команду своих однокурсников. ФИО не
// показываются - сценарий /join не знает группу студента и не открывает
// Google Таблицу.
export function TeamList({ teams, sizeMax, myTeam, busySlug, onJoin }) {
  const { t } = useTranslation();

  if (!teams.length) {
    return <Description>{t("join.team.empty")}</Description>;
  }

  return (
    <TeamCards>
      {teams.map((team) => {
        const isMine = team.slug === myTeam;
        const disabled =
          Boolean(myTeam) || team.is_full || team.members_unknown || Boolean(busySlug);

        return (
          <TeamCard key={team.slug} $mine={isMine}>
            <TeamHeader>
              <TeamName>{team.title || team.slug}</TeamName>
              <TeamCount>
                {sizeMax
                  ? t("join.team.sizeOf", { size: team.size, max: sizeMax })
                  : t("join.team.size", { count: team.size })}
              </TeamCount>
            </TeamHeader>

            {team.description && <FieldHint>{team.description}</FieldHint>}

            {team.members_unknown ? (
              <FieldHint>{t("join.team.membersUnknown")}</FieldHint>
            ) : (
              <TeamMembers>
                {team.members.map((login) => (
                  <MemberChip key={login}>{login}</MemberChip>
                ))}
                {team.pending.map((login) => {
                  // Истёкшее приглашение по-прежнему занимает место - см. TeamRegistry._read_roster.
                  const isExpired = (team.expired ?? []).includes(login);
                  return (
                    <MemberChip
                      key={login}
                      $pending
                      title={t(isExpired ? "join.team.expiredHint" : "join.team.pendingHint")}
                    >
                      {login} · {t(isExpired ? "join.team.expired" : "join.team.pending")}
                    </MemberChip>
                  );
                })}
              </TeamMembers>
            )}

            {!isMine && (
              <SecondaryButton
                type="button"
                disabled={disabled}
                onClick={() => onJoin(team.slug)}
              >
                {busySlug === team.slug
                  ? t("join.team.joining")
                  : team.is_full
                    ? t("join.team.full")
                    : t("join.team.join")}
              </SecondaryButton>
            )}
          </TeamCard>
        );
      })}
    </TeamCards>
  );
}
