import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ActionButton,
  FieldHint,
  InlineError,
  Label,
  SectionTitle,
  TeamForm,
  TextInput,
} from "./styled";
import {
  DESCRIPTION_MAX_LENGTH,
  ERROR_TRANSLATION_KEYS,
  TITLE_MAX_LENGTH,
  validateTeamForm,
} from "./state";


export function CreateTeamForm({ canCreate, countMax, isBusy, onCreate }) {
  const { t } = useTranslation();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [localError, setLocalError] = useState(null);

  if (!canCreate) {
    return (
      <FieldHint>
        {countMax
          ? t("join.team.creationClosedLimit", { count: countMax })
          : t("join.team.creationClosed")}
      </FieldHint>
    );
  }

  const submit = (event) => {
    event.preventDefault();
    // Клиентская проверка только избавляет от лишнего запроса: решение всё
    // равно принимает backend (grading/teams.py), и его код ошибки победит.
    const code = validateTeamForm(title, description);
    setLocalError(code);
    if (code) return;
    onCreate({ title, description });
  };

  return (
    <TeamForm onSubmit={submit}>
      <SectionTitle>{t("join.team.createTitle")}</SectionTitle>

      <div>
        <Label as="label" htmlFor="join-team-title">
          {t("join.team.nameLabel")}
        </Label>
        <TextInput
          id="join-team-title"
          value={title}
          maxLength={TITLE_MAX_LENGTH}
          onChange={(event) => setTitle(event.target.value)}
          placeholder={t("join.team.namePlaceholder")}
        />
      </div>

      <div>
        <Label as="label" htmlFor="join-team-description">
          {t("join.team.descriptionLabel")}
        </Label>
        <TextInput
          id="join-team-description"
          value={description}
          maxLength={DESCRIPTION_MAX_LENGTH}
          onChange={(event) => setDescription(event.target.value)}
          placeholder={t("join.team.descriptionPlaceholder")}
        />
      </div>

      {localError && (
        <InlineError role="alert">
          {t(ERROR_TRANSLATION_KEYS[localError] || "join.errors.unknown")}
        </InlineError>
      )}

      <ActionButton type="submit" disabled={isBusy}>
        {isBusy ? t("join.team.creating") : t("join.team.create")}
      </ActionButton>
    </TeamForm>
  );
}
