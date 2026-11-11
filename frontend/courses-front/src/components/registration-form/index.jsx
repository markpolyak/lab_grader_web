import { useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchCourseDetails, registerAndCheck, gradeLab } from "../../api";
import { CardTitle, colors } from "../../../theme";
import { ListContainer } from "../group-list/styled";
import { Input, InputContainer, RegistrationButton } from "./styled";
import Snackbar from "@mui/material/Snackbar";
import Alert from "@mui/material/Alert";
import { ButtonBack } from "../course-list/styled";
import { Breadcrumb } from "../breadcrumb";
import { QueueLink } from "../queue/styled";
import { courseHasQueue, queuePagePath, shouldOfferQueue } from "../queue/state";

export const RegistrationForm = ({ courseId, groupId, labId, onBack }) => {
  const [formState, setFormState] = useState({
    surname: "",
    name: "",
    patronymic: "",
    github: "",
  });

  const [checkResult, setCheckResult] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [offerQueue, setOfferQueue] = useState(false);
  const { t } = useTranslation();

  // Автопредложение встать в очередь после успешной проверки. Записывается
  // студент всё равно сам, после входа через GitHub, по обычным правилам.
  const offerQueueIfEnabled = (gradeResponse) => {
    if (!shouldOfferQueue(gradeResponse)) return;
    fetchCourseDetails(courseId)
      .then((details) => {
        setOfferQueue(courseHasQueue(details));
      })
      .catch(() => setOfferQueue(false));
  };

  const handleChange = (e) => {
    setFormState({ ...formState, [e.target.name]: e.target.value });
  };

const handleSubmit = async () => {
  setIsLoading(true);
  setCheckResult(null);
  setOfferQueue(false);

  try {
    const registerResponse = await registerAndCheck(courseId, groupId, formState);

    if (registerResponse.status === "conflict") {
      setCheckResult({
        type: "error",
        message: registerResponse.message,
      });
      setIsLoading(false);
      return;
    }

    if (
      registerResponse.status === "registered" ||
      registerResponse.status === "already_registered"
    ) {
      // Registration successful, now grade the lab
      const gradeResponse = await gradeLab(courseId, groupId, labId, formState.github);
      offerQueueIfEnabled(gradeResponse);

      if (gradeResponse.status === "updated") {
        setCheckResult({
          type: "success",
          message: gradeResponse.message,
          result: gradeResponse.result,
          passed: gradeResponse.passed,
          checks: gradeResponse.checks,
          score: gradeResponse.score,
        });
      } else if (gradeResponse.status === "rejected") {
        setCheckResult({
          type: "warning",
          message: gradeResponse.message,
          currentGrade: gradeResponse.current_grade,
          passed: gradeResponse.passed,
          checks: gradeResponse.checks,
          score: gradeResponse.score,
        });
      } else if (gradeResponse.status === "pending") {
        setCheckResult({
          type: "info",
          message: gradeResponse.message,
        });
      } else {
        setCheckResult({
          type: "info",
          message: gradeResponse.message || "Проверка завершена",
        });
      }

      setIsLoading(false);
      return;
    }

    setCheckResult({
      type: "warning",
      message: "Неизвестный ответ от сервера",
    });
    setIsLoading(false);
  } catch (error) {
    console.error("Ошибка:", error);
    setCheckResult({
      type: "error",
      message: error.message || "Произошла ошибка, попробуйте снова.",
    });
    setIsLoading(false);
  }
};

  return (
    <ListContainer $fitPhone={offerQueue}>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
      <Breadcrumb courseId={courseId} groupId={groupId} labId={labId} />
      <CardTitle>Запуск проверки</CardTitle>
      <InputContainer>
        <Input
          type="text"
          name="surname"
          placeholder="Фамилия"
          value={formState.surname}
          onChange={handleChange}
        />
        <Input
          type="text"
          name="name"
          placeholder="Имя"
          value={formState.name}
          onChange={handleChange}
        />
        <Input
          type="text"
          name="patronymic"
          placeholder="Отчество"
          value={formState.patronymic}
          onChange={handleChange}
        />
        <Input
          type="text"
          name="github"
          placeholder="GitHub-аккаунт (имя пользователя)"
          value={formState.github}
          onChange={handleChange}
        />
      </InputContainer>
      <RegistrationButton onClick={handleSubmit} disabled={isLoading}>
        {isLoading ? "Проверка..." : "Зарегистрироваться и запустить проверку"}
      </RegistrationButton>

      {checkResult && (
        <div style={{
          marginTop: "20px",
          padding: "16px",
          borderRadius: "8px",
          backgroundColor:
            checkResult.type === "success" ? "#e8f5e9" :
            checkResult.type === "error" ? "#ffebee" :
            checkResult.type === "warning" ? "#fff3e0" : "#e3f2fd",
          border: `1px solid ${
            checkResult.type === "success" ? colors.save :
            checkResult.type === "error" ? colors.error :
            checkResult.type === "warning" ? colors.edit : colors.cancel
          }`,
        }}>
          <div style={{
            fontSize: "16px",
            fontWeight: "500",
            marginBottom: "12px",
            color: colors.textPrimary,
            whiteSpace: "pre-line",
          }}>
            {checkResult.message}
          </div>

          {checkResult.passed && (
            <div style={{
              fontSize: "14px",
              marginBottom: "8px",
              color: colors.textSecondary,
            }}>
              {checkResult.passed}
            </div>
          )}

          {checkResult.result && (
            <div style={{
              fontSize: "14px",
              marginBottom: "8px",
              color: colors.textSecondary,
            }}>
              Результат: <strong>{checkResult.result}</strong>
            </div>
          )}

          {checkResult.score && (
            <div style={{
              fontSize: "14px",
              marginBottom: "8px",
              color: colors.textSecondary,
            }}>
              Баллы: <strong>{checkResult.score}</strong>
            </div>
          )}

          {checkResult.currentGrade && (
            <div style={{
              fontSize: "14px",
              marginBottom: "8px",
              color: colors.textSecondary,
            }}>
              Текущая оценка: <strong>{checkResult.currentGrade}</strong>
            </div>
          )}

          {offerQueue && (
            <div style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: "8px",
              margin: "12px 0",
              fontSize: "14px",
              color: colors.textSecondary,
            }}>
              <span>{t("queue.offer.text")}</span>
              {/* В ссылке short-name: интерфейс проверки адресует работу им,
                  страница очереди принимает и short-name, и ключ конфига. */}
              <QueueLink to={queuePagePath(courseId, labId)} style={{ alignSelf: "flex-start" }}>
                {t("queue.offer.button")}
              </QueueLink>
            </div>
          )}

          {checkResult.checks && checkResult.checks.length > 0 && (
            <div style={{ marginTop: "12px" }}>
              <div style={{
                fontSize: "14px",
                fontWeight: "500",
                marginBottom: "8px",
                color: colors.textPrimary,
              }}>
                Детали проверок:
              </div>
              {checkResult.checks.map((check, index) => (
                <div key={index} style={{
                  fontSize: "13px",
                  marginBottom: "4px",
                  color: colors.textSecondary,
                  fontFamily: "monospace",
                }}>
                  {check}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </ListContainer>
  );
};
