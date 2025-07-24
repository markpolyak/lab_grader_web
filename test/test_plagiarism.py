import re
import unittest
import json
import yaml
from plagiarism import MossChecker, PlagiarismDetectionService
from typing import List, Dict, Optional, Tuple
from pathlib import Path


def read_json(jsonpath: str) -> list[dict]:
    with open(jsonpath, "r", encoding="utf-8") as file:
        data: List[Dict] = json.load(file)
    return data


class TestPlagiarism(unittest.TestCase):

    def test_mosschecker_load_moss_report_1(self):
        report_url = "http://moss.stanford.edu/results/8/6516272398616/"
        new_report_path = Path(".\\tmp\\test_mosschecker_load_moss_report_1.html")
        MossChecker.load_moss_report(report_url, new_report_path)

    def test_mosschecker_method_parse_results_1(self):
        """ Проверка метода parse_results() | Локальный отчет """
        results = (MossChecker(lab_id="2", language="cc", moss_user_id=1234)
                   .parse_results(Path(".\\mosschecker_reports\\moss_real_report_os-2024f.html")))

        excepted_results = read_json(".\\mosschecker_reports\\moss_real_results_os-2024f.json")
        self.assertEqual(results, excepted_results)

    def test_mosschecker_method_parse_results_2(self):
        """ Проверка метода parse_results() | По URL """
        report_url = "http://moss.stanford.edu/results/8/6516272398616/"
        new_report_path = Path(".\\tmp\\test_mosschecker_method_parse_results_2.html")
        MossChecker.load_moss_report(report_url, new_report_path)
        results = (MossChecker(lab_id="2", language="cc", moss_user_id=1234)
                   .parse_results(new_report_path))

        excepted_results = read_json(".\\mosschecker_reports\\moss_real_results_os-2024f.json")
        self.assertEqual(results, excepted_results)

    def test_mosschecker_method_run_check_1(self):
        """ Проверка метода run_check() | 4 файлы с работами """
        MOSS_TEST_USER_ID = CHANGE_ME
        folder = Path(".\\students-codes")
        students_files = [file for file in folder.iterdir() if file.is_file()]
        folder = Path(".\\base-files")
        base_files = [file for file in folder.iterdir() if file.is_file()]
        local_report_path = Path(".\\tmp\\test_mosschecker_run_check_1.html")
        rec_report_path = MossChecker(lab_id="2", language="cc", moss_user_id=MOSS_TEST_USER_ID).run_check(students_files, base_files, local_report_path)
        self.assertEqual(rec_report_path, local_report_path)
        self.assertTrue(rec_report_path.stat().st_size > 0)

    def test_mosschecker_method_parse_results_3(self):
        local_report_path = Path(".\\tmp\\test_mosschecker_run_check_1.html")
        results = (MossChecker(lab_id="2", language="cc", moss_user_id=1234)
                   .parse_results(local_report_path))
        excepted_results = read_json(".\\mosschecker_reports\\custom_results.json")
        self.assertEqual(results, excepted_results)

    def test_PlagiarismDetectionService_1(self):

        MOSS_TEST_USER_ID = 325834785

        ## Open Course Config
        with open("..\\courses\\operating-systems-2024f.yaml", "r") as f:
            course_config = yaml.safe_load(f)

        ## Get Students and Base Files
        folder = Path(".\\students-codes")
        students_files = [file for file in folder.iterdir() if file.is_file()]
        folder = Path(".\\base-files")
        base_files = [file for file in folder.iterdir() if file.is_file()]

        ## Run Checks

        moss_checker = MossChecker(
            language="cc",
            moss_user_id=MOSS_TEST_USER_ID,
            lab_id="2"
        )

        service = PlagiarismDetectionService(
            checker=moss_checker,
            course_config=course_config,
            lab_id="2"
        )

        results = service.run_check_manual(
            students_files,
            base_files,
            0.7,
            max_matches=1000,
            ignore_comments=True
        )

        service.save_results_to_json(".\\tmp\\run_check_man_results.json")


