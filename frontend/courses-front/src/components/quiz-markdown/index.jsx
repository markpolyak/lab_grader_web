import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

// Текст вопроса квиза: markdown с кодом и формулами ($…$, $$…$$).
// Сырой HTML не рендерится (skipHtml) - файл квиза пишет преподаватель, но
// показывается он на телефонах студентов (docs/QUIZ_PLAN.md §3.1, §10).
export const QuizMarkdown = ({ children, inline = false }) => (
  <ReactMarkdown
    remarkPlugins={[remarkMath]}
    rehypePlugins={[rehypeKatex]}
    skipHtml
    components={inline ? { p: ({ children: c }) => <span>{c}</span> } : undefined}
  >
    {children || ""}
  </ReactMarkdown>
);
