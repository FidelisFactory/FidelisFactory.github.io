# Templates

A default set of semester templates. They are curated from the list of most downloaded
templates from the old pluspoints web backend, plus the templates students propose from
the app (each proposal arrives as an issue with the `template-proposal` label).

## Contribute

Optimally each school provides official templates or a person is responsible for managing that 
schools templates. 

### Create a new template

Add a file with the content to the respective school folder. If the school folder does not exist, 
create it. It is recommended to copy an existing template and adjust the values in the fields.

Give the template the next free `id` (the highest `id` of all templates plus one). The apps tell
templates apart by their `id`, so it must be unique and must never be reused for a different
template. Ids 5878 to 5999 are reserved for the German templates that the Android app currently
bundles itself.

A template file has these fields:

| Field | Content |
|---|---|
| `id` | A whole number, unique across all templates |
| `name` | Shown under the school name in the app's list |
| `description` | Shown on the template's page; can be empty |
| `school` | The school name, the way students search for it |
| `subject_count` | The number of subjects |
| `subjects` | The subjects, each with a `name`, a `weight` (such as `1`, `0.5`, `1/2` or `50%`) and `counted` (`true` or `false`) |

Any other field is published as it is; apps ignore fields they don't know.

### Check your change

Pull requests that change a template are checked automatically. To check locally, run

```shell
python3 scripts/build_templates_bundle.py
```

It reports errors (for example a duplicate `id` or a weight the apps can't calculate with), which
must be fixed, and warnings, which don't block publishing.

## How the apps get the templates

On every change to `main`, the [Templates workflow](../../.github/workflows/templates.yml) combines
all templates into one file and publishes it on the website:
https://www.fidelisfactory.ch/pluspoints/templates.json

The apps ship a copy of that file and check for a newer one at most once a day, so a merged
template reaches students within about a day, without an app release. If no template changed,
the published file stays byte-identical and the apps download nothing new.

The file looks like this:

```json
{"schemaVersion": 1, "generatedAt": "2026-10-17T19:32:01Z", "templates": [{"id": 4063, "name": "...", "description": "...", "school": "...", "subjects": [{"name": "...", "weight": "1", "counted": true}]}]}
```

- `generatedAt` tells the apps which of two copies is newer.
- Text is trimmed, subjects without a name are left out, and so are templates without any named
  subject. `subject_count` is left out because it only repeats the number of subjects.
- Templates are sorted by school folder and name.

Apps that are already installed keep reading this file, so changes to its format must stay
compatible: within `schemaVersion` 1, only add fields. A change that older apps can't read needs
a new file name, published next to this one for as long as older apps are in use.
