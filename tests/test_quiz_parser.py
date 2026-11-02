"""Quiz file parsing and parameters (docs/QUIZ_PLAN.md §3, §14)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.quiz import (
    QuizFormatError,
    QuizSettings,
    build_quiz_file,
    parse_question_bank,
    parse_quiz,
    quiz_id_from_filename,
    settings_from,
    split_front_matter,
)

BANK = """# Квиз по лекции 5
## Курс «Операционные системы»

**Время:** 5 минут

| Блок | Вопросов |
|---|---|
| 1 | 2 |

---

## Блок 1 — Процессы

### Вопрос 1.1 — Тип 1 (одиночный выбор), 25%
*Состояния процесса* ⚠️ вопрос с подвохом

**Вариант А:** Какой системный вызов создаёт новый процесс в Linux?
- `fork()` ✓
- `exec()`
- `wait()`
- `kill()`

**Вариант Б:**
Чему равна сложность поиска в сбалансированном дереве из $n$ элементов?

$$T(n) = O(\\log n)$$

```c
# define N 10
- это не вариант ответа
**Вариант В:** и это не метка
```
- $O(1)$
- $O(\\log n)$ ✓
- $O(n)$
- $O(n \\log n)$

## Блок 2 — Память

### Вопрос 2.1 — Тип 1, 75%
Что хранит TLB?
- кеш страниц
- трансляции адресов ✓
- дескрипторы файлов
- стек ядра
"""


def bank_question(body: str, heading: str = "### Вопрос 1.1, 10%") -> str:
    return f"{heading}\n{body}"


class TestParseBank:
    def test_title_and_questions(self):
        title, questions = parse_question_bank(BANK)
        assert title == "Квиз по лекции 5"
        assert [q.label for q in questions] == ["Вопрос 1.1", "Вопрос 2.1"]
        assert [q.weight for q in questions] == [25.0, 75.0]

    def test_text_on_label_line(self):
        _, questions = parse_question_bank(BANK)
        variant = questions[0].variants[0]
        assert variant.label == "Вариант А"
        assert variant.text == "Какой системный вызов создаёт новый процесс в Linux?"
        assert variant.options == ("`fork()`", "`exec()`", "`wait()`", "`kill()`")
        assert variant.correct == 0

    def test_text_on_following_lines_with_formula_and_code(self):
        _, questions = parse_question_bank(BANK)
        variant = questions[0].variants[1]
        assert variant.label == "Вариант Б"
        assert variant.text.startswith("Чему равна сложность поиска")
        assert "$$T(n) = O(\\log n)$$" in variant.text
        assert "# define N 10" in variant.text
        assert "- это не вариант ответа" in variant.text
        assert "**Вариант В:** и это не метка" in variant.text
        assert variant.options[1] == "$O(\\log n)$"
        assert variant.correct == 1
        assert len(questions[0].variants) == 2

    def test_italic_subtitle_is_not_question_text(self):
        _, questions = parse_question_bank(BANK)
        assert questions[0].subtitle == "*Состояния процесса* ⚠️ вопрос с подвохом"
        assert "Состояния процесса" not in questions[0].variants[0].text

    def test_question_without_variant_labels(self):
        _, questions = parse_question_bank(BANK)
        variant = questions[1].variants[0]
        assert variant.label is None
        assert variant.text == "Что хранит TLB?"
        assert variant.correct == 1

    def test_question_without_weight(self):
        _, questions = parse_question_bank(bank_question("Вопрос?\n- a ✓\n- b\n- c\n- d\n", "### Вопрос 3"))
        assert questions[0].weight is None

    def test_english_labels(self):
        body = "### Question 1 — Type 1, 50%\n**Variant A:** Which?\n- a\n- b ✓\n- c\n- d\n"
        _, questions = parse_question_bank(body)
        assert questions[0].label == "Question 1"
        assert questions[0].variants[0].label == "Variant A"


class TestBankErrors:
    def errors(self, body: str) -> list[str]:
        with pytest.raises(QuizFormatError) as exc:
            parse_question_bank(body)
        return exc.value.errors

    def test_three_and_five_options(self):
        body = (
            "### Вопрос 1.1, 50%\n**Вариант А:** Q?\n- a ✓\n- b\n- c\n"
            "**Вариант Б:** Q?\n- a ✓\n- b\n- c\n- d\n- e\n"
        )
        assert self.errors(body) == [
            "Вопрос 1.1, Вариант А: 3 вариантов ответа (нужно 4)",
            "Вопрос 1.1, Вариант Б: 5 вариантов ответа (нужно 4)",
        ]

    def test_two_marks_and_no_marks(self):
        body = (
            "### Вопрос 1.2, 50%\n**Вариант А:** Q?\n- a ✓\n- b ✓\n- c\n- d\n"
            "### Вопрос 1.3, 50%\nQ?\n- a\n- b\n- c\n- d\n"
        )
        errors = self.errors(body)
        assert any(e.startswith("Вопрос 1.2, Вариант А: отмечено правильных ответов - 2") for e in errors)
        assert any(e.startswith("Вопрос 1.3: отмечено правильных ответов - 0") for e in errors)

    def test_empty_text(self):
        assert self.errors("### Вопрос 4.1\n**Вариант А:**\n- a ✓\n- b\n- c\n- d\n") == [
            "Вопрос 4.1, Вариант А: пустой текст вопроса"
        ]

    def test_all_problems_listed_together(self):
        body = (
            "### Вопрос 1\n**Вариант А:**\n- a\n- b\n- c\n"
            "### Вопрос 2\nQ?\n- a ✓\n- b ✓\n- c\n- d\n"
        )
        assert len(self.errors(body)) == 4

    def test_no_questions(self):
        assert self.errors("# Пусто\n\nнет вопросов\n")[0].startswith("В файле нет вопросов")


class TestParameters:
    def test_front_matter_round_trip(self):
        settings = QuizSettings(title="Квиз 5", short_name="К5", max_points=1.5, seconds_per_question=30)
        text = build_quiz_file(settings, BANK)
        meta, body = split_front_matter(text)
        assert meta == {"title": "Квиз 5", "short-name": "К5", "max-points": 1.5, "seconds-per-question": 30}
        assert body.startswith("# Квиз по лекции 5")
        assert parse_quiz(text).settings == settings

    def test_rebuilding_replaces_old_front_matter(self):
        first = build_quiz_file(QuizSettings("A", "К1", 1), BANK)
        quiz = parse_quiz(first, {"short-name": "К2"})
        second = build_quiz_file(quiz.settings, quiz.body)
        assert second.count("---\n") >= 2
        assert split_front_matter(second)[0]["short-name"] == "К2"
        assert second.count("short-name") == 1

    def test_defaults(self):
        quiz = parse_quiz(BANK, {"short-name": "К5", "max-points": "2"})
        assert quiz.settings.title == "Квиз по лекции 5"   # from the first # heading
        assert quiz.settings.seconds_per_question == 20
        assert quiz.settings.max_points == 2

    def test_form_overrides_front_matter(self):
        text = build_quiz_file(QuizSettings("A", "К1", 1, 25), BANK)
        quiz = parse_quiz(text, {"max-points": "3,5"})
        assert quiz.settings.max_points == 3.5
        assert quiz.settings.seconds_per_question == 25

    @pytest.mark.parametrize("values", [
        {"short-name": "К5", "max-points": 0},
        {"short-name": "К5", "max-points": "abc"},
        {"short-name": "", "max-points": 1},
        {"short-name": "К5", "max-points": 1, "seconds-per-question": 4},
        {"short-name": "К5", "max-points": 1, "seconds-per-question": 301},
    ])
    def test_invalid_parameters(self, values):
        with pytest.raises(QuizFormatError):
            settings_from(values, default_title="T")

    def test_quiz_id(self):
        assert quiz_id_from_filename("Lecture 05_Quiz.md") == "lecture-05-quiz"
        assert quiz_id_from_filename("квиз-5.md") == "5"
        with pytest.raises(QuizFormatError):
            quiz_id_from_filename("квиз.md")
