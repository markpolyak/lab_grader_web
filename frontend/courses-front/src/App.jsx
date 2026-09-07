import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { CourseList } from "./components/course-list";
import { AdminLogin } from "./components/admin/AdminLogin";
import { ProtectedRoute } from "./components/admin/ProtectedRoute";
import { CourseListWrapper } from "./components/course-list/courseListWrapper";
import { GroupListWrapper } from "./components/group-list/groupListWrapper";
import { LabListWrapper } from "./components/lab-list/labListWrapper";
import { RegistrationFormWrapper } from "./components/registration-form/registrationFormWrapper";
import { JoinLab } from "./components/JoinLab";
import { LabListWrapper as AdminLabListWrapper } from "./components/admin/LabList/labListWrapper";

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/admin" element={<AdminLogin />} />
        <Route
          path="/admin/courses"
          element={
            <ProtectedRoute>
              <CourseList onSelectCourse={() => {}} isAdmin={true} />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/courses/:courseId/labs"
          element={
            <ProtectedRoute>
              <AdminLabListWrapper />
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
        <Route path="/join/error" element={<JoinLab />} />
        <Route path="/join/:courseId/:labId" element={<JoinLab />} />
      </Routes>
    </Router>
  );
}

export default App;
