import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, StyledInput, Text, TextError } from "./styled";
import { CourseCardContainer } from "../course-list/styled";

export const AdminLogin = () => {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();

    try {
        const response = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ login, password }),
    });

        if (response.ok) {
        navigate("/admin/courses");
        } else {
            setError("Incorrect login or password");
        }
  } catch (err) {
        setError("Ошибка при подключении к серверу");
    }
};



  return (
    <CourseCardContainer
      style={{
        padding: "16px",
      }}
    >
      <Text style={{ textAlign: "center" }}>Login to admin panel</Text>
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", width: "300px" }}
      >
        <StyledInput
          type="text"
          placeholder="Login"
          value={login}
          onChange={(e) => setLogin(e.target.value)}
          status={error ? "error" : ""}
        />
        <StyledInput
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          status={error ? "error" : ""}
        />
        {error && <TextError>{error}</TextError>}
        <Button type="submit" style={{ marginTop: error ? "0" : "16px" }}>
          Login
        </Button>
      </form>
    </CourseCardContainer>
  );
};
