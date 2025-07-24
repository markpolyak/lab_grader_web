
import json
import re
import mosspy
from pathlib import Path
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime

class PlagiarismChecker(ABC):
    """Абстрактный класс (интерфейс) для проверки работ на плагиат"""
    def __init__(self, lab_id: str, language: str):
        self.lab_id = lab_id
        self.language = language

    @abstractmethod
    def run_check(
        self,
        student_files: List[Path],
        base_files: List[Path] = None,
        report_path: Path = None,
        **kwargs: Any
    ) -> Path:
        """
        Запускает проверку. Возвращает локальный путь до файла с отчетом.

        :param student_files: Список файлов студентов для проверки
        :param base_files: Базовые файлы (шаблоны, исключения)
        :param report_path: Путь к файлу сохранения отчета
        :param kwargs: Специфичные параметры для реализации (Например: опции MOSS)
        :return: Путь до файла с отчетом
        """
        pass

    @abstractmethod
    def parse_results(
      self,
      report_path: Path,
      threshold: float = 0.0,
      **kwargs: Any
    ) -> List[Dict]:
        """
        Парсит результаты проверки.

        :param report_path: Путь до файла с отчетом
        :param threshold: Порог фильтрации совпадений
        :param kwargs: Дополнительные параметры парсинга
        :return: Извлеченные пары совпадений
        """
        pass

class MossChecker(PlagiarismChecker):
    """ Реализация PlagiarismChecker с использованием сервиса Moss """

    class HTMLParserTextExtractor(HTMLParser):
        """ HTML-парсер, основанный на патерне visitor, для извлечения данных из html-тегов """
        def __init__(self):
            super().__init__()
            self.extracted_data = []

        def handle_data(self, data: str) -> None:
            stripped_data = data.strip()
            if stripped_data:
                self.extracted_data.append(stripped_data)

    def _read_html_doc(self, report_path: str) -> str:
        """ Читает отчет Moss"""
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except FileNotFoundError:
            raise FileNotFoundError
        except Exception as e:
            raise e

        return html_content

    @staticmethod
    def load_moss_report(report_url : str, report_save_path: Path) -> Path:
        """
        Загружает файл отчета MOSS, по предоставленной ссылке

        :param report_url: URL отчета MOSS
        :param report_save_path: Куда сохранить отчет
        :return: Путь до сохраненного отчета
        """

        moss = mosspy.Moss(user_id=0)
        moss.saveWebPage(report_url, report_save_path)
        return report_save_path

    def _extract_data_from_student_string(self, line: str) -> Optional[Tuple[str, str, str]]:
        """Извлекает данные из строки по двум шаблонам"""
        # Проверяем первый формат
        match1 = self.pattern1.fullmatch(line)
        if match1:
            return match1.group(1), match1.group(2), match1.group(3)

        # Проверяем второй формат
        match2 = self.pattern2.fullmatch(line)
        if match2:
            return match2.group(1), match2.group(2), match2.group(3)

        return None

    def __init__(
        self,
        lab_id: str,
        language: str,
        moss_user_id: int
    ):
        super().__init__(lab_id, language)
        self.moss_user_id = moss_user_id
        # Формат 1: <lab_id>_<course_name>_<username> (<percent>%)
        self.pattern1 = re.compile(r'^(\d+)_[^_]+_([a-zA-Z0-9]+)\s*\((\d+)%\)$')
        # Формат 2: <lab_id>_<username>_<filename>_<date> (<percent>%)
        self.pattern2 = re.compile(r'^(\d+)_([a-zA-Z0-9-]+)_[^_]+?_\d{4}-\d{2}-\d{2}\s*\((\d+)%\)$')

    def run_check(
        self,
        student_files: List[Path],
        base_files: List[Path] = None,
        report_path: Path = None,
        **kwargs: Any
    ) -> Path:
        """
        Отправляет файлы в Moss, запускает проверку, сохраняет отчет
        :param student_files: Список файлов студентов для проверки
        :param base_files: Базовые файлы (шаблоны, исключения)
        :param report_path: Путь к файлу сохранения отчета
        :param kwargs: Специфичные параметры для реализации (Например: опции MOSS)
        :return: Локальный Путь до файла с отчетом

        Доп. параметры:
        - max_matches
        """

        if report_path is None:
            report_path = f'MossChecker_report_{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.html'

        # Реализация отправки файлов

        moss = mosspy.Moss(user_id=self.moss_user_id, language=self.language)
        max_matches = kwargs.get('max_matches', 250)
        ignore_comments = kwargs.get('ignore_comments', False)
        moss.setIgnoreLimit(max_matches)
        moss.setCommentString(ignore_comments)

        for student_file in student_files:
            if student_file.exists():
                moss.addFile(str(student_file), str(student_file.stem))
            else:
                raise FileNotFoundError(f"Student file {str(student_file)} not found")

        for base_file in base_files:
            if base_file.exists():
                moss.addBaseFile(str(base_file))
            else:
                raise FileNotFoundError(f"Student file {str(base_file)} not found")

        url = moss.send()
        if (url is None) or (len(url) == 0):
            raise Exception("Cant send files to MOSS service (recievied URL is empty)")

        moss.saveWebPage(url, report_path)
        return Path(report_path)

    def parse_results(
          self,
          report_path: Path,
          threshold: float = 0.0,
          **kwargs: Any
    ) -> List[Dict]:
        """
        Парсит HTML-страницу с результатами Moss

        :param report_path: Путь до HTML отчета MOSS
        :param threshold: Минимальный процент совпадения для учета
        :param kwargs: Дополнительные параметры парсинга для отчетов *MOSS*
        :return: Извлеченные пары совпадений
        """

        # Чтение файла отчета
        if not report_path.exists():
            raise FileNotFoundError(f"Report file: {report_path} not found")
        html_doc = self._read_html_doc(str(report_path))
        html_doc_lower = html_doc.lower()

        # Проверка что файл HTML документ
        if html_doc_lower.find("<html>") == -1:
            raise ValueError("File is not valid HTML document")

        # Проверка наличия таблицы
        if "<table>" not in html_doc_lower:
            raise ValueError("HTML document does not contain a table")

        # Находим все строки таблицы
        tr_start = 0
        tr_positions = []
        while True:
            tr_start = html_doc_lower.find("<tr>", tr_start)
            if tr_start == -1:
                break
            tr_end = html_doc_lower.find("</tr>", tr_start)

            if tr_end == -1:
                next_tr = html_doc_lower.find("<tr>", tr_start + 4)
                tr_end = next_tr if next_tr != -1 else len(html_doc)
            else:
                tr_end += 5

            tr_positions.append((tr_start, tr_end))
            tr_start = tr_end

        # Пропускаем заголовок таблицы
        if tr_positions:
            tr_positions = tr_positions[1:]

        pairs = []
        for start, end in tr_positions:
            # Извлекаем содержимое строки таблицы
            tr_content = html_doc[start:end]
            parser = self.HTMLParserTextExtractor()
            parser.feed(tr_content)

            # Ожидаем 3 элемента: два студента + количество строк
            if len(parser.extracted_data) < 3:
                continue

            student1_data = parser.extracted_data[0]
            student2_data = parser.extracted_data[1]
            lines_count = parser.extracted_data[2]

            # Извлекаем данные студентов
            stud1 = self._extract_data_from_student_string(student1_data)
            stud2 = self._extract_data_from_student_string(student2_data)

            # Проверяем что оба студента распознаны
            if not stud1 or not stud2:
                #print(f"Skipping invalid data: {student1_data} | {student2_data}")
                continue

            lab_id1, github1, percent1 = stud1
            lab_id2, github2, percent2 = stud2

            if self.lab_id != lab_id1 or self.lab_id != lab_id2:
                raise ValueError("Different lab ids")

            # Проверяем что работы одного типа
            if lab_id1 != lab_id2:
                #print(f"Skipping mismatched labs: {lab_id1} vs {lab_id2}")
                raise Exception(f"Lab IDs from student dont match: {student1_data} vs {student2_data}")

            # Формируем результат
            pairs.append({
                "student1": github1,
                "student2": github2,
                "match1": percent1,
                "match2": percent2,
                "lines": lines_count,
                "lab_id": lab_id1
            })

        return pairs



class PlagiarismDetectionService:
    """ Сервис производящий проверку студентческих файлов на плагиат """

    def __init__(self, checker: PlagiarismChecker, course_config: Dict, lab_id: str):
        self.checker = checker
        self.results: List[Dict] = []
        self.course_config = course_config
        self.lab_id = lab_id

        # Setup Checker
        self.checker.lab_id = self.lab_id
        try:
            lang_str = self.course_config['course']['labs'][self.lab_id]['moss']['language']
        except:
            raise Exception("Course config parsing error: cant get lab language")

        self.checker.language = lang_str

    def save_results_to_json(self, json_path : str):
        """ Сохраняет результаты проверки работ в формате JSON"""
        try:
            with open(json_path, 'w', encoding='utf-8') as file:
                json.dump(self.results, file, ensure_ascii=False, indent=4)
            print(f"Результаты успешно сохранены в {json_path}")
        except Exception as e:
            print(f"Ошибка при сохранении JSON: {e}")
            raise

    def load_results_from_json(self, json_path: str):
        """ Читает и устанавливает пары совпадений из формата jSON """
        with open(json_path, "r", encoding="utf-8") as file:
            data: List[Dict] = json.load(file)
        self.results = data

    @staticmethod
    def save_results_to_json_ex(results : List[Dict], json_path : str):
        """
        Сохраняет результаты проверки работ в формате JSON
        :param results: Результаты с извлеченными совпадениями
        :param json_path: Путь к новому JSON
        :exception Exception: Ошибка при сохранении JSON
        """
        try:
            with open(json_path, 'w', encoding='utf-8') as file:
                json.dump(results, file, ensure_ascii=False, indent=4)
            print(f"Результаты успешно сохранены в {json_path}")
        except Exception as e:
            print(f"Ошибка при сохранении JSON: {e}")
            raise

    def get_results(self) -> List[Dict]:
        """ Получить результаты проверки на плагиат """
        return self.results

    def _filter_by_threshold(self, threshold: float) -> List[Dict]:
        """
        Фильтрует результаты проверки, оставляя только те совпадения,
        где максимальный процент совпадения (match1 или match2) >= threshold

        :param threshold: Пороговое значение (0-100) для фильтрации
        :return: Отфильтрованный список результатов
        """
        if not isinstance(threshold, (int, float)):
            raise ValueError("Threshold must be a number")

        threshold *= 100
        if threshold < 0 or threshold > 100:
            raise ValueError("Threshold must be between 0 and 100")

        filtered_results = []

        for result in self.results:
            try:
                # Преобразуем проценты в числа и берем максимальное значение
                match1 = float(result.get("match1", 0))
                match2 = float(result.get("match2", 0))
                max_match = max(match1, match2)

                if max_match >= threshold:
                    filtered_results.append(result)
            except (ValueError, TypeError) as e:
                # Пропускаем записи с некорректными данными
                continue

        return filtered_results

    def run_check_manual(
            self,
            students_files: List[Path],
            base_files: List[Path],
            threshold: float,
            **checker_kwargs
    ) -> List[Dict]:
        """
        Запускает цикл проверки

        :param students_files: Список файлов студентов для проверки
        :param base_files: Базовые файлы (шаблоны, исключения)
        :param threshold: Минимальный процент совпадения для учета
        :param checker_kwargs: Специфичные параметры для реализации (Например: опции MOSS)
        :return: Извлеченные пары совпадений
        """

        # Checker Strategy
        if isinstance(self.checker, MossChecker):
            # Получаем user_id из kwargs или из атрибута объекта
            user_id = checker_kwargs.get("moss_user_id", self.checker.moss_user_id)

            if not user_id:
                raise ValueError("Moss checker is disabled, moss_user_id not provided")


            # Запускаем проверку с распаковкой kwargs
            report_path = self.checker.run_check(
                students_files,
                base_files,
                **checker_kwargs
            )

            # Парсим результаты
            self.results = self.checker.parse_results(
                report_path,
                threshold=threshold,
                **checker_kwargs
            )
        else:
            raise NotImplementedError("This checker type is not supported yet")

        self.results = self._filter_by_threshold(threshold)
        return self.results

