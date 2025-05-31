import { useState, useEffect, useRef } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { Select, MenuItem } from "@mui/material";
import { useTranslation } from "react-i18next";
import {
  Button,
  ButtonGroup,
  CourseCardContainer,
  CourseTitle,
  Description,
  Details,
  DetailsContainer,
  HeaderCard,
  ImageBlock,
  InfoTitle,
  MainContainer,
  SelectButton,
  SemesterTitle,
  Title,
  FixedAdminButton,
} from "./styled";
import { fetchCourseDetails, fetchCourses } from "../../api";
import { colors } from "../../../theme";
import {
  Snackbar,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from "@mui/material";

export const CourseList = ({ onSelectCourse, isAdmin = false }) => {
  const { t, i18n } = useTranslation();

  const [courses, setCourses] = useState([]);
  const [expandedCourse, setExpandedCourse] = useState(null);
  const [editingCourseId, setEditingCourseId] = useState(null);
  const [editContent, setEditContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "info",
  });
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);
  const [selectedCourseId, setSelectedCourseId] = useState(null);

  const fileInputRef = useRef();

  const showSnackbar = (message, severity = "info") => {
    setSnackbar({ open: true, message, severity });
  };

  const handleCloseSnackbar = () => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  };

  useEffect(() => {
    fetchCourses()
      .then(setCourses)
      .catch(() => showSnackbar(t("errorLoadingCourses"), "error"));
  }, [t]);

  const handleDeleteConfirmation = (courseId) => {
    setSelectedCourseId(courseId);
    setOpenConfirmDialog(true);
  };

  const handleConfirmDelete = async () => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/courses/${selectedCourseId}`, {
        method: "DELETE",
      });

      if (response.ok) {
        showSnackbar(t("courseDeleted"), "success");
        fetchCourses().then(setCourses);
      } else {
        const data = await response.json();
        showSnackbar(data.detail || t("errorDeletingCourse"), "error");
      }
    } catch (error) {
      console.error(error);
      showSnackbar(t("errorDeletingCourse"), "error");
    } finally {
      setOpenConfirmDialog(false);
    }
  };

  const handleExpand = async (courseId) => {
    if (expandedCourse === courseId) {
      setExpandedCourse(null);
      return;
    }

    try {
      const details = await fetchCourseDetails(courseId);
      setCourses((prev) =>
        prev.map((course) =>
          course.id === courseId ? { ...course, details } : course
        )
      );
      setExpandedCourse(courseId);
    } catch (error) {
      console.error(error);
      showSnackbar(t("errorLoadingCourseDetails"), "error");
    }
  };

  const handleCancelDelete = () => {
    setOpenConfirmDialog(false);
  };

  const handleEdit = async (courseId) => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/courses/${courseId}/edit`);
      if (!response.ok) {
        throw new Error(t("errorLoadingCourseForEdit"));
      }
      const data = await response.json();
      setEditingCourseId(courseId);
      setEditContent(data.content);
      setOriginalContent(data.content);
    } catch (error) {
      console.error(error);
      showSnackbar(error.message || t("errorLoadingCourseForEdit"), "error");
    }
  };

  const handleSave = async () => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/courses/${editingCourseId}/edit`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editContent }),
      });

      if (response.ok) {
        showSnackbar(t("changesSaved"), "success");
        setEditingCourseId(null);
        setIsFullscreen(false);
        fetchCourses().then(setCourses);
      } else {
        const data = await response.json();
        showSnackbar(data.detail || t("errorSavingCourse"), "error");
      }
    } catch (error) {
      console.error(error);
      showSnackbar(t("errorSavingCourse"), "error");
    }
  };

  const handleCancel = () => {
    setEditContent(originalContent);
    setEditingCourseId(null);
    setIsFullscreen(false);
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch("http://127.0.0.1:8000/courses/upload", {
        method: "POST",
        body: formData,
      });

      if (response.ok) {
        showSnackbar(t("courseUploaded"), "success");
        fetchCourses().then(setCourses);
      } else {
        const data = await response.json();
        showSnackbar(data.detail || t("errorUploadingCourse"), "error");
      }
    } catch (error) {
      console.error(error);
      showSnackbar(t("errorUploadingFile"), "error");
    }
  };


  const languages = [
    { code: "ru", label: "Русский" },
    { code: "en", label: "English" },
    { code: "zh", label: "中文" },
  ];

  return (
    <MainContainer style={{ position: "relative" }}>
      {/* Выпадающий список выбора языка */}
      <Select
        value={i18n.language}
        onChange={(e) => i18n.changeLanguage(e.target.value)}
        style={{
          position: "fixed",
          top: 16,
          right: 16,
          zIndex: 3000,
          minWidth: 120,
          backgroundColor: "#555",
          color: "#fff",
        }}
        variant="outlined"
        size="small"
        sx={{
          ".MuiOutlinedInput-notchedOutline": { borderColor: "#555" },
          "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
            borderColor: "#888",
          },
          ".MuiSvgIcon-root": { color: "#fff" },
        }}
      >
        {languages.map(({ code, label }) => (
          <MenuItem key={code} value={code}>
            {label}
          </MenuItem>
        ))}
      </Select>

      {isAdmin && (
        <>
          <FixedAdminButton onClick={handleUploadClick}>
            {t("loadCourse")}
          </FixedAdminButton>
          <input
            type="file"
            accept=".yaml,.yml"
            ref={fileInputRef}
            style={{ display: "none" }}
            onChange={handleFileChange}
          />
        </>
      )}

      {courses.map((course) => (
        <CourseCardContainer key={course.id}>
          <HeaderCard>
            <CourseTitle>{course.name}</CourseTitle>
            <SemesterTitle>
              {t("semester")}: {course.semester}
            </SemesterTitle>
          </HeaderCard>
          <ImageBlock $logo={course.logo} />

          {expandedCourse === course.id && course.details && (
            <DetailsContainer>
              <InfoTitle>{t("information")}</InfoTitle>

              <Details>
                <Title>{t("email")}:</Title>
                <Description>{course.details.email}</Description>
              </Details>

              <Details>
                <Title>{t("githubOrganization")}:</Title>
                <Description>
                  {course.details["github-organization"] ? (
                    <a
                      href={`https://github.com/${course.details["github-organization"]}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#1976d2", textDecoration: "underline" }}
                    >
                      {course.details["github-organization"]}
                    </a>
                  ) : (
                    "—"
                  )}
                </Description>
              </Details>

              <Details>
                <Title>{t("googleSpreadsheet")}:</Title>
                <Description>
                  {course.details["google-spreadsheet"] ? (
                    <a
                      href={`https://docs.google.com/spreadsheets/d/${course.details["google-spreadsheet"]}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#1976d2", textDecoration: "underline" }}
                    >
                      {course.details["google-spreadsheet"]}
                    </a>
                  ) : (
                    "—"
                  )}
                </Description>
              </Details>
            </DetailsContainer>
          )}

          {editingCourseId === course.id ? (
            <div style={{ marginTop: "10px", position: "relative" }}>
              <CodeMirror
                value={editContent}
                onChange={setEditContent}
                extensions={[yaml()]}
                height={isFullscreen ? "80vh" : "300px"}
                style={{
                  width: isFullscreen ? "95vw" : "100%",
                  position: isFullscreen ? "fixed" : "static",
                  top: isFullscreen ? "50%" : "auto",
                  left: isFullscreen ? "50%" : "auto",
                  transform: isFullscreen ? "translate(-50%, -50%)" : "none",
                  zIndex: isFullscreen ? 2000 : "auto",
                  backgroundColor: "#fff",
                  borderRadius: "8px",
                  border: "1px solid #ddd",
                  fontSize: "14px",
                }}
              />

              <ButtonGroup
                style={{
                  justifyContent: "center",
                  gap: "12px",
                  marginTop: "8px",
                  flexWrap: "wrap",
                  position: isFullscreen ? "fixed" : "static",
                  bottom: isFullscreen ? "20px" : "auto",
                  left: isFullscreen ? "50%" : "auto",
                  transform: isFullscreen ? "translateX(-50%)" : "none",
                  width: isFullscreen ? "95vw" : "auto",
                  zIndex: 2100,
                }}
              >
                <Button
                  onClick={handleSave}
                  style={{ backgroundColor: colors.save, minWidth: "120px" }}
                >
                  {t("save")}
                </Button>

                <Button
                  onClick={() => setIsFullscreen((prev) => !prev)}
                  style={{
                    backgroundColor: "#777",
                    color: "#fff",
                    minWidth: "140px",
                  }}
                >
                  {isFullscreen ? t("collapse") : t("fullscreen")}
                </Button>

                <Button
                  onClick={handleCancel}
                  style={{
                    backgroundColor: colors.error,
                    minWidth: "120px",
                    color: "#fff",
                  }}
                >
                  {t("cancel")}
                </Button>
              </ButtonGroup>
            </div>
          ) : (
            <ButtonGroup
              style={{ justifyContent: "center", gap: "12px", marginTop: "10px" }}
            >
              <SelectButton
                onClick={() => onSelectCourse(course.id)}
                style={{ backgroundColor: colors.selected }}
              >
                {t("select")}
              </SelectButton>

              <Button onClick={() => handleExpand(course.id)}>
                {expandedCourse === course.id ? t("collapse") : t("expand")}
              </Button>

              {isAdmin && (
                <>
                  <Button
                    onClick={() => handleEdit(course.id)}
                    style={{ backgroundColor: colors.edit, color: "#fff" }}
                  >
                    {t("edit")}
                  </Button>

                  <Button
                    onClick={() => handleDeleteConfirmation(course.id)}
                    style={{ backgroundColor: colors.error, color: "#fff" }}
                  >
                    {t("delete")}
                  </Button>
                </>
              )}
            </ButtonGroup>
          )}
        </CourseCardContainer>
      ))}

      {/* Диалог подтверждения удаления */}
      <Dialog open={openConfirmDialog} onClose={handleCancelDelete}>
        <DialogTitle>{t("confirmDeleteTitle")}</DialogTitle>
        <DialogContent>{t("confirmDeleteMessage")}</DialogContent>
        <DialogActions>
          <Button onClick={handleCancelDelete}>{t("cancel")}</Button>
          <Button
            onClick={handleConfirmDelete}
            style={{ backgroundColor: colors.error, color: "#fff" }}
          >
            {t("delete")}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Снэкбар */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </MainContainer>
  );
};
