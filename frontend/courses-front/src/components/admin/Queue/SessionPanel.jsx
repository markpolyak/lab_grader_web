import { useTranslation } from "react-i18next";
import {
  Alert,
  Button as MuiButton,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
} from "@mui/material";

import {
  formatInCourseTz,
  groupsLabel,
  sessionTimeRange,
  sessionTitle,
  signupText,
  statusKey,
} from "../../queue/state";
import {
  ACTION_CELL,
  ActionsRow,
  CallNextRow,
  CalledCard,
  CalledGrid,
  CalledMeta,
  CalledName,
  HeaderRow,
  HintText,
  NextUp,
  OutcomeButtons,
  SectionHeading,
  SessionBox,
  SummaryCount,
  SummaryItem,
  SummaryLabel,
  SummaryList,
  TableWrapper,
} from "./styled";
import {
  SIGNUP_COLOR,
  STATUS_COLOR,
  entryActions,
  outcomeButton,
  sessionActions,
  sessionSummary,
  splitEntries,
  summaryItems,
} from "./state";

const OUTCOME_BUTTONS = [
  { outcome: "defended", color: "success", variant: "contained" },
  { outcome: "not_defended", color: "error", variant: "outlined" },
  { outcome: "no_show", color: "warning", variant: "outlined" },
];


// Длинное ФИО в узком столбце телефона не должно сжиматься в столбик по
// слогу: столбец не уже 12em, ФИО переносится по словам и дефисам.
const NAME_CELL = { minWidth: "12em", overflowWrap: "break-word" };

// Поля кнопок исхода уже стандартных: «Не защитил» помещается в столбец
// карточки одной строкой.
const OUTCOME_SX = { px: 1 };

// На телефоне кнопка во всю ширину и в одну строку, на экране шире - по тексту.
const CALL_NEXT_SX = {
  fontSize: { xs: 16, sm: 18 },
  px: { xs: 2, sm: 4 },
  py: 1.5,
  width: { xs: "100%", sm: "auto" },
};


// Выбранная сессия дня. Блоки идут по частоте действий на паре: вызвать
// следующего, отметить исход, закрыть или открыть запись, итоги, очередь,
// завершённые. На телефоне главное так ближе к верху экрана, а «Завершить
// приём» не стоит прямо над «Вызвать следующего».
export function SessionPanel({
  session,
  nowIso,
  offset,
  lang,
  busy,
  onCallNext,
  onOutcome,
  onReturn,
  onClose,
  onReopen,
  onFinish,
  onFix,
}) {
  const { t } = useTranslation();
  const { called, waiting, finished } = splitEntries(session.entries);
  const summary = summaryItems(sessionSummary(session.entries));
  const actions = sessionActions(session, nowIso);
  const head = waiting[0] || null;
  const time = (iso) => formatInCourseTz(iso, offset, lang);

  return (
    <SessionBox>
      <HeaderRow>
        <div>
          <SectionHeading>{sessionTitle(session, t)}</SectionHeading>
          <HintText>
            {t("queue.session.groups", { groups: groupsLabel(session, t) })}
            {session.kind !== "all_day" && ` · ${sessionTimeRange(session, offset, lang)}`}
          </HintText>
        </div>
        <Chip
          color={SIGNUP_COLOR[session.signup] || "default"}
          label={signupText(session, t, offset, lang)}
        />
      </HeaderRow>

      {actions.dayOver && <Alert severity="info">{t("adminQueue.session.dayOver")}</Alert>}

      <CallNextRow>
        <MuiButton
          variant="contained"
          size="large"
          sx={CALL_NEXT_SX}
          disabled={busy || !actions.canCallNext}
          onClick={() => onCallNext(session, head)}
        >
          {t("adminQueue.callNext")}
        </MuiButton>
        <NextUp>
          {head
            ? t("adminQueue.nextUp", { name: head.name, group: head.group, lab: head.lab })
            : t("adminQueue.queueEmpty")}
        </NextUp>
      </CallNextRow>

      <SectionHeading>{t("adminQueue.called.title", { count: called.length })}</SectionHeading>
      {called.length ? (
        <CalledGrid>
          {called.map((entry) => {
            const { canReturn } = entryActions(entry, actions.dayOver);
            return (
              <CalledCard key={entry.id}>
                <CalledName>{entry.name}</CalledName>
                <CalledMeta>
                  {[entry.github, entry.group, entry.lab].filter(Boolean).join(" · ")} ·{" "}
                  {t("adminQueue.called.at", { time: time(entry.calledAt) })}
                </CalledMeta>
                <OutcomeButtons>
                  {OUTCOME_BUTTONS.map(({ outcome, color, variant }) => (
                    <MuiButton
                      key={outcome}
                      color={color}
                      variant={variant}
                      sx={OUTCOME_SX}
                      disabled={busy}
                      onClick={() => onOutcome(entry, outcome)}
                    >
                      {t(`adminQueue.outcome.${outcome}`)}
                    </MuiButton>
                  ))}
                  {canReturn && (
                    <MuiButton sx={OUTCOME_SX} disabled={busy} onClick={() => onReturn(entry)}>
                      {t("adminQueue.outcome.return")}
                    </MuiButton>
                  )}
                </OutcomeButtons>
              </CalledCard>
            );
          })}
        </CalledGrid>
      ) : (
        <HintText>{t("adminQueue.called.empty")}</HintText>
      )}

      {!actions.dayOver && (actions.canClose || actions.canReopen || actions.canFinish) && (
        <ActionsRow>
          {actions.canClose && (
            <MuiButton variant="outlined" disabled={busy} onClick={() => onClose(session)}>
              {t("adminQueue.session.close")}
            </MuiButton>
          )}
          {actions.canReopen && (
            <MuiButton variant="outlined" disabled={busy} onClick={() => onReopen(session)}>
              {t("adminQueue.session.reopen")}
            </MuiButton>
          )}
          {actions.canFinish && (
            <MuiButton variant="outlined" color="error" disabled={busy} onClick={() => onFinish(session)}>
              {t("adminQueue.session.finish")}
            </MuiButton>
          )}
        </ActionsRow>
      )}

      <SectionHeading>{t("adminQueue.summary.title")}</SectionHeading>
      <SummaryList>
        {summary.map(({ status, count }) => (
          <SummaryItem key={status} $status={status}>
            <SummaryLabel>{t(`adminQueue.summary.${status}`)}</SummaryLabel>
            <SummaryCount>{count}</SummaryCount>
          </SummaryItem>
        ))}
      </SummaryList>

      <SectionHeading>{t("adminQueue.waiting.title", { count: waiting.length })}</SectionHeading>
      {waiting.length ? (
        <TableWrapper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("adminQueue.columns.place")}</TableCell>
                <TableCell>{t("adminQueue.columns.name")}</TableCell>
                <TableCell>{t("adminQueue.columns.github")}</TableCell>
                <TableCell>{t("adminQueue.columns.lab")}</TableCell>
                <TableCell>{t("adminQueue.columns.createdAt")}</TableCell>
                <TableCell>{t("adminQueue.columns.group")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {waiting.map((entry) => (
                <TableRow key={entry.id}>
                  <TableCell>{entry.place}</TableCell>
                  <TableCell sx={NAME_CELL}>{entry.name}</TableCell>
                  <TableCell>{entry.github}</TableCell>
                  <TableCell>{entry.lab}</TableCell>
                  <TableCell>{time(entry.createdAt)}</TableCell>
                  <TableCell>{entry.group}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableWrapper>
      ) : (
        <HintText>{t("adminQueue.waiting.empty")}</HintText>
      )}

      {finished.length > 0 && (
        <>
          <SectionHeading>{t("adminQueue.finished.title", { count: finished.length })}</SectionHeading>
          <TableWrapper>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("adminQueue.columns.name")}</TableCell>
                  <TableCell>{t("adminQueue.columns.github")}</TableCell>
                  <TableCell>{t("adminQueue.columns.lab")}</TableCell>
                  <TableCell>{t("adminQueue.columns.status")}</TableCell>
                  <TableCell>{t("adminQueue.columns.createdAt")}</TableCell>
                  <TableCell>{t("adminQueue.columns.calledAt")}</TableCell>
                  <TableCell>{t("adminQueue.columns.finishedAt")}</TableCell>
                  <TableCell>{t("adminQueue.columns.group")}</TableCell>
                  <TableCell sx={ACTION_CELL} />
                </TableRow>
              </TableHead>
              <TableBody>
                {finished.map((entry) => {
                  const button = outcomeButton(entry, actions.dayOver);
                  return (
                    <TableRow key={entry.id}>
                      <TableCell sx={NAME_CELL}>{entry.name}</TableCell>
                      <TableCell>{entry.github}</TableCell>
                      <TableCell>{entry.lab}</TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          color={STATUS_COLOR[entry.status] || "default"}
                          label={t(statusKey(entry.status))}
                        />
                      </TableCell>
                      <TableCell>{time(entry.createdAt)}</TableCell>
                      <TableCell>{time(entry.calledAt) || "—"}</TableCell>
                      <TableCell>{time(entry.finishedAt) || "—"}</TableCell>
                      <TableCell>{entry.group}</TableCell>
                      <TableCell sx={ACTION_CELL}>
                        {button && (
                          <MuiButton size="small" disabled={busy} onClick={() => onFix(entry)}>
                            {button === "set" ? t("adminQueue.outcome.set") : t("adminQueue.outcome.fix")}
                          </MuiButton>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableWrapper>
        </>
      )}
    </SessionBox>
  );
}

