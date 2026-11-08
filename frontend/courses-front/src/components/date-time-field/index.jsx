import { useState } from "react";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import "dayjs/locale/ru";
import "dayjs/locale/en-gb";
import "dayjs/locale/zh-cn";
import ru from "antd/locale/ru_RU";
import en from "antd/locale/en_GB";
import zh from "antd/locale/zh_CN";
import { Picker } from "./styled";

const locales = { ru, en, zh };
const dayLocales = { ru: "ru", en: "en-gb", zh: "zh-cn" };

export function DateTimeField({ id, name, value, onChange, disabled }) {
  const { t, i18n } = useTranslation();
  const [draft, setDraft] = useState("");
  const current = value ?? draft;
  const language = i18n.resolvedLanguage?.split("-")[0] || "ru";

  function change(date) {
    const next = date ? date.format("YYYY-MM-DDTHH:mm") : "";
    setDraft(next);
    onChange?.(next);
  }

  return <>
    <Picker id={id} value={current ? dayjs(current).locale(dayLocales[language] || "ru") : null}
      onChange={change} disabled={disabled} locale={(locales[language] || ru).DatePicker}
      format="DD.MM.YYYY HH:mm" placeholder={t("adminQueues.dateTimePlaceholder")}
      showTime={{ format: "HH:mm", use12Hours: false }} needConfirm={false}
      popupStyle={{ zIndex: 3600 }} />
    <input type="hidden" name={name} value={current} />
  </>;
}
