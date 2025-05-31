import { useState } from "react";
import { registerAndCheck, gradeLab } from "../../api";
import { CardTitle, colors, MainContainer } from "../../../theme";
import { Input, InputContainer, RegistrationButton } from "./styled";
import Snackbar from "@mui/material/Snackbar";
import Alert from "@mui/material/Alert";
import { ButtonBack } from "../course-list/styled";

export const RegistrationForm = ({ courseId, groupId, labId, onBack }) => {
  const [formState, setFormState] = useState({
    surname: "",
    name: "",
    patronymic: "",
    github: "",
  });

  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "success",
  });

  const handleChange = (e) => {
    setFormState({ ...formState, [e.target.name]: e.target.value });
  };

const handleSubmit = async () => {
  try {
    const registerResponse = await registerAndCheck(courseId, groupId, formState);

    if (registerResponse.status === "conflict") {
      showSnackbar(registerResponse.message, "error");
      return;
    }

    if (
      registerResponse.status === "registered" ||
      registerResponse.status === "already_registered"
    ) {
      showSnackbar(registerResponse.message, "success");

      const gradeResponse = await gradeLab(courseId, groupId, labId, formState.github);

      if (gradeResponse.status === "updated") {
        showSnackbar(gradeResponse.message, "success");
      } else {
        showSnackbar(gradeResponse.message || "Проверка завершена", "info");
      }

      return;
    }

    showSnackbar("Неизвестный ответ от сервера", "warning");
  } catch (error) {
    console.error("Ошибка:", error);
    showSnackbar("Произошла ошибка, попробуйте снова.", "error");
  }
};


  const showSnackbar = (message, severity) => {
    setSnackbar({
      open: true,
      message,
      severity,
    });
  };

  const handleCloseSnackbar = (_, reason) => {
    if (reason === "clickaway") return;
    setSnackbar((prev) => ({ ...prev, open: false }));
  };

  console.log("courseId :>> ", typeof courseId);
  console.log("groupId :>> ", typeof groupId);
  console.log("onBack :>> ", typeof onBack);

  return (
    <MainContainer>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
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
          placeholder="GitHub никнейм"
          value={formState.github}
          onChange={handleChange}
        />
      </InputContainer>
      <RegistrationButton onClick={handleSubmit}>
        Зарегистрироваться и запустить проверку
      </RegistrationButton>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: "top", horizontal: "center" }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{
            bgcolor:
              snackbar.severity === "success"
                ? colors.save
                : snackbar.severity === "error"
                ? colors.error
                : colors.cancel,
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </MainContainer>
  );
};
