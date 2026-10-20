#!/usr/bin/env python3
"""Checks every semester template and combines them into the file the apps download.

    python3 scripts/build_templates_bundle.py                 # only check the templates
    python3 scripts/build_templates_bundle.py --output FILE   # check them and write the file

The apps download the file from https://www.fidelisfactory.ch/pluspoints/templates.json;
pluspoints/templates/README.md describes its format. With --previous FILE, the new file
keeps FILE's generatedAt when no template changed, so publishing an unchanged set of
templates produces a byte-identical file and the apps download nothing new.

Exits with 1 if a template has an error. Warnings (such as a subject without a name,
which the file leaves out) don't fail the check.
"""
import argparse
import datetime
import json
import os
import re
import sys
import unicodedata

SCHEMA_VERSION = 1
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
TEMPLATES_DIR = os.path.join(ROOT, "pluspoints", "templates")
# The fields the apps read. Any other field in a template file goes into the
# published file as it is (apps ignore fields they don't know), except
# subject_count, which only repeats the number of subjects.
TEXT_FIELDS = ("name", "description", "school")
LEFT_OUT_FIELDS = ("subject_count",)
# The weights the Android app can save and calculate with: 1, 0.5, 0,5 or 1/2.
# Its subject form only accepts these (no "50%", although the core reads it),
# and the core crashes on a weight of "0".
WEIGHT = re.compile(r"([0-9]+)(?:[.,]([0-9]+))?|([0-9]+)/([0-9]+)")
# The apps store ids as 32-bit numbers.
MAX_ID = 2**31 - 1
# Names Windows can't give a file or folder.
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
# A template's optional country, as an ISO 3166 code like "DE". Without one, a
# template is Swiss; the Android app before 2.3.0 shows only Swiss templates.
COUNTRY = re.compile(r"[A-Z]{2}")


def weight_is_usable(weight):
    match = WEIGHT.fullmatch(weight.strip())
    if not match:
        return False
    if match.group(3) is not None:
        return int(match.group(3)) > 0 and int(match.group(4)) > 0
    return float(f"{match.group(1)}.{match.group(2) or 0}") > 0


def escape_annotation(text, is_property=False):
    """Escapes text for a GitHub workflow command, the way @actions/core does."""
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if is_property:
        text = text.replace(":", "%3A").replace(",", "%2C")
    return text


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, path, message):
        self.errors.append((path, message))

    def warning(self, path, message):
        self.warnings.append((path, message))

    def print(self):
        in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
        for kind, entries in (("error", self.errors), ("warning", self.warnings)):
            for path, message in entries:
                if in_actions:
                    print(f"::{kind} file={escape_annotation(path, True)}::{escape_annotation(f'{path}: {message}')}")
                else:
                    print(f"{kind}: {path}: {message}")


def natural_key(text):
    """Sorts case- and accent-insensitively, and numbers by value: "9. Klasse" before "12. Klasse"."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()
    parts = re.split(r"([0-9]+)", plain)
    return [int(part) if i % 2 else part for i, part in enumerate(parts)]


def check_template(path, data, report):
    """Returns the template as it goes into the file, or None if it can't go in."""
    if not isinstance(data, dict):
        report.error(path, "the file must contain one JSON object")
        return None
    problems = []
    template_id = data.get("id")
    if not isinstance(template_id, int) or isinstance(template_id, bool) or not 0 < template_id <= MAX_ID:
        problems.append(f"id must be a whole number from 1 to {MAX_ID}")
    for field in TEXT_FIELDS:
        if not isinstance(data.get(field), str):
            problems.append(f"{field} must be text")
    if isinstance(data.get("school"), str) and not data["school"].strip():
        problems.append("school must not be empty")
    if "country" in data and not (isinstance(data["country"], str) and COUNTRY.fullmatch(data["country"])):
        problems.append(f"country must be a two-letter code like \"DE\", not {data['country']!r}")
    subjects = data.get("subjects")
    if not isinstance(subjects, list):
        problems.append("subjects must be a list")
        subjects = []
    for number, subject in enumerate(subjects, start=1):
        if not isinstance(subject, dict):
            problems.append(f"subject {number} must be a JSON object")
            continue
        if not isinstance(subject.get("name"), str):
            problems.append(f"subject {number}: name must be text")
        weight = subject.get("weight")
        if not isinstance(weight, str) or not weight_is_usable(weight):
            problems.append(f"subject {number}: weight must be more than 0 and look like 1, 0.5 or 1/2, not {weight!r}")
        if not isinstance(subject.get("counted"), bool):
            problems.append(f"subject {number}: counted must be true or false")
    for problem in problems:
        report.error(path, problem)
    if problems:
        return None

    subject_count = data.get("subject_count")
    if subject_count is not None and subject_count != len(subjects):
        report.warning(path, f"subject_count is {subject_count}, but the template has {len(subjects)} subjects")
    named = [
        {"name": s["name"].strip(), "weight": s["weight"].strip(), "counted": s["counted"]}
        for s in subjects
        if s["name"].strip()
    ]
    if not named:
        report.warning(path, "no subject has a name, so the template is left out")
        return None
    if len(named) < len(subjects):
        report.warning(path, f"{len(subjects) - len(named)} subject(s) without a name are left out")

    template = {"id": template_id}
    for field in TEXT_FIELDS:
        text = data[field]
        # A line break written out as the two characters "\n" (some old
        # templates had them), which the apps would show as such.
        if "\\n" in text:
            report.warning(path, f"{field} contains \"\\n\" written out; it's published as a line break")
            text = text.replace("\\n", "\n")
        template[field] = text.strip()
    template["subjects"] = named
    for field, value in data.items():
        if field not in template and field not in LEFT_OUT_FIELDS:
            template[field] = value
    try:
        json.dumps(template, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate such as "\ud83e": JSON allows it, UTF-8 can't hold it.
        report.error(path, "contains text that isn't valid Unicode, such as half of an emoji")
        return None
    return template


def folder_name(school):
    """The folder for a school's templates: its name in lowercase ASCII letters and digits
    joined by "-", like kantonsschule-zurich-nord. No folder name holds what a student
    typed as such, and every system and git accept these names."""
    text = unicodedata.normalize("NFKD", school)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    name = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:80].strip("-") or "school"
    return f"{name}-school" if name.upper() in WINDOWS_RESERVED else name


def collect_templates(report):
    entries = []
    first_file_with_id = {}
    for folder in sorted(os.listdir(TEMPLATES_DIR)):
        folder_path = os.path.join(TEMPLATES_DIR, folder)
        # Hidden files such as .DS_Store aren't the repository's.
        if folder == "README.md" or folder.startswith("."):
            continue
        relative = os.path.relpath(folder_path, ROOT)
        if not os.path.isdir(folder_path):
            report.error(relative, "templates go into a school folder")
            continue
        if folder != folder_name(folder):
            report.error(relative, f"folder names are lowercase ASCII words joined by \"-\", like {folder_name(folder)}")
            continue
        for filename in sorted(os.listdir(folder_path)):
            path = os.path.relpath(os.path.join(folder_path, filename), ROOT)
            if filename.startswith(".") and not filename.endswith(".json"):
                continue
            if not filename.endswith(".json") or os.path.isdir(os.path.join(folder_path, filename)):
                report.error(path, "only template files belong in a school folder")
                continue
            try:
                with open(os.path.join(folder_path, filename), encoding="utf-8") as file:
                    data = json.load(file)
            except (ValueError, UnicodeDecodeError) as error:
                report.error(path, f"not valid JSON: {error}")
                continue
            template = check_template(path, data, report)
            if template is None:
                continue
            if filename != f"{template['id']}.json":
                report.error(path, f"the file must be named after the template's id: {template['id']}.json")
                continue
            if template["id"] in first_file_with_id:
                report.error(path, f"id {template['id']} is already used by {first_file_with_id[template['id']]}")
                continue
            first_file_with_id[template["id"]] = path
            # By the school students see, not by folder.
            entries.append((natural_key(template["school"]), natural_key(template["name"]), template["id"], template))
    entries.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in entries]


def previous_generated_at(path, templates):
    """generatedAt of the file at path, if it holds exactly these templates."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as file:
            previous = json.load(file)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(previous, dict) and previous.get("schemaVersion") == SCHEMA_VERSION and previous.get("templates") == templates:
        return previous.get("generatedAt")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", help="where to write the combined file")
    parser.add_argument("--previous", help="the currently published file, to keep its generatedAt if nothing changed")
    args = parser.parse_args()

    report = Report()
    templates = collect_templates(report)
    report.print()
    if report.errors:
        print(f"{len(report.errors)} error(s): fix them before the templates can be published.")
        return 1

    generated_at = previous_generated_at(args.previous, templates) or (
        datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    print(f"{len(templates)} templates, {len(report.warnings)} warning(s).")
    if args.output:
        bundle = {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "templates": templates}
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as file:
            json.dump(bundle, file, ensure_ascii=False, separators=(",", ":"))
            file.write("\n")
        print(f"Wrote {args.output} (generatedAt {generated_at}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
