import { useTranslation } from "react-i18next";
import { Badge } from "./styled";

export function RegistrationStatus({ closed, cancelled = false }) {
  const { t } = useTranslation();
  return <Badge $closed={closed || cancelled}>
    {t(cancelled ? "adminQueues.cancelled" : closed ? "adminQueues.closed" : "adminQueues.open")}
  </Badge>;
}
