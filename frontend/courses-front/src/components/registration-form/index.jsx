import { q, useQueueLanguage, QueueLanguage } from "../DefenseQueue/locale";
import { useEffect, useState } from "react";
import { registerAndCheck, gradeLab, fetchCourseDetails } from "../../api";
import { CardTitle, colors, MainContainer } from "../../../theme";
import { Input, InputContainer, RegistrationButton } from "./styled";
import { ButtonBack } from "../course-list/styled";
import { Breadcrumb } from "../breadcrumb";
import { queueAppearance } from "../DefenseQueue/appearance";
import { StudentQueue } from "../DefenseQueue/StudentQueue";
import { cleanIdentity, identityKey } from "../DefenseQueue/state";
export const RegistrationForm = ({
  courseId,
  groupId,
  labId,
  onBack
}) => {
  useQueueLanguage();
  const [formState, setFormState] = useState(() => {
    try {
      return cleanIdentity(JSON.parse(sessionStorage.getItem(identityKey(courseId, groupId)) || "{}"));
    } catch {
      return cleanIdentity();
    }
  });
  const [queueEnabled, setQueueEnabled] = useState(false);
  const [registrationDisabled, setRegistrationDisabled] = useState(false);
  const [tab, setTab] = useState("registration");
  useEffect(() => {
    let active = true;
    fetchCourseDetails(courseId).then(value => {
      if (active) {
        setQueueEnabled(Boolean(value.queue_enabled));
        setRegistrationDisabled(Boolean(value.queue_registration_disabled));
        if (value.queue_registration_disabled) setTab("queue");
      }
    }).catch(() => {});
    return () => {
      active = false;
    };
  }, [courseId]);
  useEffect(() => {
    try {
      sessionStorage.setItem(identityKey(courseId, groupId), JSON.stringify(formState));
    } catch {/* Форма работает и без хранилища браузера. */}
  }, [courseId, groupId, formState]);
  const [checkResult, setCheckResult] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const handleChange = e => {
    setFormState({
      ...formState,
      [e.target.name]: e.target.value
    });
  };
  const handleSubmit = async () => {
    setIsLoading(true);
    if (queueEnabled) setTab("queue");
    setCheckResult(null);
    try {
      const registerResponse = await registerAndCheck(courseId, groupId, formState);
      if (registerResponse.status === "conflict") {
        setCheckResult({
          type: "error",
          message: registerResponse.message
        });
        setIsLoading(false);
        return;
      }
      if (registerResponse.status === "registered" || registerResponse.status === "already_registered") {
        // Registration successful, now grade the lab
        const gradeResponse = await gradeLab(courseId, groupId, labId, formState.github);
        if (gradeResponse.status === "updated") {
          setCheckResult({
            type: "success",
            message: gradeResponse.message,
            result: gradeResponse.result,
            passed: gradeResponse.passed,
            checks: gradeResponse.checks,
            score: gradeResponse.score
          });
        } else if (gradeResponse.status === "rejected") {
          setCheckResult({
            type: "warning",
            message: gradeResponse.message,
            currentGrade: gradeResponse.current_grade,
            passed: gradeResponse.passed,
            checks: gradeResponse.checks,
            score: gradeResponse.score
          });
        } else if (gradeResponse.status === "pending") {
          setCheckResult({
            type: "info",
            message: gradeResponse.message
          });
        } else {
          setCheckResult({
            type: "info",
            message: gradeResponse.message || q("Проверка завершена")
          });
        }
        setIsLoading(false);
        return;
      }
      setCheckResult({
        type: "warning",
        message: q("Неизвестный ответ от сервера")
      });
      setIsLoading(false);
    } catch (error) {
      console.error(q("Ошибка:"), error);
      setCheckResult({
        type: "error",
        message: error.message || q("Произошла ошибка, попробуйте снова.")
      });
      setIsLoading(false);
    }
  };
  return <MainContainer className="queue-surface queue-registration" style={{
    ...queueAppearance,
    width: "min(100%, 720px)",
    boxSizing: "border-box"
  }}>
      <ButtonBack onClick={onBack}>{q("← Назад")}</ButtonBack>
      <Breadcrumb courseId={courseId} groupId={groupId} labId={labId} />
      <QueueLanguage />
      <CardTitle className="queue-registration-title">{q("Проверка и защита лабораторной")}</CardTitle>
      {queueEnabled && <div className="queue-tabs" role="tablist" aria-label={q("Разделы лабораторной")}>
        <button role="tab" disabled={registrationDisabled} aria-selected={tab === "registration"} onClick={() => setTab("registration")}>{q("Регистрация и проверка")}</button>
        <button role="tab" aria-selected={tab === "queue"} onClick={() => setTab("queue")}>{q("Список на занятие")}</button>
      </div>}
      {registrationDisabled && <p role="status">{q("Локальная проверка: таблица доступна только для чтения. Укажите свои ФИО и GitHub, затем выберите посещение в списке ниже.")}</p>}
      <InputContainer className="queue-identity-grid">
        <label>{q("Фамилия")}<Input type="text" name="surname" aria-label={q("Фамилия")} disabled={isLoading} placeholder={q("Фамилия")} value={formState.surname} onChange={handleChange} /></label>
        <label>{q("Имя")}<Input type="text" name="name" aria-label={q("Имя")} disabled={isLoading} placeholder={q("Имя")} value={formState.name} onChange={handleChange} /></label>
        <label>{q("Отчество")}<Input type="text" name="patronymic" aria-label={q("Отчество")} disabled={isLoading} placeholder={q("Отчество")} value={formState.patronymic} onChange={handleChange} /></label>
        <label>{q("GitHub-аккаунт (имя пользователя)")}<Input type="text" name="github" aria-label="GitHub" disabled={isLoading} placeholder={q("GitHub-аккаунт (имя пользователя)")} value={formState.github} onChange={handleChange} /></label>
      </InputContainer>
      {tab === "registration" && !registrationDisabled && <RegistrationButton onClick={handleSubmit} disabled={isLoading}>
        {isLoading ? q("Проверка...") : q("Зарегистрироваться и запустить проверку")}
      </RegistrationButton>}
      {queueEnabled && tab === "queue" && <StudentQueue courseId={courseId} groupId={groupId} labId={labId} identity={formState} checking={isLoading} refreshToken={checkResult} />}

      {checkResult && <div style={{
      marginTop: "20px",
      padding: "16px",
      borderRadius: "8px",
      backgroundColor: checkResult.type === "success" ? "#e8f5e9" : checkResult.type === "error" ? "#ffebee" : checkResult.type === "warning" ? "#fff3e0" : "#e3f2fd",
      border: `1px solid ${checkResult.type === "success" ? colors.save : checkResult.type === "error" ? colors.error : checkResult.type === "warning" ? colors.edit : colors.cancel}`
    }}>
          <div style={{
        fontSize: "16px",
        fontWeight: "500",
        marginBottom: "12px",
        color: colors.textPrimary,
        whiteSpace: "pre-line"
      }}>
            {checkResult.message}
          </div>

          {checkResult.passed && <div style={{
        fontSize: "14px",
        marginBottom: "8px",
        color: colors.textSecondary
      }}>
              {checkResult.passed}
            </div>}

          {checkResult.result && <div style={{
        fontSize: "14px",
        marginBottom: "8px",
        color: colors.textSecondary
      }}>{q("Результат:")}<strong>{checkResult.result}</strong>
            </div>}

          {checkResult.score && <div style={{
        fontSize: "14px",
        marginBottom: "8px",
        color: colors.textSecondary
      }}>{q("Баллы:")}<strong>{checkResult.score}</strong>
            </div>}

          {checkResult.currentGrade && <div style={{
        fontSize: "14px",
        marginBottom: "8px",
        color: colors.textSecondary
      }}>{q("Текущая оценка:")}<strong>{checkResult.currentGrade}</strong>
            </div>}

          {checkResult.checks && checkResult.checks.length > 0 && <div style={{
        marginTop: "12px"
      }}>
              <div style={{
          fontSize: "14px",
          fontWeight: "500",
          marginBottom: "8px",
          color: colors.textPrimary
        }}>{q("Детали проверок:")}</div>
              {checkResult.checks.map((check, index) => <div key={index} style={{
          fontSize: "13px",
          marginBottom: "4px",
          color: colors.textSecondary,
          fontFamily: "monospace"
        }}>
                  {check}
                </div>)}
            </div>}
        </div>}
    </MainContainer>;
};
