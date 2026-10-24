# talp-aisle-env

The smallest working simulation for the TALP Simulation Hackathon, client **Aisle** (online marketplace).
36 LLM personas (20 shoppers, 8 small sellers, 8 large sellers) live through ten weeks; each week every persona
reads what it can see and the model writes what it does and its status. Output is the TALP run layout.

## Files

| File | What it is |
|---|---|
| `sim.py` | one run: prompts, ten weeks, check-in questions, writes `runs/<name>/` |
| `llm.py` | `ask(prompt, seed, model)`: Claude (tests), Gemini via `TALP_KEY` (hand-in runs), `fake` (no network) |
| `world.py` | the world layer, no LLM: decision rules, company variables week by week, `world_weekly.csv` |
| `world.json` | company variables: `value`, `kind` (fixed / decision / weekly), `source` (dossier sentence or `ours: reason`) |
| `test_world.py` | every menu action moves its variable for its weeks; budget; strike backlog; same inputs, same world |
| `metrics.py` | persona behaviour -> board goals 1-4, weighted by how many people each persona stands for |
| `aisle.json` | the dossier as data: statuses, weeks, known crisis, business as usual, action texts, check-in questions |
| `personas.jsonl` | personas: `id`, `group`, `name`, `weight`, `traits`, `story` |
| `decisions/plan.json` | an example plan (decision 1 has three actions); `decisions/none.json` is empty |
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

Each persona runs its ten weeks in its own thread (personas do not see each other): a full run with
check-ins takes about 30 s on Haiku. Answers are cached in `.cache/` by model, seed and prompt, so a rerun is
instant and a changed persona only asks again where its prompts changed.

Hand-in runs use Gemini: `export TALP_KEY=...` and `--model gemini-3.5-flash-lite`. Keys stay in the environment,
never in code or runs. `SPEND_LOG=<file>` appends one line per real call (model, tokens, $).

## Decisions

At most two decisions, each with 1-3 actions from the menu at a listed level (`discount` also needs `category`),
a message of 1-600 characters, channels from email / push / in-app banner / social media, and a target.
Both together must fit $2.5M. `sim.py` refuses a decision that breaks a rule and says which one.

| Action | Variable it changes | For |
|---|---|---|
| discount | `discount` | 1 week |
| free_shipping | `free_ship_threshold` | 2 weeks |
| extra_capacity | `spare_capacity`: Meridian and Dash take Rapid Parcel's backlog | 2 weeks |
| delivery_promise | `checkout_promise` | to week 10 |
| seller_check | `seller_audit` | to week 10 |
| authenticity_guarantee | `fake_claims` | 8 weeks |
| free_returns | `return_shipping` | 4 weeks |
| faster_payouts | `payout_freq` | 6 weeks |
| extra_support | `complaint_sla` (24h) | 4 weeks |
| ads | `ads` | 2 weeks |

## The world, week by week (`world.py`, deterministic)

Before each week: decisions in force set the variables, the budget is spent in the decision's week, the strike
moves (`announcements.json` `"set": {"strike_state": "on"}`, or `--strike on|off` for tests). Every persona
reads Aisle's terms and how fast parcels move. After the week: orders x weight x $60 = sales; Rapid Parcel
ships what its capacity allows (half while on strike), extra capacity takes the rest, what is left waits as
backlog and lengthens next week's delivery. One row per week in `runs/<name>/world_weekly.csv`.
Parcel counts are scaled so week 1 matches the plan (personas' own order counts run high; sales are not scaled).

## What a persona sees in week N

- its traits and a short story;
- the known crisis (Rapid Parcel strike warning) and every announcement up to week N
  (`plan_nosurprises` drops those with `"surprise": true`);
- what Aisle is doing: each locked action for its duration, or business as usual;
- Aisle's messages, word for word, only if the decision's target reaches its group
  (`everyone`, `shoppers`, `sellers`, `small sellers`, `large sellers`);
- Aisle's terms this week and parcel speed by carrier (from the world layer);
- its own last three weeks (status and a one-line note it wrote).

It answers in its own words and ends with `Orders`, `Returned` (shoppers) or `Sales` (sellers), `Complained`,
`Note` and `Status`. A status outside the dossier's list is asked again with a reminder (up to three tries).
