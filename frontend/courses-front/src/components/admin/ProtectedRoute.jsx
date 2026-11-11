import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { adminLoginPath } from "./Queue/state";

export const ProtectedRoute = ({ children, returnHere = false }) => {
  const [isAuth, setIsAuth] = useState(null);
  const location = useLocation();

  useEffect(() => {
    fetch("/api/v1/admin/check-auth", {
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

  // Страница очереди: после входа преподаватель возвращается на неё (?next=).
  if (!isAuth && returnHere) return <Navigate to={adminLoginPath(location.pathname + location.search)} replace />;
  if (!isAuth) return <Navigate to="/admin" replace />;

  return children;
};
