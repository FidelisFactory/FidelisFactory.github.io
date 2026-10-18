#!/usr/bin/env python3
"""Turns the template proposals students send from the app into templates.

The app files each proposal as an issue with the template-proposal label. Once a
day, the Import proposals workflow runs

    python3 scripts/import_proposals.py import --proposals FILE --results FILE

which reads the open proposals from FILE (the JSON of `gh issue list --json
number,body,labels`), writes a template file for each one that passes the checks
and records what it did in the results file. Once the new templates are on main,

    python3 scripts/import_proposals.py answer --results FILE

comments on each issue and closes it. Every rule is in this file, so no person
needs to review a proposal; pluspoints/templates/README.md describes them.

The issues can't be trusted: the token the app files them with ships inside the
app, so anyone can file a "proposal" with any text and set any label on it.
That's why approvals live in this repository (APPROVALS_FILE), and why nothing a
proposal contains may stop the import of the others.
"""
import argparse
import collections
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

from build_templates_bundle import MAX_ID, TEMPLATES_DIR, Report, check_template, weight_is_usable

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WORDS_FILE = os.path.join(SCRIPTS_DIR, "proposal_blocked_words.txt")
APPROVALS_FILE = os.path.join(SCRIPTS_DIR, "approved_proposals.txt")
ADDED_LABEL = "template-added"
REJECTED_LABEL = "template-rejected"
LABELS = (
    (ADDED_LABEL, "0e8a16", "The daily import added this proposal as a template"),
    (REJECTED_LABEL, "b60205", "The daily import didn't add this proposal; its comment says why"),
)
# The proposals up to #38 were added by hand, in pull request #39.
FIRST_ISSUE = 40
# An imported template's id is this plus the number of its issue: never reused,
# not even after a revert, and apart from the ids of templates added by hand.
ID_OFFSET = 10000
# A flood of proposals goes live over several days, and a run stays well within
# GitHub's limits for comments.
MAX_ADDED_PER_RUN = 20
MAX_HANDLED_PER_RUN = 50
MAX_SUBJECTS = 40
MAX_TEXT = 100
MAX_SUBJECT_NAME = 80
MAX_WEIGHT = 10
# Only a school name gives a test entry away; a subject can be called "Test".
TEST_WORDS = {"test", "tests", "testing", "asdf", "asdfg", "qwert", "qwertz", "qwerty", "lorem", "ipsum", "dummy", "foobar", "blabla"}

# The app's issue holds the proposal as a JSON block, and the student's own
# name for the semester in a list item above it.
RAW_JSON = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)
SEMESTER_NAME = re.compile(r"^- Their own semester name \(context only, not the template name\):[ \t]*(.*)$", re.M)
# Characters that show nothing, or show text in a different order than it's
# stored (bidi controls). Joiners and variation selectors stay: emoji need them.
INVISIBLE = {
    chr(c)
    for c in (
        0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180E, 0x200B, 0x200E, 0x200F, 0x3164, 0xFEFF, 0xFFA0,
        *range(0x202A, 0x202F), *range(0x2060, 0x2065), *range(0x2066, 0x206A),
    )
}
CONTACT = re.compile(
    r"https?:|www\.|://"  # links
    r"|\b[\w-]+\.(?:com|net|org|ch|de|fr|it|at|li|io|ly|gg|me|tv|app|xyz|info|co)\b"  # domains
    r"|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"  # email addresses
    r"|(?:\+|\b)[0-9](?:[ ./-]?[0-9]){8,}\b"  # phone numbers: 9 or more digits
    r"|<[A-Za-z/!]",  # HTML, but not "<3"
    re.I,
)
# Folder names never reach the apps, so they only use characters that every
# file system and git accept.
FOLDER_PUNCTUATION = set(" -,()&'.")
MAX_FOLDER_CHARACTERS = 80
MAX_FOLDER_BYTES = 200
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class Rejected(Exception):
    def __init__(self, reason, soft=False):
        super().__init__(reason)
        self.reason = reason
        self.soft = soft


def clean(text):
    """Trims, joins runs of spaces and line breaks into one space, and drops invisible
    characters. Text without anything visible becomes empty."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(" " if unicodedata.category(c) == "Cc" else c for c in text if c not in INVISIBLE)
    text = " ".join(text.split())
    return text if any(unicodedata.category(c)[0] in "LNPS" for c in text) else ""


def clean_weight(weight):
    """Writes "1.0" as "1", like the rest of the templates."""
    weight = "".join(weight.split())
    whole = re.fullmatch(r"([0-9]+)[.,]0+", weight)
    return whole.group(1) if whole else weight


def key(text):
    """Compares texts ignoring case, accents and spacing: "Zurich" matches "Zürich"."""
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).casefold().split())


def words(text):
    """The runs of letters, so "test4" contains "test"."""
    return re.findall(r"[^\W\d_]+", key(text))


def body_hash(body):
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


def load_blocked_words(path=WORDS_FILE):
    """(words, parts): a listed word, or a word containing a listed part ("fuck*")."""
    blocked, parts = set(), set()
    with open(path, encoding="utf-8") as file:
        for line in file:
            entry = key(line.split("#")[0].strip())
            if entry.endswith("*"):
                parts.add(entry[:-1])
            elif entry:
                blocked.add(entry)
    return blocked, parts


def load_approvals(path=APPROVALS_FILE):
    """Issue number -> hash of the issue's text when it was approved."""
    approvals = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            for line in file:
                fields = line.split("#")[0].split()
                if len(fields) == 2 and fields[0].isdigit():
                    approvals[int(fields[0])] = fields[1].lower()
    return approvals


def is_reserved(name):
    """Windows can't create these names, not even with an extension: CON, NUL.ch, COM¹."""
    return unicodedata.normalize("NFKC", name.split(".")[0]).strip().upper() in WINDOWS_RESERVED


def subjects_key(subjects):
    """What makes two templates the same semester: their subjects' names, weights and
    whether they count, in order."""
    return tuple((key(s["name"]), clean_weight(s["weight"]), s["counted"]) for s in subjects if clean(s["name"]))


def read_existing(templates_dir):
    """(folder, file name, template) for every template file that can be read."""
    existing = []
    for folder in sorted(os.listdir(templates_dir)):
        folder_path = os.path.join(templates_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for filename in sorted(os.listdir(folder_path)):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder_path, filename), encoding="utf-8") as file:
                    data = json.load(file)
            except (OSError, ValueError, UnicodeDecodeError, RecursionError):
                continue
            if isinstance(data, dict):
                existing.append((folder, filename, data))
    return existing


def soft_problem(school, texts, blocked_words):
    """Why the word list or the contact check rejects a template, or None. These checks
    can be wrong about a real school, so a maintainer can skip them."""
    blocked, parts = blocked_words
    if any(word in TEST_WORDS for word in words(school)) or any(
        word in blocked or any(part in word for part in parts) for text in texts for word in words(text)
    ):
        # Which word isn't said: the comment is public.
        return "it looks like a test, or contains a word that templates can't contain"
    if any(CONTACT.search(text) for text in texts):
        return "it contains a link, an email address, a phone number or HTML"
    return None


def read_proposal(body, blocked_words, approved):
    """The template a proposal's issue body describes, without an id. Raises Rejected."""
    match = RAW_JSON.search(body or "")
    try:
        raw = json.loads(match.group(1)) if match else None
    except (ValueError, RecursionError):
        raw = None
    if not isinstance(raw, dict):
        raise Rejected("its JSON block is missing or isn't valid JSON")
    school, year, subjects = raw.get("school"), raw.get("description", ""), raw.get("subjects")
    if not isinstance(school, str) or not isinstance(year, str) or not isinstance(subjects, list):
        raise Rejected("its JSON block doesn't have a school and a list of subjects")
    semester = SEMESTER_NAME.search(body)
    school, year, semester = clean(school), clean(year), clean(semester.group(1) if semester else "")
    if not school:
        raise Rejected("it has no school name")
    if max(len(school), len(year), len(semester)) > MAX_TEXT:
        raise Rejected(f"its school, year or semester name is longer than {MAX_TEXT} characters")

    named = []
    for number, subject in enumerate(subjects, start=1):
        if not (
            isinstance(subject, dict)
            and isinstance(subject.get("name"), str)
            and isinstance(subject.get("weight"), str)
            and isinstance(subject.get("counted"), bool)
        ):
            raise Rejected(f"its subject {number} doesn't have a name, a weight and whether it counts")
        name = clean(subject["name"])
        if not name:
            # Like the published file: a subject without a name is left out.
            continue
        if len(name) > MAX_SUBJECT_NAME:
            raise Rejected(f"a subject name is longer than {MAX_SUBJECT_NAME} characters")
        weight = clean_weight(subject["weight"])
        if len(weight) > MAX_WEIGHT or not weight_is_usable(weight):
            raise Rejected(f"its subject {number} has a weight the app can't use: weights must be more than 0, like 1, 0.5 or 1/2")
        named.append({"name": name, "weight": weight, "counted": subject["counted"]})
    if not named:
        raise Rejected("it has no subject with a name")
    if len(named) > MAX_SUBJECTS:
        raise Rejected(f"it has more than {MAX_SUBJECTS} subjects")

    if not approved:
        problem = soft_problem(school, [school, year, semester] + [subject["name"] for subject in named], blocked_words)
        if problem:
            raise Rejected(problem, soft=True)

    # The year tells a school's templates apart in the app's list; the
    # semester name is only kept where it adds something.
    name = year or semester
    description = semester if key(semester) not in (key(name), key(school)) else ""
    return {"name": name, "description": description, "school": school, "subjects": named}


class Templates:
    """The template files, as the import adds to them."""

    def __init__(self, directory):
        self.directory = directory
        # Every name in the directory, files too, ignoring case like macOS and Windows.
        self.taken = {name.casefold() for name in os.listdir(directory)}
        self.folders = sorted(name for name in os.listdir(directory) if os.path.isdir(os.path.join(directory, name)))
        self.files = collections.defaultdict(set)
        for folder in self.folders:
            self.files[folder] = {name.casefold() for name in os.listdir(os.path.join(directory, folder))}
        self.schools = collections.defaultdict(list)
        self.paths = {}
        self.semesters = {}
        for folder, filename, data in read_existing(directory):
            self.remember(folder, filename, data)

    def remember(self, folder, filename, data):
        path = f"{folder}/{filename}"
        self.files[folder].add(filename.casefold())
        if isinstance(data.get("id"), int):
            self.paths.setdefault(data["id"], path)
        if isinstance(data.get("school"), str):
            self.schools[folder].append(data["school"])
        try:
            semester = (key(data["school"]), subjects_key(data["subjects"]))
        except (KeyError, TypeError, AttributeError):
            return
        self.semesters.setdefault(semester, (data.get("id"), path))

    def folder_for(self, school):
        """An existing folder named like the school, or holding a template for a school
        named like it; otherwise a new folder named after the school."""
        wanted = key(school)
        for folder in self.folders:
            if key(folder) == wanted:
                return folder
        holding = collections.Counter(
            folder for folder, schools in self.schools.items() for name in schools if key(name) == wanted
        )
        if holding:
            return max(sorted(holding), key=lambda folder: holding[folder])
        name = unicodedata.normalize("NFKC", school)
        name = "".join(c for c in re.sub(r"[/\\_]", "-", name) if c.isalnum() or c in FOLDER_PUNCTUATION)
        name = " ".join(name.split())[:MAX_FOLDER_CHARACTERS]
        while len(name.encode("utf-8")) > MAX_FOLDER_BYTES:
            name = name[:-1]
        # Leading dots hide a folder; trailing ones and spaces break Windows.
        name = name.strip(" .-") or "School"
        if is_reserved(name):
            name = f"School {name}"
        for folder in self.folders:
            if key(folder) == key(name):
                return folder
        base = name
        for number in itertools.count(2):
            if name.casefold() not in self.taken:
                return name
            name = f"{base} {number}"

    def file_name_for(self, folder, name):
        """The template's name without spaces and punctuation, like the existing files,
        unique in its folder ignoring case."""
        base = "".join(c for c in unicodedata.normalize("NFKC", name) if c.isalnum())[:50] or "Template"
        for number in itertools.count(1):
            candidate = base if number == 1 else f"{base}-{number}"
            if not is_reserved(candidate) and f"{candidate}.json".casefold() not in self.files[folder]:
                return f"{candidate}.json"

    def add(self, folder, filename, data):
        os.makedirs(os.path.join(self.directory, folder), exist_ok=True)
        path = os.path.join(self.directory, folder, filename)
        # "x": never overwrite a file, whatever the checks above missed.
        file = open(path, "x", encoding="utf-8")
        try:
            with file:
                # One line, with \u escapes: the same format as every other template file.
                file.write(json.dumps(data))
        except BaseException:
            os.remove(path)
            raise
        if folder not in self.folders:
            self.folders.append(folder)
            self.taken.add(folder.casefold())
        self.remember(folder, filename, data)


def handle(issue, templates, blocked_words, approvals):
    number = issue["number"]
    template_id = ID_OFFSET + number
    if template_id in templates.paths:
        # Added by an earlier run whose answer didn't go through.
        return {"action": "added", "id": template_id, "path": templates.paths[template_id], "written": False}
    body = issue.get("body") or ""
    try:
        template = read_proposal(body, blocked_words, approvals.get(number) == body_hash(body))
    except Rejected as rejected:
        result = {"action": "rejected", "reason": rejected.reason, "soft": rejected.soft}
        if rejected.soft:
            result["approval"] = f"{number} {body_hash(body)}"
        return result
    semester = (key(template["school"]), subjects_key(template["subjects"]))
    if semester in templates.semesters:
        existing_id, path = templates.semesters[semester]
        return {"action": "duplicate", "id": existing_id, "path": path}
    if template_id > MAX_ID:
        return {"action": "rejected", "reason": "its issue number is too high for a template id", "soft": False}

    folder = templates.folder_for(template["school"])
    filename = templates.file_name_for(folder, template["name"])
    data = {
        "id": template_id,
        "name": template["name"],
        "description": template["description"],
        "school": template["school"],
        "subject_count": len(template["subjects"]),
        "subjects": template["subjects"],
    }
    report = Report()
    if check_template(f"{folder}/{filename}", data, report) is None or report.errors:
        # Text that isn't valid Unicode, like half an emoji, gets this far. It
        # would fail the check of all templates, and with it the whole run.
        return {"action": "rejected", "reason": "it doesn't pass the template check", "soft": False}
    templates.add(folder, filename, data)
    return {"action": "added", "id": template_id, "path": f"{folder}/{filename}", "written": True}


def import_proposals(issues, templates_dir=TEMPLATES_DIR, blocked_words=None, approvals=None):
    """Writes a template file for each proposal that passes, oldest first, and returns
    what happened to each one handled, and how many wait for the next run."""
    if blocked_words is None:
        blocked_words = load_blocked_words()
    if approvals is None:
        approvals = load_approvals()
    templates = Templates(templates_dir)
    proposals = sorted((issue for issue in issues if issue["number"] >= FIRST_ISSUE), key=lambda issue: issue["number"])
    results = []
    added = 0
    for issue in proposals:
        if len(results) == MAX_HANDLED_PER_RUN or added == MAX_ADDED_PER_RUN:
            break
        try:
            result = handle(issue, templates, blocked_words, approvals)
        except Exception as error:
            # A proposal crafted to break the import only rejects itself. The
            # message isn't printed: it could hold the proposal's text.
            print(f"#{issue['number']} couldn't be processed: {type(error).__name__}", file=sys.stderr)
            result = {"action": "rejected", "reason": "it couldn't be processed", "soft": False}
        labels = [label["name"] if isinstance(label, dict) else label for label in issue.get("labels", [])]
        results.append({"number": issue["number"], "labels": labels, **result})
        added += bool(result.get("written"))
    return results, len(proposals) - len(results)


def summary(results, waiting):
    lines = ["### Template proposals", ""]
    if not results:
        lines.append("No open proposals.")
    else:
        lines += ["| Proposal | Result |", "|---|---|"]
        for result in results:
            if result["action"] == "added":
                outcome = f"Added as template {result['id']}: `{result['path']}`"
            elif result["action"] == "duplicate":
                outcome = f"Already template {result['id']}: `{result['path']}`"
            else:
                outcome = f"Not added: {result['reason']}"
            lines.append(f"| #{result['number']} | {outcome} |")
    if waiting:
        lines += ["", f"{waiting} more proposal(s) wait for the next run."]
    return "\n".join(lines)


def comment_for(result):
    """The answer on the issue. It only holds fixed text, ids and paths, never text
    from the proposal as such: an "@name" in it would notify someone."""
    if result["action"] == "added":
        return (
            f"Thank you! This proposal is now template {result['id']}: `pluspoints/templates/{result['path']}`.\n\n"
            "Students get it within a day, the next time their app checks for new templates."
        )
    if result["action"] == "duplicate":
        return f"Thank you! This semester is already template {result['id']} (`pluspoints/templates/{result['path']}`), so nothing new was added."
    if result["soft"]:
        retry = (
            "If that's wrong, a maintainer can add this line to `scripts/approved_proposals.txt` on `main` and "
            "reopen this issue. The next daily import then adds it without this check, unless the issue was "
            f"edited in the meantime:\n\n```\n{result['approval']}\n```"
        )
    else:
        retry = "If that's wrong, a maintainer can add the template by hand."
    return f"This proposal wasn't added as a template, because {result['reason']}.\n\n{retry}"


def answer(results, repo, run=subprocess.run, pause=time.sleep):
    """Comments on each proposal and closes it."""
    def gh(*args):
        run(["gh", *args, "--repo", repo], check=True)

    if results:
        for name, color, description in LABELS:
            gh("label", "create", name, "--color", color, "--description", description, "--force")
    for result in results:
        number = str(result["number"])
        added = result["action"] == "added"
        gh("issue", "comment", number, "--body", comment_for(result))
        edit = ["issue", "edit", number, "--add-label", ADDED_LABEL if added else REJECTED_LABEL]
        if added and REJECTED_LABEL in result["labels"]:
            # Approved after an earlier rejection.
            edit += ["--remove-label", REJECTED_LABEL]
        gh(*edit)
        gh("issue", "close", number, "--reason", "completed" if added else "not planned")
        # GitHub allows about 80 comments, edits and closes a minute.
        pause(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    import_command = commands.add_parser("import", help="write the templates and the results file")
    import_command.add_argument("--proposals", required=True, help="the open proposals, from gh issue list")
    import_command.add_argument("--results", required=True, help="where to write what happened to each proposal")
    answer_command = commands.add_parser("answer", help="comment on and close the proposals in the results file")
    answer_command.add_argument("--results", required=True)
    args = parser.parse_args()

    if args.command == "import":
        with open(args.proposals, encoding="utf-8") as file:
            issues = json.load(file)
        results, waiting = import_proposals(issues)
        with open(args.results, "w", encoding="utf-8") as file:
            json.dump(results, file, ensure_ascii=False, indent=2)
        print(summary(results, waiting))
    else:
        with open(args.results, encoding="utf-8") as file:
            results = json.load(file)
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not repo:
            sys.exit("GITHUB_REPOSITORY must name the repository whose issues to answer")
        answer(results, repo)


if __name__ == "__main__":
    main()
