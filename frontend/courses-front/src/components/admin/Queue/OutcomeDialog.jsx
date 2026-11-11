import { useTranslation } from "react-i18next";
import {
  Button as MuiButton,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
} from "@mui/material";

import { statusKey } from "../../queue/state";
import { HintText, touchDialogSx } from "./styled";
import { entryActions } from "./state";


// Исправление исхода уже завершённой записи (ошиблись кнопкой) и отметка
// забытого исхода у записи, закрытой в конце дня. Без onReturn (история)
// кнопки «Вернуть в очередь» нет.
export function OutcomeDialog({ entry, dayOver, busy, onOutcome, onReturn, onClose }) {
  const { t } = useTranslation();
  const { outcomes, canReturn } = entryActions(entry, dayOver);

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth sx={touchDialogSx}>
      <DialogTitle>{t("adminQueue.outcome.fixTitle", { name: entry.name })}</DialogTitle>
      <DialogContent>
        <HintText>
          {entry.group} · {entry.lab} · {t("adminQueue.outcome.current", { status: t(statusKey(entry.status)) })}
        </HintText>
        <Stack direction="row" spacing={1} useFlexGap sx={{ mt: 2, flexWrap: "wrap" }}>
          {outcomes.map((outcome) => (
            <MuiButton key={outcome} variant="outlined" disabled={busy} onClick={() => onOutcome(entry, outcome)}>
              {t(`adminQueue.outcome.${outcome}`)}
            </MuiButton>
          ))}
          {canReturn && onReturn && (
            <MuiButton disabled={busy} onClick={() => onReturn(entry)}>
              {t("adminQueue.outcome.return")}
            </MuiButton>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <MuiButton onClick={onClose} disabled={busy}>
          {t("adminQueue.cancel")}
        </MuiButton>
      </DialogActions>
    </Dialog>
  );
}
