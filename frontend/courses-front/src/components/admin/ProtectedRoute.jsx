import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

export const ProtectedRoute = ({ children }) => {
  const [isAuth, setIsAuth] = useState(null);

  useEffect(() => {
    fetch("/api/admin/check-auth", {
      credentials: "include",
    })
      .then((res) => {
        if (res.ok) {
          setIsAuth(true);
        } else {
          setIsAuth(false);
        }
      })
      .catch(() => setIsAuth(false));
  }, []);

  if (isAuth === null) return <div>Проверка доступа...</div>;

  if (!isAuth) return <Navigate to="/admin" replace />;

  return children;
};
