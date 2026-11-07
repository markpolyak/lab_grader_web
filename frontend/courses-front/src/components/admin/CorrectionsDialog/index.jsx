import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormHelperText,
  Grid,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { load, dump } from "js-yaml";

async function fetchJson(url, options) {
  const response = await fetch(url, { credentials: "include", ...options });
  let data = null;
  try {
    data = await response.json();
  } catch {
  }
  if (!response.ok) {
    const error = new Error(data?.detail || (typeof data === "string" ? data : `HTTP ${response.status}`));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

const WEEKDAYS = [
  { value: "monday", label: "Понедельник" },
  { value: "tuesday", label: "Вторник" },
  { value: "wednesday", label: "Среда" },
  { value: "thursday", label: "Четверг" },
  { value: "friday", label: "Пятница" },
  { value: "saturday", label: "Суббота" },
  { value: "sunday", label: "Воскресенье" },
];

const WEEK_PARITY = [
  { value: "all", label: "Все недели" },
  { value: "odd", label: "Нечётные" },
  { value: "even", label: "Чётные" },
];

export const CorrectionsDialog = ({ courseId, groupId, open, onClose, onSaved }) => {
  const { t } = useTranslation();
  const [corrections, setCorrections] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState({
    id: "",
    date: "",
    type: "remove",
    slot_id: "",
    labs: "",
    new_slot_id: "",
    recurrence_day: "",
    recurrence_week: "all",
    date_once: "",
    start: "",
    end: "",
    subject: "",
    room: "",
    teacher: "",
  });

  const loadCorrections = useCallback(async () => {
    if (!courseId || !groupId) return;
    setLoading(true);
    setError(null);
    try {
      const scheduleYaml = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`);
      const parsed = load(scheduleYaml.content);
      const group = parsed.groups?.[groupId];
      if (group?.corrections) {
        setCorrections(group.corrections.map((c, i) => ({ ...c, _index: i })));
      } else {
        setCorrections([]);
      }
    } catch (err) {
      setError(err.message || t("schedule.corrections.errors.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [courseId, groupId, t]);

  useEffect(() => {
    if (open) loadCorrections();
  }, [open, loadCorrections]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const scheduleYaml = await fetchJson(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`);
      const parsed = load(scheduleYaml.content);
      
      if (!parsed.groups) parsed.groups = {};
      if (!parsed.groups[groupId]) parsed.groups[groupId] = {};
      
      parsed.groups[groupId].corrections = corrections.map(({ _index, ...c }) => c);
      
      const newContent = dump(parsed, { lineWidth: -1, noRefs: true });
      
      await fetchJson(
        `/api/v1/admin/courses/${encodeURIComponent(courseId)}/schedule/edit`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: newContent }),
        }
      );
      
      if (onSaved) onSaved();
      onClose();
    } catch (err) {
      setError(err.message || t("schedule.corrections.errors.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleAddCorrection = () => {
    setEditingId("new");
    setFormData({
      id: "",
      date: "",
      type: "remove",
      slot_id: "",
      labs: "",
      new_slot_id: "",
      recurrence_day: "",
      recurrence_week: "all",
      date_once: "",
      start: "",
      end: "",
      subject: "",
      room: "",
      teacher: "",
    });
  };

  const handleEditCorrection = (corr) => {
    setEditingId(corr._index);
    if (corr.remove_slots && corr.remove_slots.length > 0) {
      setFormData({
        id: corr.id,
        date: corr.date,
        type: "remove",
        slot_id: corr.remove_slots[0].slot_id || "",
        labs: corr.remove_slots[0].labs ? corr.remove_slots[0].labs.join(",") : "",
        new_slot_id: "",
        recurrence_day: "",
        recurrence_week: "all",
        date_once: "",
        start: "",
        end: "",
        subject: "",
        room: "",
        teacher: "",
      });
    } else if (corr.add_slots && corr.add_slots.length > 0) {
      const as = corr.add_slots[0];
      setFormData({
        id: corr.id,
        date: corr.date,
        type: "add",
        slot_id: "",
        labs: "",
        new_slot_id: as.id,
        recurrence_day: as.recurrence?.day || "",
        recurrence_week: as.recurrence?.week || "all",
        date_once: as.date || "",
        start: as.start || "",
        end: as.end || "",
        subject: as.subject || "",
        room: as.room || "",
        teacher: as.teacher || "",
      });
    }
  };

  const handleDeleteCorrection = (index) => {
    setCorrections(corrections.filter((_, i) => i !== index));
  };

  const handleFormSave = () => {
    const { id, date, type, slot_id, labs, new_slot_id, recurrence_day, recurrence_week, date_once, start, end, subject, room, teacher } = formData;
    
    if (!id || !date) {
      setError(t("schedule.corrections.errors.requiredFields"));
      return;
    }
    
    const newCorr = { id, date };
    
    if (type === "remove") {
      if (!slot_id) {
        setError(t("schedule.corrections.errors.slotRequired"));
        return;
      }
      newCorr.remove_slots = [{ slot_id, labs: labs ? labs.split(",").map(s => s.trim()) : undefined }];
    } else {
      if (!new_slot_id || !start || !end) {
        setError(t("schedule.corrections.errors.slotRequired"));
        return;
      }
      const addSlot = { id: new_slot_id, start, end };
      if (subject) addSlot.subject = subject;
      if (room) addSlot.room = room;
      if (teacher) addSlot.teacher = teacher;
      if (date_once) {
        addSlot.date = date_once;
      } else if (recurrence_day) {
        addSlot.recurrence = { day: recurrence_day, week: recurrence_week };
      }
      newCorr.add_slots = [addSlot];
    }
    
    if (editingId === "new") {
      setCorrections([...corrections, { ...newCorr, _index: corrections.length }]);
    } else {
      setCorrections(corrections.map((c, i) => i === editingId ? { ...newCorr, _index: i } : c));
    }
    setEditingId(null);
  };

  const handleCancelEdit = () => {
    setEditingId(null);
  };

  if (!open) return null;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="lg">
      <DialogTitle>{t("schedule.corrections.title", { groupId })}</DialogTitle>
      <DialogContent style={{ maxHeight: "80vh", overflow: "auto" }}>
        {error && <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2, mb: 1 }}>{error}</Alert>}
        
        {editingId !== null ? (
          <Stack spacing={2} sx={{ p: 2, mt: 1 }}>
            <Typography variant="h6">{editingId === "new" ? t("schedule.corrections.addTitle") : t("schedule.corrections.editTitle")}</Typography>
            
            <Grid container spacing={2}>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label={t("schedule.corrections.fields.id")}
                  value={formData.id}
                  onChange={e => setFormData({ ...formData, id: e.target.value })}
                  required
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  type="date"
                  label={t("schedule.corrections.fields.date")}
                  value={formData.date}
                  onChange={e => setFormData({ ...formData, date: e.target.value })}
                  InputLabelProps={{ shrink: true }}
                  required
                />
              </Grid>
              <Grid item xs={12}>
                <FormControl fullWidth>
                  <InputLabel id="type-label">{t("schedule.corrections.fields.type")}</InputLabel>
                  <Select
                    labelId="type-label"
                    value={formData.type}
                    label={t("schedule.corrections.fields.type")}
                    onChange={e => setFormData({ ...formData, type: e.target.value })}
                  >
                    <MenuItem value="remove">{t("schedule.corrections.types.remove")}</MenuItem>
                    <MenuItem value="add">{t("schedule.corrections.types.add")}</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              
              {formData.type === "remove" && (
                <>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      label={t("schedule.corrections.fields.slotId")}
                      value={formData.slot_id}
                      onChange={e => setFormData({ ...formData, slot_id: e.target.value })}
                      required
                    />
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      label={t("schedule.corrections.fields.labs")}
                      value={formData.labs}
                      onChange={e => setFormData({ ...formData, labs: e.target.value })}
                      placeholder="lab1,lab2 (пусто = весь слот)"
                    />
                    <FormHelperText>{t("schedule.corrections.fields.labsHint")}</FormHelperText>
                  </Grid>
                </>
              )}
              
              {formData.type === "add" && (
                <>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      label={t("schedule.corrections.fields.newSlotId")}
                      value={formData.new_slot_id}
                      onChange={e => setFormData({ ...formData, new_slot_id: e.target.value })}
                      required
                    />
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <FormControl fullWidth>
                      <InputLabel id="recurrence-day-label">{t("schedule.corrections.fields.recurrenceDay")}</InputLabel>
                      <Select
                        labelId="recurrence-day-label"
                        value={formData.recurrence_day}
                        label={t("schedule.corrections.fields.recurrenceDay")}
                        onChange={e => setFormData({ ...formData, recurrence_day: e.target.value, date_once: "" })}
                      >
                        {WEEKDAYS.map(w => <MenuItem key={w.value} value={w.value}>{w.label}</MenuItem>)}
                      </Select>
                      <FormHelperText>{t("schedule.corrections.fields.recurrenceDayHint")}</FormHelperText>
                    </FormControl>
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <FormControl fullWidth>
                      <InputLabel id="recurrence-week-label">{t("schedule.corrections.fields.recurrenceWeek")}</InputLabel>
                      <Select
                        labelId="recurrence-week-label"
                        value={formData.recurrence_week}
                        label={t("schedule.corrections.fields.recurrenceWeek")}
                        onChange={e => setFormData({ ...formData, recurrence_week: e.target.value })}
                      >
                        {WEEK_PARITY.map(w => <MenuItem key={w.value} value={w.value}>{w.label}</MenuItem>)}
                      </Select>
                    </FormControl>
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      type="date"
                      label={t("schedule.corrections.fields.dateOnce")}
                      value={formData.date_once}
                      onChange={e => setFormData({ ...formData, date_once: e.target.value, recurrence_day: "" })}
                      InputLabelProps={{ shrink: true }}
                    />
                    <FormHelperText>{t("schedule.corrections.fields.dateOnceHint")}</FormHelperText>
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      type="time"
                      label={t("schedule.corrections.fields.start")}
                      value={formData.start}
                      onChange={e => setFormData({ ...formData, start: e.target.value })}
                      InputLabelProps={{ shrink: true }}
                      required
                    />
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      type="time"
                      label={t("schedule.corrections.fields.end")}
                      value={formData.end}
                      onChange={e => setFormData({ ...formData, end: e.target.value })}
                      InputLabelProps={{ shrink: true }}
                      required
                    />
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      label={t("schedule.corrections.fields.subject")}
                      value={formData.subject}
                      onChange={e => setFormData({ ...formData, subject: e.target.value })}
                    />
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      label={t("schedule.corrections.fields.room")}
                      value={formData.room}
                      onChange={e => setFormData({ ...formData, room: e.target.value })}
                    />
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <TextField
                      fullWidth
                      label={t("schedule.corrections.fields.teacher")}
                      value={formData.teacher}
                      onChange={e => setFormData({ ...formData, teacher: e.target.value })}
                    />
                  </Grid>
                </>
              )}
            </Grid>
            
            <DialogActions>
              <Button onClick={handleCancelEdit}>{t("cancel")}</Button>
              <Button variant="contained" onClick={handleFormSave}>{t("save")}</Button>
            </DialogActions>
          </Stack>
        ) : (
          <Stack spacing={1} sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="h6">{t("schedule.corrections.listTitle")}</Typography>
              <Button variant="contained" startIcon={<span>+</span>} onClick={handleAddCorrection}>
                {t("schedule.corrections.addButton")}
              </Button>
            </Stack>
            
            {loading ? (
              <Typography variant="body2" color="text.secondary">{t("schedule.corrections.loading")}</Typography>
            ) : corrections.length === 0 ? (
              <Typography variant="body2" color="text.secondary">{t("schedule.corrections.empty")}</Typography>
            ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>{t("schedule.corrections.columns.id")}</TableCell>
                    <TableCell>{t("schedule.corrections.columns.date")}</TableCell>
                    <TableCell>{t("schedule.corrections.columns.type")}</TableCell>
                    <TableCell>{t("schedule.corrections.columns.details")}</TableCell>
                    <TableCell style={{ width: 120 }}>{t("schedule.corrections.columns.actions")}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {corrections.map((corr, idx) => (
                    <TableRow key={idx}>
                      <TableCell>{corr.id}</TableCell>
                      <TableCell>{corr.date}</TableCell>
                      <TableCell>
                        {corr.remove_slots ? t("schedule.corrections.types.remove") : t("schedule.corrections.types.add")}
                      </TableCell>
                      <TableCell>
                        {corr.remove_slots ? (
                          corr.remove_slots.map(rs => (
                            <span key={rs.slot_id}>
                              {t("schedule.corrections.details.removeSlot", { slotId: rs.slot_id })}
                              {rs.labs ? ` (${rs.labs.join(", ")})` : ` (${t("schedule.corrections.details.wholeSlot")})`}
                            </span>
                          ))
                        ) : (
                          corr.add_slots.map(as => (
                            <span key={as.id}>
                              {t("schedule.corrections.details.addSlot", { slotId: as.id })}
                              {as.recurrence ? ` (${t("schedule.corrections.details.recurrence", { day: as.recurrence.day, week: as.recurrence.week })})` : ` (${as.date})`}
                              {as.start && ` ${as.start}-${as.end}`}
                            </span>
                          ))
                        )}
                      </TableCell>
                      <TableCell>
                        <Button size="small" variant="outlined" onClick={() => handleEditCorrection(corr)}>
                          {t("schedule.corrections.actions.edit")}
                        </Button>
                        <Button size="small" variant="outlined" color="error" onClick={() => handleDeleteCorrection(idx)}>
                          {t("schedule.corrections.actions.delete")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Stack>
        )}
      </DialogContent>
      {editingId === null && (
        <DialogActions>
          <Button onClick={onClose}>{t("close")}</Button>
          <Button variant="contained" onClick={handleSave} disabled={saving}>
            {saving ? t("schedule.corrections.saving") : t("schedule.corrections.saveChanges")}
          </Button>
        </DialogActions>
      )}
    </Dialog>
  );
};

export default CorrectionsDialog;