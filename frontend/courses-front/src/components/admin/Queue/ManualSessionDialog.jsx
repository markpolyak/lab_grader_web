import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button as MuiButton,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  LinearProgress,
  Radio,
  RadioGroup,
  TextField,
} from "@mui/material";

import { errorKey } from "../../queue/state";
import { HintText, touchDialogSx } from "./styled";
import { manualRequest } from "./state";


// Ручное открытие записи: перенесённое или дополнительное занятие, которого
// нет в расписании. Время - в поясе курса, пустое - до конца дня.
export function ManualSessionDialog({
  groups,
  groupsLoading,
  groupsError,
  busy,
  onRetryGroups,
  onClose,
  onSubmit,
}) {
  const { t } = useTranslation();
  const [allGroups, setAllGroups] = useState(false);
  const [selected, setSelected] = useState([]);
  const [closesAt, setClosesAt] = useState("");
  const [formError, setFormError] = useState(null);

  const toggle = (group) =>
    setSelected((current) =>
      current.includes(group) ? current.filter((g) => g !== group) : [...current, group]
    );

  const submit = (event) => {
    event.preventDefault();
    const request = manualRequest({ allGroups, selected, closesAt });
    if (request.error) {
      setFormError(request.error);
      return;
    }
    setFormError(null);
    onSubmit(request);
  };

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth sx={touchDialogSx}>
      <form onSubmit={submit}>
        <DialogTitle>{t("adminQueue.manual.title")}</DialogTitle>
        <DialogContent>
          <HintText>{t("adminQueue.manual.description")}</HintText>
          <RadioGroup
            value={allGroups ? "all" : "some"}
            onChange={(event) => setAllGroups(event.target.value === "all")}
          >
            <FormControlLabel value="some" control={<Radio size="small" />} label={t("adminQueue.manual.pickGroups")} />
            <FormControlLabel value="all" control={<Radio size="small" />} label={t("adminQueue.manual.allGroups")} />
          </RadioGroup>

          {!allGroups &&
            (groupsLoading ? (
              <LinearProgress sx={{ my: 1 }} />
            ) : groupsError ? (
              <Alert
                severity="error"
                action={
                  <MuiButton color="inherit" size="small" onClick={onRetryGroups}>
                    {t("adminQueue.retry")}
                  </MuiButton>
                }
              >
                {t("adminQueue.manual.groupsError")}
              </Alert>
            ) : (
              <FormGroup sx={{ pl: 3 }}>
                {(groups || []).map((group) => (
                  <FormControlLabel
                    key={group}
                    control={
                      <Checkbox size="small" checked={selected.includes(group)} onChange={() => toggle(group)} />
                    }
                    label={group}
                  />
                ))}
              </FormGroup>
            ))}

          <TextField
            type="time"
            size="small"
            fullWidth
            sx={{ mt: 2 }}
            label={t("adminQueue.manual.closesAt")}
            helperText={t("adminQueue.manual.closesAtHint")}
            value={closesAt}
            onChange={(event) => setClosesAt(event.target.value)}
            slotProps={{ inputLabel: { shrink: true } }}
          />

          {formError && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              {t(errorKey(formError))}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <MuiButton onClick={onClose} disabled={busy}>
            {t("adminQueue.cancel")}
          </MuiButton>
          <MuiButton type="submit" variant="contained" disabled={busy}>
            {t("adminQueue.manual.submit")}
          </MuiButton>
        </DialogActions>
      </form>
    </Dialog>
  );
}
