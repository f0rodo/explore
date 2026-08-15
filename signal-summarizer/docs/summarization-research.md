# Summarizing chat with a small model: what the evidence says

August 2026. Research notes for this project, aimed at the on-device path
(Apple's ~3B model, or a 1–4B GGUF), where the model is the constraint rather
than the plumbing.

Confidence is marked per finding: **[strong]** = multiple sources or a
benchmark paper; **[moderate]** = one credible source or consistent practitioner
reports; **[weak]** = plausible, untested, or my inference. Sources are listed
at the end. Note that arxiv.org and huggingface.co were unreachable from the
machine this was researched on, so several papers are cited through secondary
summaries rather than read in full — those are marked **[secondary]**.

---

## 1. What actually goes wrong

Dialogue is the hard case for summarization, and the failure modes are
specific rather than general "the model is dumb":

Five error categories recur in the literature on LLM conversation summaries
**[strong, secondary]**: missing information from failing to connect events
across a long conversation; hallucinated details, including details imported
from a *different* part of the conversation; misreading humor and sarcasm;
**inaccurate speaker attribution**; and treating insignificant chatter as
salient.

Two of those matter disproportionately for us:

- **Attribution.** "Bob will update the invite" is the load-bearing part of a
  chat summary, and it is exactly what models get wrong. Hallucinations
  correlate strongly with low quality ratings; attribution errors correlate
  weakly-to-moderately — but for an action-item summary an attribution error is
  the whole product being wrong.
- **Salience on secondary topics.** TofuEval found error rates on *marginal*
  topics run far higher than on the main topic: for 7B-class models, ~35–36% of
  summary sentences on marginal topics contained errors, versus ~20–29% on main
  topics; at the summary level, 55% of marginal-topic summaries had an error
  **[strong, secondary]**. A group chat is mostly marginal topics.

The uncomfortable corollary from the same work: **hallucination is not solved by
scale** — models hallucinate factual errors in dialogue "regardless of model
size". Going from a 1B to a 3B model narrows the gap but does not close it.

**Implication.** Design the pipeline so the model is never asked to do
attribution and salience judgement in the same pass over a long, multi-topic
transcript. Everything below follows from that.

---

## 2. Architecture: how to get a long transcript through a small window

### 2.1 The three classic shapes

| Shape | How | Trade-off |
|---|---|---|
| **Stuff** | Whole transcript in one prompt | Only viable inside the window. Apple's on-device model shares ~4,096 tokens between instructions, prompt and output **[moderate]**, so this covers maybe an hour of a busy group chat |
| **Map-reduce** | Summarize each chunk independently, then summarize the summaries | Parallelizable; loses cross-chunk narrative; each chunk is summarized blind to the others |
| **Refine** | Summarize chunk 1, then feed that summary plus chunk 2, and so on | Keeps an evolving global context; **reported to beat map-reduce on accuracy** and to preserve detail and coherence better **[moderate]**; strictly sequential |

We currently do map-reduce (`summarizer.py`, `ConversationSummarizer.swift`).
The usual argument for map-reduce is parallelism — **which is worth nothing to
us**: on a phone the model is a single serial resource either way. That removes
map-reduce's only advantage over refine.

The caution on refine with a *small* model is drift: each step rewrites the
running summary, so errors compound and early detail gets progressively
compressed. Practitioner reports on long agent sessions describe exactly this
("context rot", summary drift) and mitigate it with a **Summary-of-Summaries**
pass at intervals **[moderate]**.

### 2.2 The shape that actually fits a phone: anchored iterative summarization

The pattern converging in agent-memory work **[moderate]**:

- Keep a **persistent, structured summary** — not free prose — per conversation.
- **Incrementally update** it as new messages arrive, in small batches.
- Keep the **most recent N turns verbatim** alongside it.
- Periodically compress old summary material (summary-of-summaries) to stop it
  growing without bound.
- Do the updates **in the background**, so the summary is already current when
  the user asks for it.

For our iOS app this is a much better fit than summarizing on demand:

- The model never sees more than a batch of new messages plus a compact state
  object, so we stay far inside 4,096 tokens without any chunking machinery.
- Tapping **Summarize** becomes near-instant — it renders existing state rather
  than running 5 passes at 15 tok/s.
- Cost is amortized into moments when the phone is already awake and charging,
  instead of a 2-minute stall when the user asks.

The cost is complexity (state to store, migrate and invalidate when messages are
edited or deleted) and drift, which the structured state and periodic
recompression are there to bound.

### 2.3 Chunk on conversation structure, not character counts

We currently chunk by character budget, which cuts mid-topic and mid-exchange.
Cheaper and better **[weak — deterministic, but untested here]**:

- Split on **time gaps** (a 45–90 minute silence is a near-free session
  boundary in chat).
- Never split between a message and its quoted reply.
- Prefer speaker-turn boundaries over mid-turn splits.
- Add small **overlap** between chunks so a decision spanning the boundary
  appears in both.

Chunk-and-merge with *overlapping* segments is the standard recommendation for
conversation specifically **[moderate]**.

### 2.4 Topic-focused decomposition

Given that marginal-topic errors dominate, the direct fix is to stop asking for
one summary of everything. Recent work decomposes dialogue summarization along
**topic and participant** axes and summarizes each thread separately
**[moderate, secondary]** — and TofuEval's whole framing is *topic-focused*
summaries.

Practical version for a group chat: segment by topic (or by time-gap as a
proxy), summarize each segment on its own, then concatenate under headings
rather than fusing into one narrative. A concatenation of correct
per-topic summaries is more useful than a fluent global summary that
misattributes half of it.

---

## 3. Prompting and generation technique

### 3.1 Extract, then abstract

Two-stage — pull the salient lines out extractively, then rewrite only those
abstractively — is a long-standing way to cut hallucination: the abstractive
step is constrained to a relevant subset and has less to process **[strong]**.
Abstractive generation is where hallucination comes from; extraction cannot
invent.

For us the first stage can be nearly free: a pass that selects the message
*indices* that matter (decisions, commitments, questions, plans) and discards
greetings, reactions and "ok". Stage two writes prose from only those messages,
with speakers attached. It also directly attacks the "insignificant dialogs
wrongly considered salient" failure.

On Apple's framework there is a shipped **content-tagging** use case
(`SystemLanguageModel(useCase: .contentTagging)`) intended for entity
extraction, topic detection and tag generation **[moderate]** — a natural fit
for the extraction stage rather than a hand-prompted one.

### 3.2 Structured output: useful, but there is a tax

Apple's **guided generation** (`@Generable`) constrains output to a Swift type,
removing format-wrangling from the prompt and eliminating malformed output
**[moderate]**. That is genuinely valuable for small models, which are the ones
that ramble, add preambles, and ignore "reply with only the summary".

But constrained decoding is **not free**, and the effect is worse on small
models **[moderate]**:

- It guarantees *syntactic* validity, never semantic correctness — a
  perfectly-shaped object can contain a wrong or invented value.
- Forcing the model down a legal-token path can produce tokenizations it rarely
  saw in training, subtly degrading quality.
- Part of the generation budget goes to syntax rather than the task — and on a
  sub-3B model with weaker instruction-following, "the same schema can compete
  with the task it is meant to package".
- Tracking parse-success as your metric can show improvement while the actual
  answers get worse.

**Therefore:** use a *shallow, flat* schema — a few arrays of short strings
(`topics`, `decisions`, `actionItems`, `openQuestions`), each item one sentence
— not a deep nested object with enums and optionals. And evaluate summary
quality, never just "did it parse".

### 3.3 Chain-of-Density and friends

Chain-of-Density (iteratively re-write at fixed length while fusing in missing
entities) is well-established **on GPT-4-class models** **[strong]**. I found
**no evidence for CoD on 3B-class models** — the published results are large
models, and the technique demands exactly the instruction-following and
rewriting capacity small models lack **[weak: absence of evidence]**. Do not
assume it transfers.

What *does* transfer is the distillation trick: generate CoD-style summaries
with a large model, then **fine-tune a small model on them** — reported as
~20× lower latency and ~50× lower cost while retaining entity density
**[moderate]**. That is the shape of a later win for us (§5).

### 3.4 Small-model prompt hygiene

Consistent with the constraint-tax finding and with what we saw building the
Termux path:

- **Length control by structure, not word counts.** "Under 400 words" is poorly
  followed by small models; "at most 5 bullets, one sentence each" is a
  structural constraint they can satisfy.
- **Short instructions.** The instruction block competes with the transcript for
  the same 4,096 tokens. Our current system prompt is ~200 tokens; that is ~5%
  of the window, which is acceptable but not free.
- **Give the model the participant roster explicitly** and tell it to use only
  those names. Attribution errors are a known failure mode; naming the legal set
  is the cheapest available mitigation **[weak, but low-cost]**.
- Strip `<think>` blocks (already done in `backends/edge.py`) — reasoning models
  spend the shared budget thinking.

---

## 4. Evaluation: the part that decides whether any of this helped

This is where the literature is most useful and most sobering.

**Automated faithfulness metrics are weak on dialogue.** TofuEval's headline
secondary finding: LLMs used as binary factual evaluators — GPT-4 included —
**perform poorly and are outperformed by specialized factuality metrics**, and
*all* automated metrics still do badly on dialogue **[strong, secondary]**. An
LLM-judge score is not a substitute for looking at outputs here, and a judge
running on the *same* small on-device model is close to worthless.

**ROUGE is not the metric.** Reference-based overlap tells you little about
whether "Bob" or "Alice" owns the action item.

What to do instead, cheapest-first:

1. **Deterministic grounding checks** — these are the highest value-per-effort
   for our specific failure modes, and they need no model:
   - every person named in the summary appears in the chunk's participant roster;
   - every number, date and time in the summary appears in the source text;
   - no summary sentence exceeds the section's bullet budget;
   - output is non-empty and contains no leaked template/tag text.
2. **A small frozen eval set** — 20–30 real windows from your own chats, with
   hand-written "what a correct summary must contain" bullet lists (3–6 facts
   each). Score recall of those facts by hand once per technique change. Thirty
   transcripts is enough to see a technique win or lose.
3. **Pairwise preference, not absolute scores.** Judge A-vs-B on the same
   window; absolute 1–5 quality scores from an LLM judge are noisy and biased.
   If you use a judge, use a *large* model off-device for eval only — this is
   the one place sending data off-device might be acceptable, on synthetic or
   consented transcripts.
4. **Latency and pass count** alongside quality. On a phone, a technique that
   improves quality 5% and triples wall-clock is a regression.

---

## 5. Fine-tuning, when prompting runs out

PEFT/LoRA fine-tuning on dialogue-summarization benchmarks (SAMSum, DialogSum)
is well-trodden and effective — reported ROUGE-1 ≈ 48–49 and BERTScore ≈ 91–92%
for LLaMA-2 with LoRA on both datasets **[moderate, secondary]**. Apple ships an
**adapter toolkit for training LoRA adapters** against the on-device model
**[moderate]**, so this is a supported path on our target platform rather than a
research exercise.

The sequencing that makes sense:

1. Get the pipeline right with prompting (§2, §3), measured with §4.
2. If quality is still short, generate training pairs by summarizing *your own*
   chats with a frontier model once, correcting the outputs by hand, and
   training an adapter on that. Personal-style adaptation ("summarize like this,
   for these people") is where a tiny adapter beats a bigger generic model.
3. Note the privacy trade: that one-time labelling step sends real chats
   off-device. Use consented or synthetic transcripts, or accept the exposure
   knowingly — and document it, since privacy is the whole point of this project.

---

## 6. What this means for our code

Ordered by value per unit of work. Nothing here is committed yet.

| # | Change | Where | Why |
|---|---|---|---|
| 1 | **Deterministic grounding checks** on every summary: names ∈ roster, numbers/dates ∈ source | `summarizer.py`, `ConversationSummarizer.swift` | Attacks the documented top failure mode; no model, no latency; gives the eval harness teeth |
| 2 | **Chunk on time gaps and turn boundaries**, with small overlap; never split a quote from its reply | `Transcript.chunk`, `chunk_messages` | Removes mid-topic cuts, which is where attribution errors come from |
| 3 | **Drop the greetings/reactions** before chunking (deterministic filter) | both | "Insignificant dialogue treated as salient" is a named failure mode; also buys window space |
| 4 | **Per-topic (or per-session) summaries concatenated under headings**, instead of one fused narrative | prompts in both | Marginal-topic error rates are ~2× main-topic; stop fusing them |
| 5 | **Explicit participant roster in the prompt**, constrained to those names | prompts in both | Cheapest attribution mitigation available |
| 6 | **Replace map-reduce with refine**, or keep map-reduce but add a summary-of-summaries pass | `_combine` / `combine` | Refine reportedly wins on accuracy; parallelism was our only reason to prefer map-reduce and it is worthless on a phone |
| 7 | **Anchored iterative summarization** for iOS: structured running state per chat, updated in the background | new, iOS side | Turns a 2-minute wait into an instant answer; keeps every prompt small |
| 8 | **Shallow `@Generable` struct** for the summary, rendered to text by us | `FoundationModelsEngine`, sheet | Kills preambles and format drift — but keep the schema flat and measure quality, not parse rate |
| 9 | **Extraction pass before the writing pass**, possibly via the content-tagging use case | both | Best-established hallucination reduction available |
| 10 | **Eval set of 20–30 windows + fact-recall scoring** | `tests/` or a small harness | Without this, every change above is a guess |

Do 1–3 first: they are deterministic, cheap, and independently useful. Then
build 10, because 4–9 are all judgement calls that need measurement to settle.

---

## Sources

- [TofuEval: Evaluating Hallucinations of LLMs on Topic-Focused Dialogue Summarization](https://aclanthology.org/2024.naacl-long.251/) — marginal vs main topic error rates; automated faithfulness metrics and LLM judges underperform ([review summary](https://www.themoonlight.io/en/review/tofueval-evaluating-hallucinations-of-llms-on-topic-focused-dialogue-summarization), [Amazon Science](https://www.amazon.science/code-and-datasets/tofueval-evaluating-hallucinations-of-llms-on-topic-focused-dialogue-summarization))
- [Evaluating Very Long-Term Conversational Memory of LLM Agents](https://arxiv.org/html/2402.17753v1) — the five conversation-summarization error categories, including speaker attribution
- [Summarization techniques, iterative refinement and map-reduce for document workflows](https://cloud.google.com/blog/products/ai-machine-learning/long-document-summarization-with-workflows-and-gemini-models) — map-reduce vs refine trade-offs
- [Stop LLM Summarization From Failing Users](https://galileo.ai/blog/llm-summarization-production-guide) — chunk-and-merge with overlap for chat; multi-topic tracking
- [A Hybrid Strategy for Chat Transcript Summarization](https://arxiv.org/pdf/2402.01510) — chat-specific pipeline (not readable from this sandbox)
- [Recursively Summarizing Enables Long-Term Dialogue Memory in LLMs](https://arxiv.org/html/2308.15022v3) — recursive summarization for long dialogue memory
- [Dialogue Summarization with Emotion Dynamics Using Topic- and Participant-Centric Decomposition](https://arxiv.org/html/2607.14769v1) — topic/participant decomposition
- [AI Agent Memory Compaction Strategies for Long Sessions](https://fast.io/resources/ai-agent-memory-compaction-strategies/) and [Agent Context Compaction for Long-Running Sessions](https://zylos.ai/research/2026-04-21-agent-context-compaction-long-running-sessions/) — anchored iterative summarization, rolling summaries, context rot, summary-of-summaries
- [Meet the Foundation Models framework (WWDC25)](https://developer.apple.com/videos/play/wwdc2025/286/) and [Apple Foundation Models: the on-device LLM framework, explained](https://blakecrosley.com/blog/apple-foundation-models-framework) — guided generation, content-tagging use case, adapter toolkit
- [Apple Foundation Models in iOS 27: builder guide](https://chatforest.com/builders-log/apple-foundation-models-ios-27-on-device-llm-api-builder-guide/) — the 4,096-token shared budget and session behaviour
- [The Constraint Tax: Measuring Validity-Correctness Tradeoffs in Structured Outputs for Small Language Models](https://arxiv.org/pdf/2605.26128) and [When Correct Isn't Usable: Improving Structured Output Reliability in Small Language Models](https://arxiv.org/pdf/2605.02363) — constrained decoding costs on small models
- [From Sparse to Dense: GPT-4 Summarization with Chain of Density Prompting](https://arxiv.org/pdf/2309.04269) and [Smarter Summaries w/ Finetuning GPT-3.5 and Chain of Density](https://python.useinstructor.com/blog/2023/11/05/chain-of-density/) — CoD, and distilling it into a smaller model
- [A Comprehensive Approach for Fine-tuning and Evaluation of LLMs on Dialogue Summarization](https://link.springer.com/article/10.1007/s42979-026-04852-6) — LoRA results on SAMSum/DialogSum
- [Text Summarization: Extractive vs Abstractive](https://thecodeforge.io/ml-ai/text-summarization/) — two-stage extract-then-abstract and hallucination
- [LLM-as-a-Judge in 2026](https://deepeval.com/blog/llm-as-a-judge) — judge techniques, jury-of-judges, reference-free metrics in production
