import { useState, useEffect } from "react";
import { fetchCourseDetails } from "../../api";
import { BreadcrumbContainer, BreadcrumbItem, BreadcrumbSeparator } from "./styled";

export const Breadcrumb = ({ courseId, groupId, labId }) => {
  const [courseName, setCourseName] = useState("");

  useEffect(() => {
    if (courseId) {
      fetchCourseDetails(courseId)
        .then((data) => {
          setCourseName(data.name || courseId);
        })
        .catch(() => {
          setCourseName(courseId);
        });
    }
  }, [courseId]);

  if (!courseId) return null;

  return (
    <BreadcrumbContainer>
      <BreadcrumbItem $active={!groupId && !labId}>
        {courseName || courseId}
      </BreadcrumbItem>

      {groupId && (
        <>
          <BreadcrumbSeparator>›</BreadcrumbSeparator>
          <BreadcrumbItem $active={!labId}>
            Группа {groupId}
          </BreadcrumbItem>
        </>
      )}

      {labId && (
        <>
          <BreadcrumbSeparator>›</BreadcrumbSeparator>
          <BreadcrumbItem $active={true}>
            {labId}
          </BreadcrumbItem>
        </>
      )}
    </BreadcrumbContainer>
  );
};
