# talp-aisle-env

The smallest working simulation for the TALP Simulation Hackathon, client **Aisle** (online marketplace).
36 LLM personas (20 shoppers, 8 small sellers, 8 large sellers) live through ten weeks; each week every persona
reads what it can see and the model writes what it does and its status. Output is the TALP run layout.

## Files

| File | What it is |
|---|---|
| `sim.py` | one run: prompts, ten weeks, check-in questions, writes `runs/<name>/` |
| `llm.py` | `ask(prompt, seed, model)`: Claude (tests), Gemini via `TALP_KEY` (hand-in runs), `fake` (no network) |
| `metrics.py` | persona behaviour -> board goals 1-4, weighted by how many people each persona stands for |
| `aisle.json` | the dossier as data: statuses, weeks, known crisis, business as usual, action texts, check-in questions |
| `personas.jsonl` | personas: `id`, `group`, `name`, `weight`, `traits`, `story` |
| `decisions/plan.json` | an example plan (two decisions, TALP format); `decisions/none.json` is empty |
| `announcements.json` | crises announced on the dashboard: `[{"id", "week", "title", "text", "surprise": true}]` |

## Run

```bash
pip install anthropic google-genai
export ANTHROPIC_KEY_FILE=~/anthropic.key          # or ANTHROPIC_API_KEY
python sim.py --type plan --seed 1 --probe         # runs/plan_1
python sim.py --type nodecision --seed 1 --probe   # runs/nodecision_1
python sim.py --type plan --seed 1 --limit 4       # quick test, four personas
python sim.py --type plan --seed 1 --model fake    # no network, checks the pipeline
```

Hand-in runs use Gemini: `export TALP_KEY=...` and `--model gemini-3.5-flash-lite`. Keys stay in the environment,
never in code or runs. `SPEND_LOG=<file>` appends one line per real call (model, tokens, $).

## What a persona sees in week N

- its traits and a short story;
- the known crisis (Rapid Parcel strike warning) and every announcement up to week N
  (`plan_nosurprises` drops those with `"surprise": true`);
- what Aisle is doing: each locked action for its duration, or business as usual;
- Aisle's messages, word for word, only if the decision's target reaches its group
  (`everyone`, `shoppers`, `sellers`, `small sellers`, `large sellers`);
- its own last three weeks (status and a one-line note it wrote).

It answers in its own words and ends with `Orders`, `Returned` (shoppers) or `Sales` (sellers), `Complained`,
`Note` and `Status`. A status outside the dossier's list is asked again with a reminder (up to three tries).
