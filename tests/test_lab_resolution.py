r"""
Тесты разрешения lab_id в конфиг лабораторной (main.find_lab_config).

Регрессия: ЛР0.1 (ключ "01") разрешалась в ЛР0 (ключ "0"), потому что
re.search(r"\d+", "ЛР0.1") останавливается на "0"; а /join/<курс>/01
разрешался в ЛР1, потому что int("01") == 1. В обоих случаях студент
получал чужой репозиторий и чужой столбец в таблице.
"""
import sys
import os

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module


# Раскладка лаб курса ОС (courses/operating-systems-2026.yaml) - именно на
# ней баг и проявляется в проде.
OS_LABS = {
    "0": {"short-name": "ЛР0", "github-prefix": "os-task0"},
    "01": {"short-name": "ЛР0.1", "github-prefix": "os-task-I-1"},
    "1": {"short-name": "ЛР1", "github-prefix": "os-task1"},
    "2": {"short-name": "ЛР2", "github-prefix": "os-task2"},
}


class TestFindLabConfigByExactKey:
    """Ключ YAML - так лабу адресуют URL /join и админки."""

    @pytest.mark.parametrize("lab_id,expected_short_name", [
        ("0", "ЛР0"),
        ("01", "ЛР0.1"),
        ("1", "ЛР1"),
        ("2", "ЛР2"),
    ])
    def test_exact_key_wins(self, lab_id, expected_short_name):
        key, config = main_module.find_lab_config(OS_LABS, lab_id)
        assert key == lab_id
        assert config["short-name"] == expected_short_name

    def test_leading_zero_key_is_not_collapsed_to_int(self):
        """int("01") == 1 делал ключ "01" недостижимым через /join."""
        key, config = main_module.find_lab_config(OS_LABS, "01")
        assert key == "01"
        assert config["github-prefix"] == "os-task-I-1"


class TestFindLabConfigByShortName:
    """Короткое имя - в таком виде lab_id приходит из интерфейса проверки,
    потому что GET /courses/{id}/groups/{gid}/labs отдаёт short-name."""

    @pytest.mark.parametrize("short_name,expected_key,expected_prefix", [
        ("ЛР0", "0", "os-task0"),
        ("ЛР0.1", "01", "os-task-I-1"),
        ("ЛР1", "1", "os-task1"),
        ("ЛР2", "2", "os-task2"),
    ])
    def test_short_name_resolves_to_its_own_lab(self, short_name, expected_key, expected_prefix):
        key, config = main_module.find_lab_config(OS_LABS, short_name)
        assert key == expected_key
        assert config["github-prefix"] == expected_prefix

    def test_fractional_short_name_is_not_truncated_to_first_number(self):
        """Регрессия: "ЛР0.1" разрешалась в ЛР0 - студент сдавал ЛР0.1, а
        проверялся репозиторий ЛР0 и результат уходил в столбец ЛР0."""
        key, config = main_module.find_lab_config(OS_LABS, "ЛР0.1")
        assert key == "01"
        assert config["short-name"] == "ЛР0.1"

    def test_short_name_without_digits_resolves(self):
        """fundamental-statistics-2025: short-name "Тест / КР" не содержит цифр,
        и прежний разбор падал с 400 "Некорректный lab_id"."""
        labs = {"7": {"short-name": "Тест / КР", "github-prefix": "stats-test"}}
        key, config = main_module.find_lab_config(labs, "Тест / КР")
        assert key == "7"
        assert config["github-prefix"] == "stats-test"


class TestFindLabConfigNumericFallback:
    """Запасной путь для строк, которые не являются ни ключом, ни short-name."""

    @pytest.mark.parametrize("lab_id,expected_key", [
        ("lab1", "1"),
        ("Lab2", "2"),
        ("лаба 2", "2"),
    ])
    def test_free_form_with_number(self, lab_id, expected_key):
        key, _config = main_module.find_lab_config(OS_LABS, lab_id)
        assert key == expected_key


class TestFindLabConfigNotFound:
    def test_unknown_number_returns_none(self):
        assert main_module.find_lab_config(OS_LABS, "99") is None

    def test_unknown_text_returns_none(self):
        assert main_module.find_lab_config(OS_LABS, "чего-то нет") is None

    def test_empty_labs_returns_none(self):
        assert main_module.find_lab_config({}, "1") is None

    def test_non_dict_labs_returns_none(self):
        assert main_module.find_lab_config(None, "1") is None


class TestNoRegressionForEveryProductionCourse:
    """Сквозная проверка по реальным конфигам: обращение и по ключу, и по
    short-name должно приводить ровно в ту лабу, которой они принадлежат."""

    def test_every_lab_of_every_course_resolves_to_itself(self):
        import glob
        import yaml

        checked = 0
        for path in sorted(glob.glob("courses/*.yaml")):
            if os.path.basename(path) == "index.yaml":
                continue
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            labs = (data.get("course") or {}).get("labs") or {}
            for key, config in labs.items():
                by_key = main_module.find_lab_config(labs, str(key))
                assert by_key is not None, f"{path}: ключ {key!r} не разрешается"
                assert by_key[0] == str(key), f"{path}: ключ {key!r} -> {by_key[0]!r}"

                short_name = config.get("short-name")
                if short_name:
                    by_name = main_module.find_lab_config(labs, short_name)
                    assert by_name is not None, f"{path}: short-name {short_name!r} не разрешается"
                    assert by_name[0] == str(key), (
                        f"{path}: short-name {short_name!r} -> ключ {by_name[0]!r}, ожидался {key!r}"
                    )
                checked += 1
        assert checked > 0


class TestJoinResolvesFractionalLab:
    """Эндпоинт /join должен отдавать ЛР0.1 по адресу /join/<курс>/01."""

    def test_join_info_returns_the_lab_addressed_by_key(self, monkeypatch):
        course = {
            "name": "ОС",
            "github": {"organization": "suai-os-2026"},
            "labs": {
                "0": {"short-name": "ЛР0", "github-prefix": "os-task0",
                      "template-repo": "org/t0"},
                "01": {"short-name": "ЛР0.1", "github-prefix": "os-task-I-1",
                       "template-repo": "org/t01"},
                "1": {"short-name": "ЛР1", "github-prefix": "os-task1",
                      "template-repo": "org/t1"},
            },
        }
        monkeypatch.setattr(main_module, "get_course_by_id", lambda _cid: course)

        _course, lab_config, _org = main_module._load_lab_for_join("os", "01")
        assert lab_config["short-name"] == "ЛР0.1"
        assert lab_config["template-repo"] == "org/t01"

        _course, lab_config, _org = main_module._load_lab_for_join("os", "1")
        assert lab_config["short-name"] == "ЛР1"

    def test_unknown_lab_still_404(self, monkeypatch):
        course = {"name": "ОС", "github": {"organization": "org"},
                  "labs": {"1": {"short-name": "ЛР1", "template-repo": "org/t1"}}}
        monkeypatch.setattr(main_module, "get_course_by_id", lambda _cid: course)

        with pytest.raises(HTTPException) as exc_info:
            main_module._load_lab_for_join("os", "99")
        assert exc_info.value.status_code == 404
