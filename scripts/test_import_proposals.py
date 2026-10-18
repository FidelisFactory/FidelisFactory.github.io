"""Tests for import_proposals.py: python3 -m unittest discover -s scripts"""
import json
import os
import tempfile
import unittest
from unittest import mock

import import_proposals as ip

MATHE_SPORT = [{"name": "Mathe", "weight": "1", "counted": True}, {"name": "Sport", "weight": "1", "counted": False}]


def body(school="Kantonsschule Musterhausen", year="1. Klasse", semester="HS 25", subjects=MATHE_SPORT):
    """An issue body in the format the Android app files it in."""
    # Like the app, which leaves out a blank year.
    raw = {"school": school, **({"description": year} if year else {}), "subjects": subjects}
    return (
        '**Proposed by a student, via the app\'s "Propose a template" flow.**\n\n'
        f"- School: {school}\n- Year: {year}\n"
        f"- Their own semester name (context only, not the template name): {semester}\n\n"
        "| Subject | Weight | Counts |\n|---|---|---|\n| Mathe | 1 | ✅ |\n\n"
        "<details><summary>Raw JSON (for turning this into data/*.json)</summary>\n\n"
        f"```json\n{json.dumps(raw, indent=4, ensure_ascii=False)}\n```\n</details>"
    )


def proposal(number, labels=(), **fields):
    return {"number": number, "body": body(**fields), "labels": [{"name": "template-proposal"}, *({"name": label} for label in labels)]}


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.templates = self.directory.name
        self.write("Kantonsschule Zürich Nord", "1Klasse.json", {
            "id": 4000, "name": "1. Klasse", "description": "", "school": "KZN", "subject_count": 1,
            "subjects": [{"name": "Latein", "weight": "1", "counted": True}],
        })
        with open(os.path.join(self.templates, "README.md"), "w", encoding="utf-8") as file:
            file.write("# Templates")

    def tearDown(self):
        self.directory.cleanup()

    def write(self, folder, filename, data):
        os.makedirs(os.path.join(self.templates, folder), exist_ok=True)
        with open(os.path.join(self.templates, folder, filename), "w", encoding="utf-8") as file:
            file.write(json.dumps(data))

    def read(self, path):
        with open(os.path.join(self.templates, path), encoding="utf-8") as file:
            return file.read()

    def run_import(self, *issues, approvals=None):
        results, _ = ip.import_proposals(
            list(issues), templates_dir=self.templates, blocked_words=({"cunt"}, {"fuck", "hurensohn"}), approvals=approvals or {}
        )
        return results

    def paths(self):
        return [f"{folder}/{filename}" for folder, filename, _ in ip.read_existing(self.templates)]

    def test_adds_a_template_like_the_existing_files(self):
        subjects = [
            {"name": "  Mathe  🧙🏻‍♂️ ", "weight": "1.0", "counted": True},
            {"name": "", "weight": "1", "counted": True},
            {"name": "Sport", "weight": "0,5", "counted": False},
        ]
        [result] = self.run_import(proposal(41, school=" Gymnasium  Neu ", year="1.  Klasse", semester="HS 25 🥀", subjects=subjects))

        self.assertEqual(
            {"number": 41, "labels": ["template-proposal"], "action": "added", "id": 10041, "path": "Gymnasium Neu/1Klasse.json", "written": True},
            result,
        )
        self.assertEqual(
            '{"id": 10041, "name": "1. Klasse", "description": "HS 25 \\ud83e\\udd40", "school": "Gymnasium Neu", '
            '"subject_count": 2, "subjects": [{"name": "Mathe \\ud83e\\uddd9\\ud83c\\udffb\\u200d\\u2642\\ufe0f", '
            '"weight": "1", "counted": true}, {"name": "Sport", "weight": "0,5", "counted": false}]}',
            self.read(result["path"]),
        )

    def test_names_it_after_the_year_and_keeps_the_semester_name_where_it_adds_something(self):
        results = self.run_import(
            proposal(41, school="A", year="GYM 2", semester="gym 2"),
            proposal(42, school="B", year="9. Klasse", semester="B"),
            proposal(43, school="C", year="", semester="Gym 1"),
        )

        files = [json.loads(self.read(result["path"])) for result in results]
        self.assertEqual([("GYM 2", ""), ("9. Klasse", ""), ("Gym 1", "")], [(f["name"], f["description"]) for f in files])

    def test_answers_a_proposal_added_by_an_earlier_run_as_added(self):
        # The earlier run's answer didn't go through, so the issue is still open.
        self.write("A", "1Klasse.json", {"id": 10041, "name": "1. Klasse", "description": "", "school": "A", "subjects": MATHE_SPORT})

        [result] = self.run_import(proposal(41, school="A"))

        self.assertEqual(("added", 10041, "A/1Klasse.json", False), (result["action"], result["id"], result["path"], result["written"]))

    def test_goes_into_the_folder_of_the_same_school(self):
        results = self.run_import(
            proposal(41, school="kantonsschule zurich  nord"),  # the folder's name
            proposal(42, school="kzn", subjects=[{"name": "Bio", "weight": "1", "counted": True}]),  # a school in the folder
            proposal(43, school="Kanti Baden/Wettingen"),
        )

        self.assertEqual(
            ["Kantonsschule Zürich Nord/1Klasse-2.json", "Kantonsschule Zürich Nord/1Klasse-3.json", "Kanti Baden-Wettingen/1Klasse.json"],
            [result["path"] for result in results],
        )
        # The school stays as the student typed it, whatever the folder.
        self.assertEqual("kzn", json.loads(self.read(results[1]["path"]))["school"])

    def test_new_folder_and_file_names_work_on_every_system(self):
        schools = [".hidden: school?", "README.md", "git~1", "__pycache__", "Aux.Kanti", "COM¹", "NUL .x", "🦄" * 70, "x" * 99]
        results = self.run_import(*(proposal(41 + i, school=school) for i, school in enumerate(schools)), proposal(60, school="Con", year="Con"))

        self.assertEqual(["added"] * 10, [result["action"] for result in results])
        self.assertEqual(
            ["hidden school", "README.md 2", "git1", "pycache", "School Aux.Kanti", "School COM1", "School NUL .x", "School", "x" * 80, "School Con"],
            [result["path"].split("/")[0] for result in results],
        )
        self.assertEqual("Con-2.json", results[-1]["path"].split("/")[1])

    def test_doesnt_add_a_semester_twice(self):
        latin = [{"name": "latein", "weight": "1.0", "counted": True}]
        results = self.run_import(proposal(41, school="KZN", subjects=latin), proposal(42), proposal(43, year="2. Klasse"))

        self.assertEqual(
            [("duplicate", 4000, "Kantonsschule Zürich Nord/1Klasse.json"),
             ("added", 10042, "Kantonsschule Musterhausen/1Klasse.json"),
             ("duplicate", 10042, "Kantonsschule Musterhausen/1Klasse.json")],
            [(result["action"], result["id"], result["path"]) for result in results],
        )

    def test_rejects_what_the_app_cant_use(self):
        results = self.run_import(
            {"number": 41, "body": "No JSON here", "labels": []},
            proposal(42, subjects=[{"name": "Mathe", "weight": "0", "counted": True}]),
            proposal(43, subjects=[{"name": "‍", "weight": "1", "counted": True}]),
            proposal(44, subjects=[{"name": "Mathe", "weight": "1"}]),
            proposal(45, school="x" * 101),
            proposal(46, subjects=[{"name": f"Fach {i}", "weight": "1", "counted": True} for i in range(41)]),
            proposal(47, school="ㅤ⁠ ­"),
            proposal(48, subjects=[{"name": "Mathe", "weight": "1" * 11, "counted": True}]),
        )

        self.assertEqual([("rejected", False)] * 8, [(result["action"], result["soft"]) for result in results])
        self.assertEqual(["Kantonsschule Zürich Nord/1Klasse.json"], self.paths())

    def test_a_crafted_proposal_only_rejects_itself(self):
        lone_surrogate = '```json\n{"school": "A", "subjects": [{"name": "Mathe \\ud83e", "weight": "1", "counted": true}]}\n```'
        results = self.run_import(
            {"number": 41, "body": "```json\n" + "[" * 30000 + "]" * 30000 + "\n```", "labels": []},
            proposal(42, subjects=[{"name": "Mathe", "weight": "1" * 4301 + "/1", "counted": True}]),
            {"number": 43, "body": lone_surrogate, "labels": []},
            {"number": 44, "labels": []},
            proposal(45),
        )

        self.assertEqual(["rejected"] * 4 + ["added"], [result["action"] for result in results])
        self.assertEqual(["Kantonsschule Musterhausen/1Klasse.json", "Kantonsschule Zürich Nord/1Klasse.json"], self.paths())

    def test_an_unexpected_error_only_rejects_that_proposal(self):
        real_add = ip.Templates.add

        def add(templates, folder, filename, data):
            if data["school"] == "Broken":
                raise OSError("disk full")
            real_add(templates, folder, filename, data)

        with mock.patch.object(ip.Templates, "add", add):
            results = self.run_import(proposal(41, school="Broken"), proposal(42))

        self.assertEqual(
            [("rejected", "it couldn't be processed"), ("added", None)], [(result["action"], result.get("reason")) for result in results]
        )

    def test_rejects_test_entries_insults_and_contact_details(self):
        results = self.run_import(
            proposal(41, school="test4"),
            proposal(42, subjects=[{"name": "Fuckyou", "weight": "1", "counted": True}]),
            proposal(43, school="Hurensohnschule"),
            proposal(44, semester="Call me: 079/123/45/67"),
            proposal(45, school="Kanti discord.gg/abc"),
            proposal(46, school="See www.example.com"),
            proposal(47, school="<b>School</b>"),
            proposal(48, school="Testfach", year="Schuljahr 2025/2026 <3", semester="St.Gallen 1.9.2025",
                     subjects=[{"name": "Französisch Voci-Test", "weight": "1", "counted": True}]),
            proposal(49, school="Kantonsschule Wattwil, Scunthorpe"),
        )

        self.assertEqual(
            [("rejected", True)] * 7 + [("added", None)] * 2,
            [(result["action"], result.get("soft")) for result in results],
        )
        self.assertEqual("41 " + ip.body_hash(body(school="test4")), results[0]["approval"])

    def test_an_approval_skips_the_soft_checks_for_the_approved_text_only(self):
        zero = [{"name": "Mathe", "weight": "0", "counted": True}]
        approved = ip.body_hash(body(school="test4"))
        results = self.run_import(
            proposal(41, school="test4"),
            proposal(42, school="test4", year="2. Klasse"),  # approved, but edited since
            proposal(43, school="test4", subjects=zero),
            approvals={41: approved, 42: approved, 43: ip.body_hash(body(school="test4", subjects=zero))},
        )

        self.assertEqual([("added", None), ("rejected", True), ("rejected", False)], [(r["action"], r.get("soft")) for r in results])

    def test_strips_invisible_characters_but_keeps_emoji(self):
        [result] = self.run_import(proposal(41, school="Schule‮ Nord​", subjects=[{"name": "👩🏽‍🔬 Chemie", "weight": "1", "counted": True}]))

        data = json.loads(self.read(result["path"]))
        self.assertEqual(("Schule Nord", "👩🏽‍🔬 Chemie"), (data["school"], data["subjects"][0]["name"]))

    def test_takes_the_oldest_proposals_first_and_stops_at_the_limits(self):
        issues = [proposal(45, school="C"), proposal(41, school="A"), {"number": 42, "body": "", "labels": []}, proposal(43, school="B")]

        with mock.patch.object(ip, "MAX_ADDED_PER_RUN", 2):
            results, waiting = ip.import_proposals(issues, templates_dir=self.templates, blocked_words=(set(), set()), approvals={})
        self.assertEqual(([41, 42, 43], 1), ([result["number"] for result in results], waiting))

        with mock.patch.object(ip, "MAX_HANDLED_PER_RUN", 1):
            results, waiting = ip.import_proposals(issues, templates_dir=self.templates, blocked_words=(set(), set()), approvals={})
        self.assertEqual(([41], 3), ([result["number"] for result in results], waiting))

    def test_leaves_the_proposals_added_by_hand_alone(self):
        results, waiting = ip.import_proposals([proposal(38)], templates_dir=self.templates, blocked_words=(set(), set()), approvals={})

        self.assertEqual(([], 0), (results, waiting))

    def test_every_added_template_passes_the_check(self):
        self.run_import(proposal(41), proposal(42, school="GYMO, SPF PPP", year="3.1"), proposal(43, school="🦄" * 70))

        report = ip.Report()
        for folder, filename, data in ip.read_existing(self.templates):
            ip.check_template(f"{folder}/{filename}", data, report)
        self.assertEqual([], report.errors)


class RulesTest(unittest.TestCase):
    def test_the_soft_checks_pass_every_existing_template(self):
        # The curated templates are real schools, so these checks must not
        # reject any of them.
        blocked_words = ip.load_blocked_words()
        problems = []
        for folder, filename, data in ip.read_existing(ip.TEMPLATES_DIR):
            texts = [data["school"], data["name"], data["description"]] + [subject["name"] for subject in data["subjects"]]
            if ip.soft_problem(ip.clean(data["school"]), [ip.clean(text) for text in texts], blocked_words):
                problems.append(f"{folder}/{filename}")
        self.assertEqual([], problems)

    def test_the_word_list_and_the_approvals_file_can_be_read(self):
        blocked, parts = ip.load_blocked_words()

        self.assertIn("cunt", blocked)
        self.assertIn("fuck", parts)
        self.assertFalse(any(not word or " " in word or "#" in word or "*" in word for word in blocked | parts))
        self.assertEqual({}, ip.load_approvals())


class AnswerTest(unittest.TestCase):
    def answer(self, results):
        commands = []
        ip.answer(results, "Owner/Repo", run=lambda command, check: commands.append(command), pause=lambda seconds: None)
        self.assertTrue(all(command[-2:] == ["--repo", "Owner/Repo"] for command in commands))
        return [command[:-2] for command in commands if command[1] == "issue"]

    def test_comments_labels_and_closes_each_proposal(self):
        added = {"number": 41, "labels": [], "action": "added", "id": 10041, "path": "A/1Klasse.json", "written": True}
        rejected = {"number": 42, "labels": [], "action": "rejected", "reason": "it has no school name", "soft": False}

        commands = self.answer([added, rejected])

        self.assertEqual(
            [["gh", "issue", "comment", "41", "--body", ip.comment_for(added)],
             ["gh", "issue", "edit", "41", "--add-label", "template-added"],
             ["gh", "issue", "close", "41", "--reason", "completed"],
             ["gh", "issue", "comment", "42", "--body", ip.comment_for(rejected)],
             ["gh", "issue", "edit", "42", "--add-label", "template-rejected"],
             ["gh", "issue", "close", "42", "--reason", "not planned"]],
            commands,
        )
        self.assertIn("template 10041: `pluspoints/templates/A/1Klasse.json`", commands[0][-1])

    def test_an_approved_proposal_loses_its_rejected_label(self):
        commands = self.answer([{"number": 41, "labels": ["template-proposal", "template-rejected"], "action": "added", "id": 10041, "path": "A/1Klasse.json"}])

        self.assertEqual(["gh", "issue", "edit", "41", "--add-label", "template-added", "--remove-label", "template-rejected"], commands[1])

    def test_a_soft_rejection_shows_the_line_that_approves_it(self):
        comment = ip.comment_for({"action": "rejected", "reason": "it contains a link", "soft": True, "approval": "41 9f86d081884c7d65"})

        self.assertIn("`scripts/approved_proposals.txt`", comment)
        self.assertIn("```\n41 9f86d081884c7d65\n```", comment)


if __name__ == "__main__":
    unittest.main()
