import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

// Кто вошёл в админку: данные /admin/check-auth (логин, роль, способ входа).
const AdminHeader = ({ identity }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const handleLogout = async () => {
    try {
      await fetch("/api/v1/admin/logout", { method: "POST", credentials: "include" });
    } finally {
      navigate("/admin", { replace: true });
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 16,
        left: 16,
        zIndex: 3100,
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "6px 10px",
        borderRadius: "6px",
        backgroundColor: "#f5f5f5",
        border: "1px solid #ddd",
        fontSize: "13px",
        color: "#333",
      }}
    >
      <span>
        {t("adminAuth.signedInAs", { login: identity.login })} ·{" "}
        {t(`adminAuth.roles.${identity.role}`, { defaultValue: identity.role })}
      </span>
      <button
        type="button"
        onClick={handleLogout}
        style={{
          background: "#3c3c43",
          color: "#fff",
          border: "none",
          borderRadius: "4px",
          padding: "4px 8px",
          cursor: "pointer",
          fontSize: "12px",
        }}
      >
        {t("adminAuth.logout")}
      </button>
    </div>
  );
};

export const ProtectedRoute = ({ children }) => {
  const { t } = useTranslation();
  // undefined - проверка идёт, null - не вошёл, объект - кто вошёл
  const [identity, setIdentity] = useState(undefined);

  useEffect(() => {
    fetch("/api/v1/admin/check-auth", {
      credentials: "include",
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setIdentity(data && data.authenticated ? data : null))
      .catch(() => setIdentity(null));
  }, []);

  if (identity === undefined) return <div>{t("adminAuth.checking")}</div>;

  if (!identity) return <Navigate to="/admin" replace />;

  return (
    <>
      <AdminHeader identity={identity} />
      {children}
    </>
  );
};
