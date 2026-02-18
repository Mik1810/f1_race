# F1 Race — DALI Multi-Agent Simulation

A Formula 1 race simulation built with the **DALI Multi-Agent System** framework.  
Four reactive agents communicate through the LINDA blackboard. The **Pit Wall** coordinates the race flow and generates **probabilistic events** (safety car, rain) automatically — no user injection required.

---

## Agents

| Agent | Instance | Type | Ruolo |
|-------|----------|------|-------|
| `ferrari` | `mas/instances/ferrari.txt` | `ferrariCar` | Auto Ferrari SF-24 |
| `mclaren` | `mas/instances/mclaren.txt` | `mclarenCar` | Auto McLaren MCL38 |
| `pitwall` | `mas/instances/pitwall.txt` | `pitWallType` | Muretto box, coordinatore e generatore di eventi casuali |
| `safety_car` | `mas/instances/safety_car.txt` | `safetyCarType` | Safety car |

### Race flow (event chain — gara normale)

```
User ──send_message(start_race)──► Ferrari
                                       │ lap1_ferrari
                                       ▼
                                    Pitwall ──[50% Safety Car!]──► Safety Car
                                       │ lap1_start                    │ deploy → Ferrari + McLaren
                                       ▼                               ▼
                                    McLaren                    Ferrari/McLaren slow down
                                       │ lap2_mclaren
                                       ▼
                                    Pitwall ──[30% RAIN!]──► Ferrari + McLaren (rain_warning)
                                       │ lap3_start
                                       ▼
                                    Ferrari
                                       │ lap3_ferrari
                                       ▼
                                    Pitwall ──lap4_start──► McLaren
                                                                │ lap4_mclaren
                                                                ▼
                                    Pitwall ──final_lap──► Ferrari
                                                                │ finish_ferrari
                                                                ▼
                                    Pitwall  ✓ RACE OVER! Ferrari P1!
```

### Probabilistic Events (automatic, no user needed)

| Trigger | Probability | Effect |
|---|---|---|
| After lap 1 (ferrari reports to pitwall) | **50%** | Safety car deployed — SC notifies Ferrari + McLaren |
| After lap 2 (mclaren reports to pitwall) | **30%** | Heavy rain — both cars receive `rain_warning` |

These are implemented in `pitWallType.txt` using SICStus `random/3`:
```prolog
lap1_ferrariE:> ..., random(0, 10, R1), if(R1 < 5, send_m(safety_car, send_message(deploy, pitwall)), true), messageA(mclaren, ...).
```
`send_m/2` is the internal DALI send predicate — safe to use inside `if/3` unlike `messageA`.

Each agent reacts to incoming events and fires the next event through the pitwall, simulating a 5-lap race with automatic probabilistic incidents.

---

## How to Run

### 1 — Start the MAS (WSL / Linux)

```bash
cd DALI/Examples/f1_race
bash startmas.sh
```

Requirements: `tmux`, `dos2unix`, SICStus Prolog 4.6.0 at `/usr/local/sicstus4.6.0`.

### 2 — Launch the Web Dashboard (recommended)

In a **second WSL terminal** (leave the MAS running):

```bash
cd DALI/Examples/f1_race
bash ui/run.sh
```

`run.sh` creates a local Python venv (`ui/.venv`) on first run and installs Flask automatically — no system-wide pip needed.

Then open **http://localhost:5000** in your browser.

The dashboard shows all 6 agent panes side-by-side, auto-scrolling in real time.  
Use the toolbar buttons to control the race — no tmux scrolling needed.

### 3 — Start the Race

Click **▶ Start Race** in the dashboard header, or in the User Console pane type:

```prolog
ferrari.
user.
send_message(start_race, user).
```

The race runs automatically. The pit wall randomly triggers the safety car (50% chance) after lap 1, and rain warnings (30% chance) after lap 2.

---

## Dashboard Features

| UI element | Function |
|---|---|
| **▶ Start Race** | Sends `start_race` to ferrari via the user agent |
| **⚠ Deploy SC** | Deploys the safety car immediately |
| **✓ Recall SC** | Recalls the safety car |
| **Agent: / Command:** bar | Send any arbitrary Prolog command to any agent pane |
| ↓ pin button (top-right of each pane) | Toggle auto-scroll for that pane |

---

## Shutdown

```bash
tmux kill-session -t f1_race   # stop the MAS
# Ctrl+C in the dashboard terminal to stop Flask
```

---

## Project Structure

```
f1_race/
├── startmas.sh          # Launch script for Linux/WSL
├── ui/
│   ├── dashboard.py     # Web dashboard (Flask, polls tmux panes)
│   ├── run.sh           # Wrapper: creates venv + launches dashboard
│   └── requirements.txt # pip: flask
├── mas/
│   ├── instances/
│   │   ├── ferrari.txt      # → ferrariCar
│   │   ├── mclaren.txt      # → mclarenCar
│   │   ├── pitwall.txt      # → pitWallType
│   │   └── safety_car.txt   # → safetyCarType
│   └── types/
│       ├── ferrariCar.txt   # Ferrari DALI logic
│       ├── mclarenCar.txt   # McLaren DALI logic
│       ├── pitWallType.txt  # Pit wall coordinator + random event generator
│       └── safetyCarType.txt # Safety car logic
├── conf/
│   ├── communication.con    # FIPA communication policy
│   ├── makeconf.sh / .bat   # Generates agent config files
│   └── startagent.sh / .bat # Starts a single agent
├── build/               # Runtime: merged type+instance files (auto-generated)
├── work/                # Runtime: compiled agent files (auto-generated)
└── log/                 # Runtime: agent logs (auto-generated)
```

---

## DALI Syntax Reference (used in this project)

| Syntax | Meaning | Example |
|--------|---------|---------|
| `nameE:> Body.` | React to external event `name` | `start_raceE:> write('Go!').` |
| `messageA(agent, msg)` | Send a message (top-level only) | `messageA(mclaren, send_message(lap_done, ferrari)).` |
| `send_m(agent, msg)` | Send a message (safe inside `if/3`) | `send_m(safety_car, send_message(deploy, pitwall)).` |
| `random(Low, High, R)` | Random integer `Low =< R < High` | `random(0, 10, R).` |
| `if(Cond, Then, Else)` | Conditional | `if(R < 5, send_m(...), true).` |
| `:- Goal.` | Directive (runs at load time) | `:- write('Agent ready!').` |
