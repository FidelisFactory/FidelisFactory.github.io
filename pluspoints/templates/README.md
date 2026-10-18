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

Give the template the next free `id` (the highest `id` of all templates plus one). The apps tell
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
