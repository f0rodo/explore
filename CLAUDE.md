# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this repository is

`github/explore` is a **content repository**, not an application. It holds the community-curated
data behind [GitHub Topics](https://github.com/topics) and
[GitHub Collections](https://github.com/collections). GitHub periodically pulls this content into
the product; nothing here is built, served, or deployed from this repo.

The only code in the repo is a Ruby/minitest lint suite that validates the content. Almost every
change is an edit to a Markdown file or an image, and the real work is making that content conform
to the rules the tests enforce.

## Layout

```
topics/<topic-slug>/          677 topic pages
  index.md                    YAML front matter + Markdown body (required)
  <topic-slug>.png            optional logo, must be named after the slug
collections/<collection-slug>/ 70 collections
  index.md                    YAML front matter (incl. `items` list) + body (required)
  <collection-slug>.png       optional image, must be named after the slug
docs/API.md                   field-by-field reference for index.md front matter
docs/styleguide.md            prose/voice rules (also partly enforced by tests)
test/                         minitest lint suite (the source of truth for validation)
script/setup                  bundle install (+ Homebrew/rbenv setup on macOS)
script/cibuild                setup, then `rake test` and `rubocop`
topics-todo.md                checklist of popular topics still lacking a curated page
.github/workflows/            CI (pull_request.yml) and stale-PR automation
```

Directory names are the URL slugs: `topics/rails/` ⇒ `https://github.com/topics/rails`,
`collections/music/` ⇒ `https://github.com/collections/music`.

**A topic or collection directory may contain only `index.md` and at most one image.** The test
suite fails on any other file or subdirectory.

## Running the checks

```bash
script/cibuild                                     # what CI runs: setup + rake test + rubocop
bundle exec rake test                              # full suite (topics + collections)
bundle exec ruby -Itest test/topics_test.rb        # topics only (~35s, no network)
bundle exec ruby -Itest test/collections_test.rb   # collections only (hits the GitHub API)
bundle exec rubocop --display-cop-names            # lints test/ and Rakefile only
```

Practical notes for this environment:

- **Set a UTF-8 locale.** Many `index.md` files contain non-ASCII characters, and with the default
  `US-ASCII` external encoding `test_helper.rb`'s `File.read(...).split("---", 3)` raises
  `ArgumentError: invalid byte sequence`. Run tests as
  `LANG=C.UTF-8 bundle exec ruby -Itest test/topics_test.rb`. Verified: the topics suite is green
  (16,900 runs, 0 failures) with the locale set, and produces ~1,800 spurious errors without it.
- **`bundle exec rubocop` may fail with `command not found: rubocop`** (old bundler 2.1.4 against
  the installed Ruby). The gem is present — invoke it directly:
  `ruby "$(bundle info rubocop --path)/exe/rubocop" --display-cop-names`. It currently reports no
  offenses across the 8 Ruby files.
- **The collections suite makes live GitHub API calls** for every repository and user in every
  `items` list, to verify they still exist and have not been renamed. Set `GITHUB_TOKEN` to avoid
  being rate limited. On `Octokit::TooManyRequests` the custom client in `test/test_helper.rb`
  silently treats the item as valid and prints a warning at the end of the run, so a green
  collections run without a token does not prove much.
- Ruby version is pinned to 3.1.0 in `.ruby-version`; RuboCop targets Ruby 2.7. CI uses
  `ruby/setup-ruby` with `bundler-cache: true`.

Because the suite iterates every topic and collection, a run touches all content — a failure
message names the offending topic/collection in the test description
(e.g. `topics::react topic#test_0012_has a valid short_description`).

## `topics/<slug>/index.md`

Front matter keys, alphabetized by convention. Only these keys are permitted
(`VALID_TOPIC_METADATA_KEYS` in `test/topics_test_helper.rb`); any other key fails the suite.

| Key | Required | Rules enforced by tests |
| --- | --- | --- |
| `aliases` | no | Comma-separated slugs. Each must match `/\A[a-z0-9][a-z0-9-]*\Z/` and be ≤35 chars. Must not equal the topic, must be unique, ≤120 of them. Must not name a topic that has its own directory (use `related` instead) and must not collide with another topic's aliases. |
| `created_by` | no | No emoji. Oxford comma enforced. Names of the actual authors, not the contributor's. |
| `display_name` | no | No emoji. Proper-noun capitalization. |
| `github_url` | no | `http(s)://`, host must contain `github.com`. |
| `logo` | no | Must exactly match the image file present in the directory — and if an image is present, this key is required. |
| `related` | no | Comma-separated slugs, same format rules as aliases, ≤10, unique, must not equal the topic, must not overlap with `aliases`. |
| `released` | no | `MONTH DD, YYYY`, `MONTH YYYY`, or `YYYY`. Month spelled out in English, no comma directly after the month name, must end with a digit. Bare years need quoting (`released: '2010'`) so YAML keeps them as strings. |
| `short_description` | **yes** | ≤129 characters, must end with `.`, `?`, or `!`, no emoji, must differ from the body. Oxford comma enforced. |
| `topic` | **yes** | Must exactly equal the directory name; slug format, ≤35 chars. |
| `url` | no | `http(s)://` with a hostname. |
| `wikipedia_url` | no | `http(s)://`, host must contain `wikipedia.org`. |

Body (everything after the closing `---`): required, non-empty, under 2,000 characters, must differ
from `short_description`. Markdown links are fine; emoji are allowed in the body only.

Example (`topics/react/index.md`):

```markdown
---
aliases: reactjs, react-js
created_by: Jordan Walke
display_name: React
github_url: https://github.com/facebook/react
logo: react.png
related: vue, angular, react-native
released: March 2013
short_description: React is an open source JavaScript library used for designing user
  interfaces.
topic: react
url: https://reactjs.org/
wikipedia_url: https://en.wikipedia.org/wiki/React_(JavaScript_library)
---
React (also known as React.js or ReactJS) is a JavaScript library that makes developing interactive user interfaces simple.
```

### Topic images

- At most one image per directory, named `<topic-slug>.png` / `.jpg` / `.jpeg`.
- Exactly 288×288 pixels, at most 75,000 bytes.
- Must be the project's official logo, and the contributor must have permission to use it.
- The `logo:` front matter key and the file name must agree in both directions.

## `collections/<slug>/index.md`

Permitted keys: `collection`, `created_by`, `display_name`, `image`, `items`. Required:
`items` and `display_name`.

- `items` — YAML list, 1–100 entries. Each entry is a repo path (`owner/repo`), a username or
  organization, or any web URL (including YouTube). Entries that look like `owner/repo` or a bare
  username are checked against the live GitHub API and must exist, be public, and not be renamed.
- `display_name` — required, ≤100 characters, no emoji.
- `created_by` — a GitHub username (`/\A[a-z0-9]+(-[a-z0-9]+)*\z/i`), no emoji.
- `image` — must match an image file in the directory. Collections also allow `.gif`, and have no
  dimension or file-size checks.
- Body — required, under 2,000 characters.
- Collection slugs may be up to 40 characters.

```markdown
---
items:
 - beetbox/beets
 - musescore/MuseScore
 - https://www.youtube.com/watch?v=dSl_qnWO104
display_name: Music
created_by: jonrohan
---
Drop the code bass with these musically themed repositories.
```

## Prose conventions (`docs/styleguide.md`)

Some of these are enforced by the `follows the Topic Page Style Guide` test, which scans the body
line by line — the rest are review conventions. When writing or editing body text:

Enforced by tests (failures block CI):
- `open source`, never `open-source`.
- Spell months out: `January`, not `Jan`.
- Never use GitHub or Git as a verb (`GitHubbing`, `Gitting`).
- Capitalize `GitHub` and `Git` correctly — lowercase `github`/`Github` outside of a
  `github.com` URL, and `git` followed by `.`, `,`, `;`, `:`, or a space, all fail.
- Oxford comma in `short_description` and `created_by`.

Convention, checked by reviewers:
- Approachable, concise, community-oriented; assume no prior knowledge of the topic.
- Prefer "developers"/"people" over "users".
- Use "and", not "&", except in brand names. Avoid exclamation points.
- Write out numbers below ten. Never abbreviate "pull request" or "repository".
- "email" not "e-mail"; lowercase "internet" and "agile" unless starting a sentence.

`docs/API.md` says the body must be ≤1,000 characters and `short_description` ≤130; the tests
actually allow <2,000 and <130 respectively. **The tests are authoritative** — but stay close to the
documented limits, since maintainers review against the docs.

## Contribution workflow

1. One topic or collection per pull request. Maintainers close PRs that batch several together.
2. Contributors must not curate a topic or collection for a project they maintain or work on
   (conflict of interest).
3. New topics: create `topics/<slug>/` with `index.md` (and optionally the logo). If the slug
   appears in `topics-todo.md`, tick its checkbox in the same PR.
4. Fill out `.github/PULL_REQUEST_TEMPLATE.md` completely — incomplete templates get closed.
5. Run `script/cibuild` (or at minimum the relevant test file) before pushing. CI runs
   `bundle exec rake` and `bundle exec rubocop` on every push to `main` and every pull request.
6. `CODEOWNERS` routes everything to `@github/communities-oss-reviewers`, with
   `collections/made-in-india/` also going to `@github/india-community-reviewers`.
7. Pull requests go stale after 30 days of inactivity and close 7 days later
   (`.github/workflows/stale.yml`).

Content is CC-BY-4.0; see `notices.md`. Note that upstream docs still link to a `master` branch,
but the default branch here is `main`.

## Working notes for AI assistants

- **Prefer minimal, targeted diffs.** Touch one topic or collection at a time unless explicitly
  asked otherwise; a repo-wide sweep across 677 directories is almost never the requested change.
- **Read the tests before guessing at a rule.** `test/topics_test_helper.rb` and
  `test/collections_test_helper.rb` hold every constant (length caps, regexes, valid keys); the
  `_test.rb` files hold the assertions. They are cheaper and more accurate than inferring
  conventions from the docs.
- **Never invent facts** for `created_by`, `released`, `url`, `github_url`, or `wikipedia_url`.
  Omit an optional field rather than fill it with a plausible guess — every one of these is a claim
  about a real project.
- **Keys are conventionally alphabetized** in topic front matter; match the ordering and the
  two-space YAML continuation-line indentation used by existing files.
- **Do not add a logo you cannot verify permission for**, and do not resize or regenerate a logo to
  hit 288×288 if that distorts it.
- When adding an image, confirm the dimensions and byte size before committing —
  `ruby -rfastimage -e 'p FastImage.size(ARGV[0]), FastImage.new(ARGV[0]).content_length'`.
- Ruby changes are rare. If you do touch `test/` or the `Rakefile`, RuboCop enforces double-quoted
  strings, 100-character lines, trailing commas in multi-line literals, and `error` as the rescued
  exception variable name.
