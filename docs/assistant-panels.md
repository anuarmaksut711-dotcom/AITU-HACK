# Assistant-controlled meeting panels

The model decides whether a new answer benefits from a meeting side panel. This is
an explicit structured output, not a frontend keyword or citation heuristic:

```json
{"panel": {"meeting_id": "<authorized meeting UUID>", "view": "kanban"}}
```

`view` is `kanban`, `insights` or `conversation`; `panel: null` leaves the UI alone.
Workspace answer generation selects the target from retrieved transcript, catalog
or board sources. Task planning may select one of the meetings receiving created
tasks. The assistant routing prompt preserves requests to open (or not open) panels
when rewriting search queries. Ambiguous targets should produce no panel and a
clarifying answer; citing a board is not by itself a reason to open it.

The API validates the target against the already authorized response scope after
checking source freshness. Out-of-scope panel targets are discarded without losing
an otherwise valid answer. Task creation attaches the action to its idempotently
saved result only if its target is among the created tasks. Panels never authorize
writes; existing meeting and board endpoints retain their authorization checks.

The client validates the action shape and matches its meeting to the returned
sources/tasks again. A query-cache subscription observes pending-to-completed
turns in the active conversation. It consumes each new action once, after the
completed result is saved. Loading history, polling, switching back to a completed
chat or closing the panel does not replay it. Manual source-card opening remains
available. Closing the panel preserves the chat draft and scroll state.

Validation uses API tests with local SQLite and deterministic model doubles plus
browser tests with mocked API results. These cover optional/valid/foreign actions,
task-operation replay, automatic opening and non-replay after page reload. They do
not measure a live model's propensity to choose the right presentation.

## Direct navigation

`open_panel` is a separate assistant action for requests to open a board or meeting
view. `/assistant/panel` resolves it against an authorized meeting catalog using a
strict `PanelPlan`. It never runs embeddings or transcript search. A unique target
opens automatically; ambiguous targets return a `navigation` result with clickable
meeting choices. Ownership and existence are rechecked after model IO. Content
questions continue to use grounded search, whose catalog now includes the current
outcomes-processing state so a failed analysis can be explained accurately.

## Outcomes provider

Compose now uses `INTELLIGENCE_PROVIDER=openai` by default, with
`INTELLIGENCE_OPENAI_MODEL=gpt-5.6-luna` and low reasoning effort. The protocol
worker sends the full transcript chunks to OpenAI and retains exact-quote and
revision validation before saving outcomes. `INTELLIGENCE_PROVIDER=ollama` retains
the local-only path and its local endpoint validation; it is not an automatic
fallback. Settings used without Compose still default to Ollama.
