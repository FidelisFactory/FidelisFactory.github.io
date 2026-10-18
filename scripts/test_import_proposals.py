"""Tests for import_proposals.py: python3 -m unittest discover -s scripts"""
import json
import os
import tempfile
import unittest

import import_proposals as ip


def proposal(number, school="Kantonsschule Musterhausen", year="1. Klasse", semester="HS 25", subjects=None, labels=()):
    """An issue in the format the Android app files it in."""
    if subjects is None:
        subjects = [{"name": "Mathe", "weight": "1", "counted": True}, {"name": "Sport", "weight": "1", "counted": False}]
    # Like the app, which leaves out a blank year.
    raw = {"school": school, **({"description": year} if year else {}), "subjects": subjects}
    body = (
        '**Proposed by a student, via the app\'s "Propose a template" flow.**\n\n'
        f"- School: {school}\n- Year: {year}\n"
        f"- Their own semester name (context only, not the template name): {semester}\n\n"
        "| Subject | Weight | Counts |\n|---|---|---|\n| Mathe | 1 | ✅ |\n\n"
        "<details><summary>Raw JSON (for turning this into data/*.json)</summary>\n\n"
        f"```json\n{json.dumps(raw, indent=4, ensure_ascii=False)}\n```\n</details>"
    )
    return {"number": number, "body": body, "labels": [{"name": "template-proposal"}, *({"name": label} for label in labels)]}


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.templates = self.directory.name
        self.write("Kantonsschule Zürich Nord", "1Klasse.json", {
            "id": 4000, "name": "1. Klasse", "description": "", "school": "KZN", "subject_count": 1,
            "subjects": [{"name": "Latein", "weight": "1", "counted": True}],
        })

    def tearDown(self):
        self.directory.cleanup()

    def write(self, folder, filename, data):
        os.makedirs(os.path.join(self.templates, folder), exist_ok=True)
        with open(os.path.join(self.templates, folder, filename), "w", encoding="utf-8") as file:
            file.write(json.dumps(data))

    def read(self, path):
        with open(os.path.join(self.templates, path), encoding="utf-8") as file:
            return file.read()

    def run_import(self, *issues, **kwargs):
        return ip.import_proposals(list(issues), templates_dir=self.templates, blocked_words={"test", "fuck"}, **kwargs)

    def test_adds_a_template_like_the_existing_files(self):
        subjects = [
            {"name": "  Mathe  🧙🏻‍♂️ ", "weight": "1.0", "counted": True},
            {"name": "", "weight": "1", "counted": True},
            {"name": "Sport", "weight": "0,5", "counted": False},
        ]
        [result] = self.run_import(proposal(41, school=" Gymnasium  Neu ", year="1.  Klasse", semester="HS 25 🥀", subjects=subjects))

        self.assertEqual({"number": 41, "action": "added", "id": 6000, "path": "Gymnasium Neu/1Klasse.json"}, result)
        self.assertEqual(
            '{"id": 6000, "name": "1. Klasse", "description": "HS 25 \\ud83e\\udd40", "school": "Gymnasium Neu", '
            '"subject_count": 2, "subjects": [{"name": "Mathe \\ud83e\\uddd9\\ud83c\\udffb\\u200d\\u2642\\ufe0f", '
            '"weight": "1", "counted": true}, {"name": "Sport", "weight": "0,5", "counted": false}]}',
            self.read(result["path"]),
        )

    def test_the_semester_name_is_only_kept_where_it_adds_something(self):
        results = self.run_import(
            proposal(41, school="A", year="GYM 2", semester="gym 2"),
            proposal(42, school="B", year="9. Klasse", semester="B"),
            proposal(43, school="C", year="", semester="Gym 1"),
        )

        files = [json.loads(self.read(result["path"])) for result in results]
        self.assertEqual([("GYM 2", ""), ("9. Klasse", ""), ("Gym 1", "")], [(f["name"], f["description"]) for f in files])

    def test_continues_after_the_highest_id(self):
        self.write("Other", "Template.json", {"id": 6500, "name": "", "description": "", "school": "Other", "subjects": []})
        results = self.run_import(proposal(41, school="A"), proposal(42, school="B"))

        self.assertEqual([6501, 6502], [result["id"] for result in results])

    def test_goes_into_the_folder_of_the_same_school(self):
        results = self.run_import(
            proposal(41, school="kantonsschule zurich  nord"),  # the folder's name
            proposal(42, school="kzn", subjects=[{"name": "Bio", "weight": "1", "counted": True}]),  # a school in the folder
            proposal(43, school="Kanti Baden/Wettingen"),
            proposal(44, school=".hidden: school?"),
        )

        self.assertEqual(
            ["Kantonsschule Zürich Nord/1Klasse-2.json", "Kantonsschule Zürich Nord/1Klasse-3.json",
             "Kanti Baden-Wettingen/1Klasse.json", "hidden- school-/1Klasse.json"],
            [result["path"] for result in results],
        )
        # The school stays as the student typed it, whatever the folder.
        self.assertEqual("kzn", json.loads(self.read(results[1]["path"]))["school"])

    def test_doesnt_add_a_semester_twice(self):
        latin = [{"name": "latein", "weight": "1.0", "counted": True}]
        results = self.run_import(proposal(41, school="KZN", subjects=latin), proposal(42), proposal(43, year="2. Klasse"))

        self.assertEqual(
            [("duplicate", 4000, "Kantonsschule Zürich Nord/1Klasse.json"),
             ("added", 6000, "Kantonsschule Musterhausen/1Klasse.json"),
             ("duplicate", 6000, "Kantonsschule Musterhausen/1Klasse.json")],
            [(result["action"], result["id"], result["path"]) for result in results],
        )

    def test_rejects_what_the_app_cant_use(self):
        results = self.run_import(
            {"number": 41, "body": "No JSON here", "labels": []},
            proposal(42, subjects=[{"name": "Mathe", "weight": "0", "counted": True}]),
            proposal(43, subjects=[{"name": "", "weight": "1", "counted": True}]),
            proposal(44, subjects=[{"name": "Mathe", "weight": "1"}]),
            proposal(45, school="x" * 101),
            proposal(46, subjects=[{"name": f"Fach {i}", "weight": "1", "counted": True} for i in range(41)]),
        )

        self.assertEqual(["rejected"] * 6, [result["action"] for result in results])
        self.assertFalse(any(result["soft"] for result in results))
        self.assertEqual(["Kantonsschule Zürich Nord/1Klasse.json"], [f"{f}/{n}" for f, n, _ in ip.read_existing(self.templates)])

    def test_rejects_test_entries_insults_and_contact_details_unless_approved(self):
        results = self.run_import(
            proposal(41, school="test4"),
            proposal(42, subjects=[{"name": "Mathe", "weight": "1", "counted": True}, {"name": "FUCK", "weight": "1", "counted": True}]),
            proposal(43, semester="Call me: +41 79 123 45 67"),
            proposal(44, school="See www.example.com"),
            proposal(45, school="<b>School</b>"),
            proposal(46, school="test4", labels=["template-approved"]),
            proposal(47, school="Testfach", year="Schuljahr 2025/2026 <3", semester="info at school"),
        )

        self.assertEqual(
            [("rejected", True)] * 5 + [("added", None), ("added", None)],
            [(result["action"], result.get("soft")) for result in results],
        )

    def test_strips_invisible_characters_but_keeps_emoji(self):
        [result] = self.run_import(proposal(41, school="Schule‮ Nord​", subjects=[{"name": "👩🏽‍🔬 Chemie", "weight": "1", "counted": True}]))

        data = json.loads(self.read(result["path"]))
        self.assertEqual(("Schule Nord", "👩🏽‍🔬 Chemie"), (data["school"], data["subjects"][0]["name"]))

    def test_takes_the_oldest_proposals_first_up_to_the_limit(self):
        results = self.run_import(proposal(45, school="C"), proposal(41, school="A"), proposal(43, school="B"), limit=2)

        self.assertEqual([41, 43], [result["number"] for result in results])

    def test_every_added_template_passes_the_check(self):
        self.run_import(proposal(41), proposal(42, school="GYMO, SPF PPP", year="3.1"))

        report = ip.Report()
        for folder, filename, data in ip.read_existing(self.templates):
            ip.check_template(f"{folder}/{filename}", data, report)
        self.assertEqual([], report.errors)


class AnswerTest(unittest.TestCase):
    def test_comments_labels_and_closes_each_proposal(self):
        commands = []
        results = [
            {"number": 41, "action": "added", "id": 6000, "path": "A/1Klasse.json"},
            {"number": 42, "action": "rejected", "reason": "it has no school name", "soft": False},
        ]

        ip.answer(results, "Owner/Repo", run=lambda command, check: commands.append(command))

        issue_commands = [command[1:4] for command in commands if command[1] == "issue"]
        self.assertEqual(
            [["issue", "comment", "41"], ["issue", "edit", "41"], ["issue", "close", "41"],
             ["issue", "comment", "42"], ["issue", "edit", "42"], ["issue", "close", "42"]],
            issue_commands,
        )
        self.assertIn("template 6000: `pluspoints/templates/A/1Klasse.json`", commands[3][commands[3].index("--body") + 1])
        self.assertEqual(["template-added", "template-rejected"], [c[c.index("--add-label") + 1] for c in commands if "--add-label" in c])
        self.assertEqual(["completed", "not planned"], [c[c.index("--reason") + 1] for c in commands if "--reason" in c])
        self.assertTrue(all(command[-2:] == ["--repo", "Owner/Repo"] for command in commands))

    def test_the_word_list_reads_one_word_per_line(self):
        words = ip.load_blocked_words()

        self.assertIn("test", words)
        self.assertNotIn("", words)
        self.assertFalse(any(" " in word or "#" in word for word in words))


if __name__ == "__main__":
    unittest.main()
