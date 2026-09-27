# Dynamic operation + target

The input is a natural-language goal. Every page observation builds an indexed table of accessible elements and their current values. One node receives one index, even when it supports both clicking and typing.

One TypeSafe request asks which operation to perform and which target would be appropriate for each available operation. The executor consumes only the target head corresponding to the selected operation. This avoids serial operation-then-target calls and rejects targets incompatible with the operation. Dropdown targets include a code-owned option index.

Operation and target questions receive the same next-step rules. Target criteria include current values and checked/selected state. The questions run independently: a target cannot read the operation answer, so its premise explicitly names the operation it assumes.

TYPE_TEXT sends the goal, selected field, visible page context, and recent actions to a small LLM. Its JSON must contain exactly one valid `text` value. The code does not extract quoted literals. A value can be reused after a stale decision only while the entire helper input is identical, and is discarded after a successful mutation.

## Runtime

One browser-side DOM snapshot supplies common HTML/ARIA roles, names, values, visible text, and executable targets. A WeakMap gives each actual node a code-owned identity; a Map keeps the live references used for execution. Replaced elements receive new identities, disconnected references are pruned, and navigation starts a new cache. These IDs are not CDP backend node IDs. Geometry is always read again immediately before input.

The model sees visible text. Background focus emulation keeps animation frames running in the owned tab. Screenshots are optional and disabled in library calls by default; `screenshots=True` or `record_dir=...` enables them. The inspector enables them explicitly. A continuous screencast can record a run separately.

Freshness compares semantic state instead of counting DOM mutations. Before a click/select, guards compare the document, full URL, viewport, safe form values/states, selected target, and nearby form/dialog/row context. Text generation, typing, scrolling, waiting, and completion use a full semantic comparison. The executor rechecks target visibility, enabled state, geometry, and click occlusion. Scoped guards intentionally permit unrelated visible content to change; this is a practical heuristic, not proof that arbitrary page changes are irrelevant to the goal.

Browser mutations are not retried by transport recovery. Completed execution is logged before the next observation, including when that observation encounters a navigation. An interrupted native-select evaluation stops because its change event may already have fired. Typing uses a browser select-all command followed by CDP text insertion, so existing input contents are replaced.

The next observation waits for up to two animation frames or 50 ms after an interaction. Editable ARIA comboboxes instead wait for visible options, capped at 200 ms. This avoids paying for a prediction before autocomplete suggestions arrive. An explicit WAIT remains 100 ms; network loading is never fast-forwarded in the recording.

## Multi-step execution and rescue

Complex tasks often require sequential phases (e.g., searching for options, selecting a result or applying filters, and completing form details). Rather than forcing every browser run into a single monolithic plan:

1. **Multi-step goal lifecycle (`STEP` operation)**:
   - When a task begins, the text model decomposes the overarching goal into sequential milestones with `plan_multi_step_goal`.
   - The fast local policy (`LayaPolicy`) executes the requirements and finish condition for the active step.
   - When the step's finish condition is satisfied, the policy emits `STEP` if subsequent steps remain, or `DONE` if the step is final.
   - In `Agent.command("act")`, a `STEP` outcome logs the completed milestone in `state["history"]`, captures a fresh page observation, and calls `plan_next_step` to evaluate progress and generate requirements for the next stage on the updated DOM.
   - The policy advances its internal step state (`advance_step`), and execution continues seamlessly in `ready` status.

2. **Text model rescue (`RESCUE` choice)**:
   - Local policies can encounter unexpected obstacles: cookie consent banners, unexpected modal overlays, unhandled validation states, or 3 failed attempts on form requirements.
   - Instead of abruptly failing with `BLOCKED`, the policy escalates to `RESCUE`.
   - The text model (`rescue_agent`) diagnoses the issue from a fresh page snapshot, recent action history, and candidate elements.
   - The rescue response prescribes an action:
     - Direct corrective browser action (`click` or `fill` targeting an observed element, such as closing an overlay),
     - A revised step plan (`replan`),
     - Or an unrecoverable signal (`give_up`), which then gracefully halts execution as `BLOCKED`.
   - To guarantee safety and prevent infinite recovery loops, the policy tracks its rescue state and halts if a subsequent hurdle cannot make progress.

## What changed after the first demo

The initial prototype used five manually prepared steps and copied quoted strings. That proved finite-choice browser execution but did not demonstrate task decomposition or text generation. The current policy removes that shortcut and uses the original goal throughout. Operation/target distributions replace the old flat-choice/lookahead/Noul arrangement.

The audit also found that treating every INPUT as editable misclassified checkboxes. Editable roles now control TYPE_TEXT availability. Tests cover checkbox/radio/button distinction, invalid operation/target outputs, stale decisions, text-cache invalidation, missing credentials, waits, and final-route verification.

## Boundaries

Sixty browser actions and 120 decision requests bound a run. Up to 250 action candidates are retained; truncated candidates cannot be selected. The service stays loopback-only, serializes inspector actions, and checks Host, Origin, and a local request token. Credentials remain server-side. Tabs share the existing Chrome profile.

The policy is generic, but two websites do not establish broad reliability. Name resolution covers common labels, ARIA references, and text; it is not the browser's full accessibility algorithm. Shadow roots, frames, canvas, uploads, nested scrolling, pop-ups, and complex keyboard interactions can block progress. A valid action can still be wrong. Independent checks, rather than the model's DONE choice, determine whether the demonstrated task succeeded.
