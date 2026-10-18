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
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys
import unicodedata

from build_templates_bundle import MAX_ID, TEMPLATES_DIR, Report, check_template, weight_is_usable

ADDED_LABEL = "template-added"
REJECTED_LABEL = "template-rejected"
# A maintainer adds it to a reopened proposal to skip the soft checks (the
# word list and contact details), which can be wrong about a real school.
APPROVED_LABEL = "template-approved"
LABELS = (
    (ADDED_LABEL, "0e8a16", "The daily import added this proposal as a template"),
    (REJECTED_LABEL, "b60205", "The daily import didn't add this proposal; its comment says why"),
    (APPROVED_LABEL, "1d76db", "Lets the daily import add this proposal despite the word list and contact checks"),
)
# A flood of proposals waits for the following days instead of all going live at once.
MAX_PER_RUN = 20
MAX_SUBJECTS = 40
MAX_TEXT = 100
MAX_SUBJECT_NAME = 80
# Ids 5878 to 5999 are reserved for the German templates (see the README).
FIRST_ID = 6000
WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proposal_blocked_words.txt")

# The app's issue holds the proposal as a JSON block, and the student's own
# name for the semester in a list item above it.
RAW_JSON = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)
SEMESTER_NAME = re.compile(r"^- Their own semester name \(context only, not the template name\):[ \t]*(.*)$", re.M)
# Invisible characters that only cause trouble: bidi controls can show text in
# a different order than it's stored. Joiners stay, emoji are built with them.
INVISIBLE = {chr(c) for c in (0x200B, 0x200E, 0x200F, 0xFEFF, *range(0x202A, 0x202F), *range(0x2066, 0x206A))}
CONTACT = re.compile(
    r"https?:|www\.|://"  # links
    r"|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"  # email addresses
    r"|(?:\+|\b)[0-9](?:[ .-]?[0-9]){8,}\b"  # phone numbers: 9 or more digits
    r"|<[A-Za-z/!]",  # HTML, but not "<3"
    re.I,
)
# Characters that some systems don't allow in file names; ` would break the
# file name in the issue comment's code span.
UNSAFE_IN_FOLDER = re.compile(r'[<>:"/\\|?*`]')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class Rejected(Exception):
    def __init__(self, reason, soft=False):
        super().__init__(reason)
        self.reason = reason
        self.soft = soft


def clean(text):
    """Trims, joins runs of spaces and line breaks into one space, and drops invisible characters."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(" " if unicodedata.category(c) == "Cc" else c for c in text if c not in INVISIBLE)
    return " ".join(text.split())


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


def load_blocked_words(path=WORDS_FILE):
    with open(path, encoding="utf-8") as file:
        return {key(line) for line in (line.split("#")[0].strip() for line in file) if line}


def subjects_key(subjects):
    """What makes two templates the same semester: their subjects' names, weights and whether they count."""
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
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(data, dict):
                existing.append((folder, filename, data))
    return existing


def read_proposal(body, blocked_words, approved):
    """The template a proposal's issue body describes, without an id. Raises Rejected."""
    match = RAW_JSON.search(body or "")
    try:
        raw = json.loads(match.group(1)) if match else None
    except ValueError:
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
        if not weight_is_usable(weight):
            # Nothing the student typed goes into the public comment: an "@name" would notify someone.
            raise Rejected(f"its subject {number} has a weight the app can't use: weights must be more than 0, like 1, 0.5 or 1/2")
        named.append({"name": name, "weight": weight, "counted": subject["counted"]})
    if not named:
        raise Rejected("it has no subject with a name")
    if len(named) > MAX_SUBJECTS:
        raise Rejected(f"it has more than {MAX_SUBJECTS} subjects")

    texts = [school, year, semester] + [subject["name"] for subject in named]
    if not approved:
        if any(word in blocked_words for text in texts for word in words(text)):
            # The word itself isn't repeated: the comment is public.
            raise Rejected("it looks like a test, or contains a word that templates can't contain", soft=True)
        if any(CONTACT.search(text) for text in texts):
            raise Rejected("it contains a link, an email address, a phone number or HTML", soft=True)

    # The year tells a school's templates apart in the app's list; the
    # semester name is only kept where it adds something.
    name = year or semester
    description = semester if key(semester) not in (key(name), key(school)) else ""
    return {"name": name, "description": description, "school": school, "subjects": named}


def folder_for(school, folders, schools_by_folder):
    """An existing folder named like the school, or holding a template for a school named
    like it; otherwise a new folder named after the school as the student typed it."""
    wanted = key(school)
    for folder in folders:
        if key(folder) == wanted:
            return folder
    holding = collections.Counter(
        folder for folder, schools in schools_by_folder.items() for name in schools if key(name) == wanted
    )
    if holding:
        return max(sorted(holding), key=lambda folder: holding[folder])
    new = UNSAFE_IN_FOLDER.sub("-", school).strip(" .")[:80].strip(" .") or "School"
    if new.split(".")[0].upper() in WINDOWS_RESERVED:
        new += " school"
    for folder in folders:
        if key(folder) == key(new):
            return folder
    return new


def file_name_for(name, taken):
    """The template's name without spaces and punctuation, like the existing files, and
    unique in its folder ignoring case, for macOS and Windows checkouts."""
    base = "".join(c for c in unicodedata.normalize("NFKC", name) if c.isalnum())[:50] or "Template"
    candidate, number = base, 1
    while f"{candidate}.json".casefold() in taken:
        number += 1
        candidate = f"{base}-{number}"
    return f"{candidate}.json"


def import_proposals(issues, templates_dir=TEMPLATES_DIR, blocked_words=None, limit=MAX_PER_RUN):
    """Writes a template file for each proposal that passes, and returns what happened to
    each one (the oldest proposals first, at most limit of them)."""
    if blocked_words is None:
        blocked_words = load_blocked_words()
    existing = read_existing(templates_dir)
    ids = [data["id"] for _, _, data in existing if isinstance(data.get("id"), int)]
    next_id = max([FIRST_ID - 1, *ids]) + 1
    folders = sorted({folder for folder in os.listdir(templates_dir) if os.path.isdir(os.path.join(templates_dir, folder))})
    schools_by_folder = collections.defaultdict(list)
    files_by_folder = collections.defaultdict(set)
    known = {}
    for folder, filename, data in existing:
        files_by_folder[folder].add(filename.casefold())
        if isinstance(data.get("school"), str):
            schools_by_folder[folder].append(data["school"])
        try:
            same = (key(data["school"]), subjects_key(data["subjects"]))
        except (KeyError, TypeError, AttributeError):
            continue
        known.setdefault(same, (data.get("id"), f"{folder}/{filename}"))

    results = []
    for issue in sorted(issues, key=lambda issue: issue["number"])[:limit]:
        labels = {label["name"] if isinstance(label, dict) else label for label in issue.get("labels", [])}
        result = {"number": issue["number"]}
        try:
            template = read_proposal(issue.get("body"), blocked_words, APPROVED_LABEL in labels)
        except Rejected as rejected:
            results.append({**result, "action": "rejected", "reason": rejected.reason, "soft": rejected.soft})
            continue
        same = (key(template["school"]), subjects_key(template["subjects"]))
        if same in known:
            template_id, path = known[same]
            results.append({**result, "action": "duplicate", "id": template_id, "path": path})
            continue
        if next_id > MAX_ID:
            results.append({**result, "action": "rejected", "reason": "there is no free template id left", "soft": False})
            continue

        folder = folder_for(template["school"], folders, schools_by_folder)
        filename = file_name_for(template["name"], files_by_folder[folder])
        data = {
            "id": next_id,
            "name": template["name"],
            "description": template["description"],
            "school": template["school"],
            "subject_count": len(template["subjects"]),
            "subjects": template["subjects"],
        }
        report = Report()
        if check_template(f"{folder}/{filename}", data, report) is None or report.errors:
            # Can't happen with the checks above; it would fail the whole run otherwise.
            results.append({**result, "action": "rejected", "reason": "it doesn't pass the template check", "soft": False})
            continue
        os.makedirs(os.path.join(templates_dir, folder), exist_ok=True)
        # "x": never overwrite a file, whatever the checks above missed.
        with open(os.path.join(templates_dir, folder, filename), "x", encoding="utf-8") as file:
            # One line, with \u escapes: the same format as every other template file.
            file.write(json.dumps(data))
        if folder not in folders:
            folders.append(folder)
        files_by_folder[folder].add(filename.casefold())
        schools_by_folder[folder].append(template["school"])
        known[same] = (next_id, f"{folder}/{filename}")
        results.append({**result, "action": "added", "id": next_id, "path": f"{folder}/{filename}"})
        next_id += 1
    return results


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
    if result["action"] == "added":
        return (
            f"Thank you! This proposal is now template {result['id']}: `pluspoints/templates/{result['path']}`.\n\n"
            "Students get it within a day, the next time their app checks for new templates."
        )
    if result["action"] == "duplicate":
        return f"Thank you! This semester is already template {result['id']} (`pluspoints/templates/{result['path']}`), so nothing new was added."
    if result["soft"]:
        retry = (
            f"If that's wrong, a maintainer can reopen this issue and add the `{APPROVED_LABEL}` label. "
            "The next daily import then adds it without this check."
        )
    else:
        retry = "If that's wrong, a maintainer can add the template by hand."
    return f"This proposal wasn't added as a template, because {result['reason']}.\n\n{retry}"


def answer(results, repo, run=subprocess.run):
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
        gh("issue", "edit", number, "--add-label", ADDED_LABEL if added else REJECTED_LABEL)
        gh("issue", "close", number, "--reason", "completed" if added else "not planned")


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
        results = import_proposals(issues)
        with open(args.results, "w", encoding="utf-8") as file:
            json.dump(results, file, ensure_ascii=False, indent=2)
        print(summary(results, max(0, len(issues) - len(results))))
    else:
        with open(args.results, encoding="utf-8") as file:
            results = json.load(file)
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not repo:
            sys.exit("GITHUB_REPOSITORY must name the repository whose issues to answer")
        answer(results, repo)


if __name__ == "__main__":
    main()
