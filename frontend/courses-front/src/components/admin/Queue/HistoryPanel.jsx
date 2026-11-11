import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button as MuiButton,
  Chip,
  LinearProgress,
  MenuItem,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
} from "@mui/material";

import { statusKey } from "../../queue/state";
import { OutcomeDialog } from "./OutcomeDialog";
import { ACTION_CELL, FilterRow, HintText, TableWrapper } from "./styled";
import {
  HISTORY_PAGE_SIZE,
  STATUS_COLOR,
  adminErrorKeys,
  entryDay,
  historyDayLabel,
  historyErrorCode,
  historyFileName,
  historyMoment,
  historyOutcomeButton,
  historyPage,
  historyPeriodError,
  historyQuery,
  historyToCsv,
  normalizeEntry,
} from "./state";

const EMPTY_FILTERS = { from: "", to: "", group: "", q: "", lab: "" };


// Строки одной страницы. Отдельный memo: ввод в фильтры и опрос вкладки
// «Сегодня» их не перерисовывают.
const HistoryRows = memo(function HistoryRows({ rows, offset, lang, busy, onPick }) {
  const { t } = useTranslation();
  return (
    <TableBody>
      {rows.map((entry) => {
        const button = historyOutcomeButton(entry);
        const day = entryDay(entry, offset);
        return (
          <TableRow key={entry.id}>
            <TableCell>{historyDayLabel(day, lang)}</TableCell>
            <TableCell>{historyMoment(entry.createdAt, day, offset, lang)}</TableCell>
            <TableCell>{entry.name}</TableCell>
            <TableCell>{entry.group}</TableCell>
            <TableCell>{entry.github}</TableCell>
            <TableCell>{entry.lab}</TableCell>
            <TableCell>
              <Chip
                size="small"
                color={STATUS_COLOR[entry.status] || "default"}
                label={t(statusKey(entry.status))}
              />
            </TableCell>
            <TableCell>{historyMoment(entry.calledAt, day, offset, lang) || "—"}</TableCell>
            <TableCell>{historyMoment(entry.finishedAt, day, offset, lang) || "—"}</TableCell>
            <TableCell sx={ACTION_CELL}>
              {button && (
                <MuiButton size="small" disabled={busy} onClick={() => onPick(entry)}>
                  {button === "set" ? t("adminQueue.outcome.set") : t("adminQueue.outcome.fix")}
                </MuiButton>
              )}
            </TableCell>
          </TableRow>
        );
      })}
    </TableBody>
  );
});


// История очереди курса: все записи с исходами за период, и у архивного
// курса тоже. Здесь же отмечают забытый исход и исправляют исход прошлых
// дней - на вкладке «Сегодня» этих записей уже нет. memo: опрос «Сегодня»
// раз в 5 с не должен перерисовывать скрытую историю.
export const HistoryPanel = memo(function HistoryPanel({
  api,
  courseId,
  offset,
  lang,
  groups,
  busy,
  onNeedGroups,
  onUnauthorized,
  onOutcome,
}) {
  const { t } = useTranslation();
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [labs, setLabs] = useState([]);
  const [fixTarget, setFixTarget] = useState(null);
  const [page, setPage] = useState(0);
  // Фильтры показанной таблицы: после исхода она перечитывается по ним, а
  // не по полям формы, которые могли поменять, но не применить.
  const appliedRef = useRef(EMPTY_FILTERS);

  const load = useCallback(
    (query) => {
      appliedRef.current = query;
      setLoading(true);
      setError(null);
      return api
        .getHistory(courseId, historyQuery(query))
        .then((data) => {
          setResult(data);
          // Период по умолчанию (30 дней) выбирает сервер - показываем его.
          setFilters((current) => ({
            ...current,
            from: current.from || (data && data.from) || "",
            to: current.to || (data && data.to) || "",
          }));
        })
        .catch((err) => {
          if (err.status === 401) {
            onUnauthorized();
            return;
          }
          // Старая таблица под ошибкой выглядела бы ответом на новый фильтр,
          // а CSV выгрузил бы её.
          setResult(null);
          setError(historyErrorCode(err.code));
        })
        .finally(() => setLoading(false));
    },
    [api, courseId, onUnauthorized]
  );

  useEffect(() => {
    onNeedGroups();
    load(EMPTY_FILTERS);
    api
      .getAdminLabs(courseId)
      .then((list) => setLabs(Array.isArray(list) ? list : []))
      .catch(() => setLabs([]));
  }, [api, courseId, load, onNeedGroups]);

  const setFilter = (name) => (event) =>
    setFilters((current) => ({ ...current, [name]: event.target.value }));

  const submit = (event) => {
    event.preventDefault();
    const periodError = historyPeriodError(filters);
    if (periodError) {
      setResult(null);
      setError(periodError);
      return;
    }
    setPage(0);
    load(filters);
  };

  const handleOutcome = async (entry, outcome) => {
    const done = await onOutcome(entry, outcome);
    if (!done) return;
    setFixTarget(null);
    load(appliedRef.current);
  };

  const entries = useMemo(() => (result && Array.isArray(result.entries) ? result.entries : []), [result]);
  const rows = useMemo(() => entries.map(normalizeEntry), [entries]);
  const shown = useMemo(() => historyPage(rows, page), [rows, page]);

  const downloadCsv = () => {
    const csv = historyToCsv(entries, { t, offsetMinutes: offset });
    // BOM: без него Excel читает UTF-8 как cp1251 и портит кириллицу.
    const blob = new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = historyFileName(courseId, result && result.from, result && result.to);
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const pager = (
    <TablePagination
      component="div"
      count={rows.length}
      page={shown.page}
      rowsPerPage={HISTORY_PAGE_SIZE}
      rowsPerPageOptions={[]}
      onPageChange={(_event, value) => setPage(value)}
      showFirstButton
      showLastButton
      labelDisplayedRows={({ from, to, count }) => t("adminQueue.history.pageRange", { from, to, count })}
      getItemAriaLabel={(type) => t(`adminQueue.history.page.${type}`)}
    />
  );

  return (
    <>
      <form onSubmit={submit}>
        <FilterRow>
          <TextField
            type="date"
            size="small"
            label={t("adminQueue.history.from")}
            value={filters.from}
            onChange={setFilter("from")}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            type="date"
            size="small"
            label={t("adminQueue.history.to")}
            value={filters.to}
            onChange={setFilter("to")}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            select
            size="small"
            sx={{ minWidth: 140 }}
            label={t("adminQueue.history.group")}
            value={filters.group}
            onChange={setFilter("group")}
          >
            <MenuItem value="">{t("adminQueue.history.anyGroup")}</MenuItem>
            {(groups || []).map((group) => (
              <MenuItem key={group} value={group}>
                {group}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            sx={{ minWidth: 140 }}
            label={t("adminQueue.history.lab")}
            value={filters.lab}
            onChange={setFilter("lab")}
          >
            <MenuItem value="">{t("adminQueue.history.anyLab")}</MenuItem>
            {labs.map((lab) => (
              <MenuItem key={lab.id} value={String(lab.id)}>
                {lab.short_name || lab.id}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            sx={{ minWidth: 220 }}
            label={t("adminQueue.history.query")}
            value={filters.q}
            onChange={setFilter("q")}
          />
          <MuiButton type="submit" variant="contained" disabled={loading}>
            {t("adminQueue.history.apply")}
          </MuiButton>
          <MuiButton variant="outlined" disabled={loading || Boolean(error) || !rows.length} onClick={downloadCsv}>
            {t("adminQueue.history.download")}
          </MuiButton>
        </FilterRow>
      </form>

      {loading && <LinearProgress />}
      {error && <Alert severity="error">{t(adminErrorKeys(error))}</Alert>}

      {result && !loading && (
        <HintText>
          {rows.length
            ? t("adminQueue.history.count", { count: rows.length })
            : t("adminQueue.history.empty")}
        </HintText>
      )}
      {result && !loading && result.truncated && (
        <Alert severity="warning">{t("adminQueue.history.truncated", { count: rows.length })}</Alert>
      )}

      {rows.length > HISTORY_PAGE_SIZE && pager}
      {rows.length > 0 && (
        <TableWrapper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("adminQueue.columns.day")}</TableCell>
                <TableCell>{t("adminQueue.columns.createdAt")}</TableCell>
                <TableCell>{t("adminQueue.columns.name")}</TableCell>
                <TableCell>{t("adminQueue.columns.group")}</TableCell>
                <TableCell>{t("adminQueue.columns.github")}</TableCell>
                <TableCell>{t("adminQueue.columns.lab")}</TableCell>
                <TableCell>{t("adminQueue.columns.status")}</TableCell>
                <TableCell>{t("adminQueue.columns.calledAt")}</TableCell>
                <TableCell>{t("adminQueue.columns.finishedAt")}</TableCell>
                <TableCell sx={ACTION_CELL} />
              </TableRow>
            </TableHead>
            <HistoryRows rows={shown.rows} offset={offset} lang={lang} busy={busy} onPick={setFixTarget} />
          </Table>
        </TableWrapper>
      )}
      {rows.length > HISTORY_PAGE_SIZE && pager}

      {fixTarget && (
        <OutcomeDialog
          entry={fixTarget}
          dayOver
          busy={busy}
          onOutcome={handleOutcome}
          onClose={() => setFixTarget(null)}
        />
      )}
    </>
  );
});
