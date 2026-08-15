# Spec: summary quality on small models

Turns the findings in [`summarization-research.md`](summarization-research.md)
into buildable work. Written to be picked up cold: every milestone states the
interface, the behaviour, and how you know it is done.

**The order is deliberate.** Milestones A and B come first because A is
deterministic (no model, no judgement) and B is what makes C–F decidable at all.
Do not start C before B exists, or you are tuning by vibes.

---

## 0. Ground rules

**Two implementations, one algorithm.** The pipeline lives twice — Python
(`signal_summarizer/`) and Swift (`ios/Sources/SummarizerCore/`). They must stay
behaviourally identical: same chunk boundaries, same prompts, same checks. Python
is the reference implementation and the place to iterate, because the test loop is
seconds rather than an Xcode build.

**Prompts are data, not code.** Move the prompt strings into one module per
language (`signal_summarizer/prompts.py`, `SummarizerCore/Prompts.swift`) so a
prompt change is a one-file diff and the eval harness can pin a version.
Every prompt gets a `PROMPT_VERSION` string recorded in eval output.

**Nothing regresses silently.** Each milestone ships with tests. A change that
improves fact recall but doubles wall-clock is reported as both numbers, not
merged on the strength of the first.

### Non-goals for this round

- No new backends. Ollama / OpenAI-compatible / command / Claude stay as they are.
- No model fine-tuning or adapter training (that is downstream of B and F).
- No change to the Signal transport, storage schema, or bot commands.
- No cloud eval service. The harness is a local script and a JSONL file.

---

## Milestone A — Grounding and hygiene

Deterministic, model-free, and aimed at the two documented failure modes
(attribution errors, chatter treated as salient). Ship all four together.

### A1. Participant roster

Every window has a roster: the distinct senders in it, in first-appearance order.

```python
# signal_summarizer/summarizer.py
def roster(messages: Sequence[Message]) -> list[str]:
    """Distinct sender names, first-appearance order."""
```

Used in two places: injected into the prompt ("The only people in this
conversation are: Alice, Bob, You. Do not name anyone else.") and as the legal
name set for A2.

### A2. Grounding checks

```python
# signal_summarizer/grounding.py  (new)
@dataclass(frozen=True)
class GroundingIssue:
    kind: str        # "unknown-name" | "unsourced-number" | "leaked-markup"
    detail: str      # the offending token

def check(summary: str, messages: Sequence[Message]) -> list[GroundingIssue]: ...
```

Rules, in order of value:

| Check | Rule | Notes |
|---|---|---|
| `unknown-name` | Every capitalised token that looks like a personal name and is not in the roster is an issue | Match against roster case-insensitively; ignore sentence-initial common words via a small stopword list. False positives are acceptable — this is a flag, not a gate |
| `unsourced-number` | Every number, time and date in the summary must appear in the source text | Normalise digits only (`10`, `10:00`, `2026-08-15`); skip numbers that are part of our own scaffolding ("3 messages") |
| `leaked-markup` | Summary contains `<think`, `---`, `part N ---`, or the system prompt's opening words | Catches template bleed |

**Behaviour on failure:** never silently drop the summary, and never auto-retry
in v1 (a retry doubles latency on a phone for an unproven gain). Log every issue
at `WARNING`, count them in eval output, and append a single line to the summary
only when there is at least one `unknown-name` issue:

```
(Names I could not match to this conversation: Dave. Treat that line with caution.)
```

Rationale: attribution is the failure the user cannot detect by eye, so it is the
one worth surfacing in-product. Numbers are reported to the operator, not the chat.

### A3. Noise filter

```python
def is_noise(message: Message) -> bool: ...
```

Drop before chunking. Conservative list only — when in doubt, keep:

- Body is only emoji, punctuation, or whitespace.
- Body matches a short acknowledgement set, case-insensitive, ≤ 3 words:
  `ok`, `okay`, `k`, `thanks`, `thank you`, `ty`, `yes`, `no`, `yep`, `nope`,
  `sure`, `lol`, `haha`, `same`, `+1`, `nice`, `cool`, `got it`, `sounds good`.
- Attachment-only messages with no body (already excluded on iOS).

**Never** drop a message that carries a quote, contains a `?`, or contains a
digit — a bare "yes" answering a question is signal, and the quote proves it.

Report the count: `filtered 34 of 210 messages as noise` at `INFO`, and expose it
in eval output. If the filter is dropping more than ~40% of a normal window, it is
too aggressive.

### A4. Chunk on conversation structure

Replace character-budget chunking. New rules, applied in order:

1. **Session split** — a gap of ≥ `session_gap_minutes` (default 60) between
   consecutive messages starts a new chunk, regardless of size.
2. **Budget split** — within a session, split when adding the next message would
   exceed `transcript_budget`.
3. **Never** split between a message and the message it quotes, when both are in
   the window.
4. **Overlap** — carry the last `chunk_overlap_messages` (default 2) messages of
   chunk *n* into the head of chunk *n+1*, marked so the model knows they are
   context rather than new material.
5. A single message larger than the budget is its own chunk, truncated with the
   existing "earlier messages omitted" marker.

```python
def chunk_messages(
    messages: Sequence[Message],
    budget: int,
    *,
    session_gap_minutes: int = 60,
    overlap: int = 2,
) -> list[Chunk]: ...

@dataclass(frozen=True)
class Chunk:
    messages: list[Message]
    overlap_count: int      # leading messages that are context, not new
    started_at: datetime
    ended_at: datetime
```

Swift mirror: `Transcript.chunk(_:budget:sessionGap:overlap:) -> [Chunk]`.

### Acceptance for Milestone A

- Unit tests: roster ordering and dedup; each grounding rule firing and not
  firing; noise filter keeps question/quote/digit messages; chunker splits on a
  61-minute gap, does not split on 59, keeps quote+reply together, applies overlap.
- A real window of ≥ 200 messages produces chunks whose boundaries all fall on
  either a session gap or a budget limit — assert no boundary lands between a
  quote and its reply.
- `signal-summarizer digest` output is unchanged in shape; the new warning line
  appears only when a name is unmatched.

---

## Milestone B — The eval harness

The point of the whole exercise: make C–F decidable. Small, local, no service.

### B1. Dataset format

One JSONL file, one window per line, at a path the user chooses
(`SUMMARIZER_EVAL_SET`, default `eval/windows.jsonl`). Gitignored by default —
it contains real messages.

```json
{
  "id": "standup-2026-08-14",
  "label": "Standup",
  "window": "the last 24 hours",
  "messages": [
    {"sender": "Alice", "timestamp": 1755100000000, "body": "standup moves to 10", "quote": null}
  ],
  "must_contain": [
    "standup moved to 10",
    "Bob is updating the invite",
    "release notes are unassigned"
  ],
  "must_not_contain": ["Dave"]
}
```

`must_contain` is 3–6 short factual claims a correct summary has to convey —
written by hand, phrased as facts rather than exact wording. `must_not_contain`
is optional and catches known confusions (a person not in the chat, a decision
that was explicitly deferred).

A `scripts/make_eval_window.py` helper pulls a window straight out of a live
`signal-summarizer.db` into this shape, so building the set is copy-paste-free:

```bash
python scripts/make_eval_window.py --chat group:abc= --hours 24 --id standup-2026-08-14 >> eval/windows.jsonl
```

### B2. Scoring

```bash
signal-summarizer eval --set eval/windows.jsonl --tag baseline
```

Runs the configured backend over every window and writes
`eval/runs/<tag>-<timestamp>.json` containing, per window:

| Field | How |
|---|---|
| `summary` | The produced text |
| `grounding` | Issues from A2, by kind |
| `must_contain_hits` | **Model-free** substring/keyword match as a *hint*, plus a manual field |
| `manual_recall` | `null` until a human fills it in — the number of `must_contain` facts actually conveyed |
| `must_not_contain_hits` | Exact matches, automatic |
| `elapsed_seconds`, `model_calls`, `chunks`, `filtered_messages` | Cost |
| `prompt_version`, `backend`, `model`, `strategy`, `config_digest` | Provenance |

**Manual recall is the metric that decides things.** The harness prints a review
prompt after the run — each summary beside its `must_contain` list — and writes
the scores back into the run file. Twenty-five windows takes about fifteen
minutes to score. Do not automate this with an LLM judge in v1; the research is
explicit that judges do badly on dialogue, and a small local judge is worthless.

```bash
signal-summarizer eval review eval/runs/baseline-20260815.json   # interactive scoring
signal-summarizer eval compare baseline refine-strategy          # A/B table
```

`compare` prints per-window and aggregate deltas:

```
                       baseline    refine     Δ
fact recall              71%        84%     +13pp
grounding issues          9          4        -5
median latency           38s        51s     +13s
model calls (mean)        4.2        5.0     +0.8
```

### B3. Regression tests, not just experiments

Three windows from the set get frozen into `tests/` as fixtures with a stub
backend, asserting pipeline shape rather than model output: chunk count,
roster, filtered count, prompt contents. These run in CI-speed pytest and catch
accidental changes to chunking or prompt assembly.

### Acceptance for Milestone B

- `eval` runs end to end against the `command` backend with a stub script and
  produces a run file with every field populated except `manual_recall`.
- `eval review` writes scores back and is resumable (scoring 10 of 25, quitting,
  and resuming keeps the 10).
- `eval compare` refuses to compare runs whose `config_digest` differs in ways
  that are not the tagged change, or prints a loud warning naming the differences.
- A baseline run over your real eval set exists and is committed *as numbers*
  (`docs/eval-baseline.md`), not as transcripts.

---

## Milestone C — Strategy switch

Make the architecture choice measurable rather than argued.

```python
# config
strategy: str = "map_reduce"   # map_reduce | refine | per_topic
```

- **`map_reduce`** — current behaviour, unchanged. Stays the default until B says
  otherwise.
- **`refine`** — summarize chunk 1; for each subsequent chunk, pass the running
  summary plus the new chunk and ask for a revised summary. One call per chunk,
  no combine step. Add a summary-of-summaries pass every
  `refine_recompress_every` (default 6) chunks to bound drift.
- **`per_topic`** — treat each *session* (from A4's gap splitting) as a topic,
  summarize each independently, and concatenate under a time-range heading with no
  fusing pass. Cheapest way to test the topic-decomposition finding.

All three share the chunker, roster, filter and checks. `estimate_calls` must stay
accurate per strategy — the iOS progress UI depends on it.

**Acceptance:** all three strategies produce a summary for the same window;
`estimate_calls` matches actual call count for each (existing property test,
extended); an eval comparison of the three exists in `docs/eval-baseline.md`.

---

## Milestone D — Extract, then abstract

Two-stage per chunk, behind `extract_first: bool = False` until eval justifies it.

**Stage 1 — select.** Ask the model for the indices of messages that carry
decisions, commitments, questions, plans or numbers. Output is a bare list of
integers; anything unparseable falls back to "keep everything" rather than failing.

```
Messages are numbered. Reply with only the numbers of messages that state a
decision, a commitment, a question that is still open, a plan, or a number/date
that matters. No other text.
```

**Stage 2 — write.** Render only the selected messages (plus their quoted
parents) and run the normal summary prompt over them.

Cost is one extra call per chunk, and stage 1 output is tiny. Eval decides
whether the hallucination reduction is worth the latency.

**Acceptance:** stage-1 parse failures fall back cleanly (test with a stub
returning prose); selected-message count is logged; eval comparison recorded.

---

## Milestone E — Structured output

Flat schema, both languages. Not nested, per the constraint-tax finding.

```swift
@Generable struct SummaryDraft {
    @Guide(description: "Topics discussed, one short sentence each") var topics: [String]
    @Guide(description: "Decisions made, naming who made them") var decisions: [String]
    @Guide(description: "Action items as: Name — what — when") var actionItems: [String]
    @Guide(description: "Questions still unanswered") var openQuestions: [String]
}
```

Python equivalent: request JSON with the same four keys; parse with a tolerant
reader (strip code fences, accept a bare array under a missing key, fall back to
treating the whole response as prose if parsing fails).

**We render, the model does not.** Section headings, ordering, and the "at most 5
bullets per section" cap are applied by our code after parsing. This is where the
length control lives — no word counts in the prompt.

**Acceptance:** parse-failure fallback covered by tests; rendered output is
byte-identical for the same parsed struct across Python and Swift; eval compares
structured vs prose on quality, *not* on parse rate.

---

## Milestone F — Anchored iterative state (iOS)

Only after A–C are settled, because it bakes the pipeline into stored state.

- **Store:** one row per thread — `threadUniqueId`, `updatedThroughTimestamp`,
  `SummaryDraft` (E's struct, encoded), `recentVerbatim` (last N messages),
  `promptVersion`, `schemaVersion`.
- **Update trigger:** on app foreground and on a batch of ≥ `updateBatchSize`
  (default 25) new messages; never while the user is actively summarizing.
- **Update call:** existing state + new messages → revised state. One model call
  per batch, small prompt, well inside the window.
- **Invalidate** on: prompt version change, schema change, a message edit or
  remote delete inside the covered range, or a gap in coverage (the app was not
  run for long enough that messages were missed).
- **Tapping Summarize** renders stored state instantly, then, if
  `updatedThroughTimestamp` lags the newest message, runs one catch-up call and
  re-renders.

**Acceptance:** cold start with no state behaves exactly as today; a state that
covers the window renders in under a second with no model call; invalidation
paths each covered by a test; drift bounded by a recompress pass every
`stateRecompressEvery` (default 10) updates.

---

## Configuration added

| Setting | Default | Milestone |
|---|---|---|
| `SUMMARIZER_SESSION_GAP_MINUTES` | `60` | A |
| `SUMMARIZER_CHUNK_OVERLAP` | `2` | A |
| `SUMMARIZER_FILTER_NOISE` | `1` | A |
| `SUMMARIZER_GROUNDING_CHECKS` | `1` | A |
| `SUMMARIZER_EVAL_SET` | `eval/windows.jsonl` | B |
| `SUMMARIZER_STRATEGY` | `map_reduce` | C |
| `SUMMARIZER_REFINE_RECOMPRESS_EVERY` | `6` | C |
| `SUMMARIZER_EXTRACT_FIRST` | `0` | D |
| `SUMMARIZER_STRUCTURED_OUTPUT` | `0` | E |

Every one is off-by-default or behaviour-preserving except A's, which are on
because they are strictly additive.

---

## Running it on a laptop

Fastest loop is the Python side against a local model. macOS:

```bash
# 1. model
brew install ollama && ollama serve &
ollama pull llama3.2:3b          # or qwen2.5:7b if you have the RAM

# 2. project
cd signal-summarizer
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 3. confirm the model path works
export SIGNAL_ACCOUNT=+15551234567       # not used by digest/eval, but Config requires it
export SUMMARIZER_BACKEND=ollama
export EDGE_MODEL=llama3.2:3b
signal-summarizer check

# 4. tests
python -m pytest -q
cd ios && swift test && cd ..            # Swift core, needs Xcode CLT
```

To iterate without Signal at all, point `SUMMARIZER_DB` at a scratch database and
seed it with `scripts/make_eval_window.py --from-json` (part of Milestone B), or
run `signal-summarizer eval` directly against the JSONL set — neither needs
signal-cli running.

To iterate *with* real messages, run the bot as documented in the main README
(signal-cli daemon + `signal-summarizer run`), then pull windows out of its
database into the eval set.

For the iOS side, the Swift core builds and tests standalone (`cd ios && swift
test`) without opening the Signal workspace — do pipeline work there, and only
open Xcode when touching `SignalPatch/`.

---

## Risks and open questions

- **The name checker will have false positives.** English capitalises sentence
  openers and product names. It flags rather than blocks, so the cost is a noisy
  log; if it proves too noisy, restrict it to tokens that also appear in a
  known-names list built from the thread's history.
- **Noise filtering can remove the answer.** "yes" to "shall we ship Friday?" is
  the decision. The quote/question/digit carve-outs cover the common case, but
  the eval set should include at least one window where a bare acknowledgement is
  load-bearing.
- **Refine may drift worse than map-reduce at 1B.** The recompress pass is the
  mitigation; if eval shows refine losing at 1B but winning at 3B, make the
  default depend on `edge_context_tokens` or model size rather than picking one
  globally.
- **Structured output may cost more than it saves** on the smallest models. That
  is exactly what E's eval comparison is for — be willing to leave it off.
- **The eval set is real message data.** It is gitignored, and
  `docs/eval-baseline.md` records only aggregate numbers. Do not commit windows.
