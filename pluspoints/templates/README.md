# Templates

A default set of semester templates. They are curated from the list of most downloaded
templates from the old pluspoints web backend, plus the templates students propose from
the app (each proposal arrives as an issue with the `template-proposal` label).

## Contribute

Optimally each school provides official templates or a person is responsible for managing that 
schools templates. 

### Create a new template

Put each template into its school's folder, as a file named after the template's `id`, like
`kantonsschule-zurich-nord/6026.json`. A folder's name is the school's name in lowercase ASCII
letters and digits joined by `-` (`Kantonsschule Zürich Nord` becomes `kantonsschule-zurich-nord`);
if the school has no folder yet, create one. The school's name as students know it goes into the
file's `school` field: names of folders and files never reach the apps. It is recommended to copy an
existing template and adjust the values in the fields.

Give the template the next free `id` below 10000 (the highest such `id` plus one); the ids from
10000 on belong to the proposals that the daily import adds (see below). The apps tell
templates apart by their `id`, so it must be unique and must never be reused for a different
template. Ids 5878 to 5999 are reserved for the German templates of the Android app's upcoming
2.3.0 version.

A template file has these fields:

| Field | Content |
|---|---|
| `id` | A whole number, unique across all templates |
| `name` | Shown under the school name in the app's list |
| `description` | Shown on the template's page; can be empty |
| `school` | The school name, the way students search for it |
| `country` | Only for a school outside Switzerland: its country's two-letter code, such as `DE`. The Android app before 2.3.0 doesn't show these templates |
| `subject_count` | The number of subjects |
| `subjects` | The subjects, each with a `name`, a `weight` (more than 0, such as `1`, `0.5` or `1/2`) and `counted` (`true` or `false`) |

Any other field of a template (not of a subject) is published as it is; apps ignore fields they
don't know.

### Check your change

Pull requests that change a template are checked automatically. To check locally, run

```shell
python3 scripts/build_templates_bundle.py
```

It reports errors (for example a duplicate `id`, a file not named after its `id`, or a weight the
apps can't calculate with), which
must be fixed, and warnings, which don't block publishing.

## How proposals become templates

Students propose templates from the Android app, and each proposal arrives as an issue with the
`template-proposal` label. Once a day, the
[Import proposals workflow](../../.github/workflows/import-proposals.yml) turns them into templates
without anyone reviewing them first. `scripts/import_proposals.py` holds the rules:

- The school, the subjects and their weights stay as the student typed them. Only spaces are
  tidied up, invisible characters are removed, a weight like `1.0` is written `1`, and subjects
  without a name are left out.
- The template's `name` is the school year the student typed, or their own name for the semester
  if the year is empty. Its `description` is that semester name, unless it only repeats the name or
  the school.
- Its `id` is 10000 plus the number of its issue, and its file is named after it, like
  `10041.json`.
- It goes into the school's folder, named by the rule above (`Sek leonard` goes into
  `sek-leonard`), or into the folder that holds most templates for a school with that folder name.
  Otherwise the import creates that folder.

A proposal is not added, and a comment on its issue says why, if:

- it can't be read, has no subject with a name, has more than 40 subjects, or has text longer than
  100 characters (80 for a subject);
- a weight isn't one the apps can use;
- an existing template has the same school and the same subjects, in the same order;
- its school name looks like a test entry ("test", "asdf"), or it contains a word from
  `scripts/proposal_blocked_words.txt` (insults);
- it contains a link, an email address, a phone number or HTML.

The token that the app files proposals with ships inside the app, so anyone could file one, and set
any label on it. Approvals therefore live in this repository: if the last two checks are wrong
about a proposal, add the line that the comment on its issue shows to
`scripts/approved_proposals.txt` on `main` and reopen the issue. The next run adds it without those
two checks, unless the issue was edited since.

The import closes added proposals with the `template-added` label and the others with
`template-rejected`. A run adds at most 20 proposals and answers at most 50; the rest wait for the
next day. The proposals up to #38 were added by hand, so it leaves those alone.

Each run that adds templates is one commit on `main` ("Add the templates proposed in #41, #42"). To
undo one, revert its commit: the next published file leaves those templates out, and the apps drop
them within a day. To keep a proposal out of the import, remove its `template-proposal` label. To
stop the import, disable the workflow in the Actions tab. A manual run of the workflow is a dry run
unless you uncheck that: its summary shows what it would add. GitHub also disables the schedule
after 60 days without any activity in the repository; it can be enabled again in the same place.

The import reads the JSON block of the issue (`school`, `description` with the year, `subjects`)
and the line with the student's own name for the semester, so an app that files proposals must
keep that format.

## How the apps get the templates

On every change to `main`, the [Templates workflow](../../.github/workflows/templates.yml) combines
all templates into one file and publishes it on the website:
https://www.fidelisfactory.ch/pluspoints/templates.json

The Android app ships a copy of that file and checks for a newer one when a student opens the
template browser, at most once a day. So a merged template reaches students without an app
release. (The iOS app doesn't use this file.) If no template changed, the published file stays
byte-identical and the app downloads nothing new.

The file looks like this:

```json
{"schemaVersion": 1, "generatedAt": "2026-10-17T19:32:01Z", "templates": [{"id": 4063, "name": "...", "description": "...", "school": "...", "subjects": [{"name": "...", "weight": "1", "counted": true}]}]}
```

- `generatedAt`, in UTC and written like `2026-10-17T19:32:01Z`, tells the app which of two copies
  is newer.
- Text is trimmed, subjects without a name are left out, and so are templates without any named
  subject. `subject_count` is left out because it only repeats the number of subjects.
- Templates are sorted by their `school` and `name`.

Apps that are already installed keep reading this file, so changes to its format must stay
compatible: within `schemaVersion` 1, only add fields. A change that older apps can't read needs
a new file name, published next to this one for as long as older apps are in use.
