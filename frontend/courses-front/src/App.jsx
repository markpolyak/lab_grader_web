import { BrowserRouter as Router, Routes, Route, useNavigate } from "react-router-dom";
import { CourseList } from "./components/course-list";
import { AdminLogin } from "./components/admin/AdminLogin";
import { ProtectedRoute } from "./components/admin/ProtectedRoute";
import { CourseListWrapper } from "./components/course-list/courseListWrapper";
import { GroupListWrapper } from "./components/group-list/groupListWrapper";
import { LabListWrapper } from "./components/lab-list/labListWrapper";
import { RegistrationFormWrapper } from "./components/registration-form/registrationFormWrapper";
import { PlagiarismMatches } from "./components/plagiarism-matches";
import { AdminLabList } from "./components/plagiarism-matches/adminLabList";

function AdminCoursesPage() {
  const navigate = useNavigate();
  return (
    <CourseList
      isAdmin={true}
      onSelectCourse={(courseId) => navigate(`/admin/courses/${courseId}/labs`)}
    />
  );
}

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/admin" element={<AdminLogin />} />
        <Route
          path="/admin/courses"
          element={
            <ProtectedRoute>
              <AdminCoursesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/courses/:courseId/labs"
          element={
            <ProtectedRoute>
              <AdminLabList />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/courses/:courseId/labs/:labId/plagiarism"
          element={
            <ProtectedRoute>
              <PlagiarismMatches />
            </ProtectedRoute>
          }
        />
        <Route path="/" element={<CourseListWrapper />} />
        <Route path="/course/:courseId" element={<GroupListWrapper />} />
        <Route
          path="/course/:courseId/group/:groupId"
          element={<LabListWrapper />}
        />
        <Route
          path="/course/:courseId/group/:groupId/lab/:labId"
          element={<RegistrationFormWrapper />}
        />
      </Routes>
    </Router>
  );
}

export default App;
