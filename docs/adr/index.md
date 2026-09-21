# Architecture decisions

Short records of decisions that were expensive to make and would be expensive to
reverse. Each states the forces at the time, what was chosen, what was rejected,
and what the choice costs — because a decision without its rejected alternatives
is folklore, and the next person will re-litigate it from scratch.

Format: a trimmed [MADR](https://adr.github.io/madr/). Records are immutable once
accepted; a decision that changes gets a new record that supersedes the old one,
and the old one stays readable.

| # | Decision | Status |
|---|---|---|
| [0001](0001-adopt-datapizza-ai.md) | Adopt `datapizza-ai` as the agent vocabulary | Accepted |
| [0002](0002-unify-the-agentic-loop.md) | Unify the agentic loop behind ports | Accepted |
| [0003](0003-offline-echo-backend.md) | Ship an offline scripted backend in the product | Accepted |
| [0004](0004-name-the-project-dogana.md) | Name the project Dogana | Superseded by 0005 |
| [0005](0005-name-the-project-annona.md) | Name the project Annona | Accepted |
| [0006](0006-runner-link.md) | Link a runner to Agents Studio: outbound only, its own credential | Accepted |
| [0007](0007-release-bound-to-endpoint.md) | Bind a higher release ceiling to the Studio it is for | Accepted |
| [0008](0008-skills-installed-on-request.md) | Skills installed on request: the policy pre-approves, Studio only says when | Accepted |

## Writing one

Add a record when a decision meets any of these:

- it constrains what the code can become (layering, dependencies, protocols);
- it was contested, or the losing option was reasonable;
- it will look wrong to someone who was not in the room.

Skip one when the choice is local, cheap to reverse, and obvious from the code.

```
docs/adr/NNNN-short-title.md
```
