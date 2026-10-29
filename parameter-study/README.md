# Parameter study runner

`run_study.py` runs a complete, methodical parameter study for **one Sushi Go MCTS player**: you give it the player's package name. It does the following unattended:

1. **Stage 1:** sweeps the player's three main parameters, the exploration constant **K**, **max tree depth** and **rollout length**, at **100 ms** per decision. It changes one factor at a time around a fixed reference point.
2. **Selection:** picks the **top 3** stage-1 configurations automatically, by win rate.
3. **Stage 2:** reruns those 3 at the competition's **500 ms** per decision, with fresh games.
4. **Analysis:** writes a report with confidence intervals and paired significance tests.

```bash
python3 parameter-study/run_study.py sushiGoDecoupled
```

---

## 1. What you need

| Requirement | Notes |
|---|---|
| **The repository**, on the same branch | The script compiles the player straight from `src/main/java`, so it always tests the code as it is now. |
| **Python 3.8+** | Only the standard library is used. No `pip install` is needed. |
| **JDK 21 or newer** (`java` **and** `javac`) | It must be a JDK, not just a JRE. It's found through `JAVA_HOME` if set, otherwise on `PATH`. Check with `javac -version`. |
| **The project's dependency jars in the local Maven cache** | Any machine where the repo already builds and runs (e.g. imported in IntelliJ) has these in `~/.m2/repository`. |

**Maven is not needed, and nothing is downloaded.** `maven-dependencies.txt` lists the exact jar files the project uses, as paths inside the Maven cache. The script finds them in `~/.m2/repository` (or the folder given with `--m2`) and compiles with plain `javac`. If any jar is missing, it lists them and stops before doing anything else.

> If the repo runs in IntelliJ but the script reports missing jars, IntelliJ may be using a different Maven cache. Find it under *Settings → Build Tools → Maven → Local repository* and pass that folder with `--m2`.

## 2. Files in this folder

| File | Purpose |
|---|---|
| `run_study.py` | The whole study: checks, compilation, configs, running, selection and analysis. |
| `maven-dependencies.txt` | The project's dependency jars, as paths relative to the Maven cache. These were generated from `pom.xml` on this branch. If `pom.xml` changes, regenerate it (see section 9). |
| `README.md` | This file. |
| `.gitignore` | Keeps the compiled `build/` folder out of git. |
| `build/` | *(created)* The compiled classes. Rebuilt on every run unless `--no-compile` is used. |
| `results/<package>/` | *(created)* Everything a study produces (see section 6). |

The script also uses these folders that already exist in the repository: `src/main/java/` (the code), `metrics/` (TAG loads its metric definitions from here) and `data/` (game data). The script always runs TAG from the repository root, so these are found.

## 3. Quick start

All commands are run from the **repository root**.

```bash
# 1. ~3 minutes: compile and play 24 games, to check everything works on this machine
python3 parameter-study/run_study.py sushiGoDecoupled --stage smoke

# 2. ~7 hours: the full study (stage 1, selection, stage 2, report)
python3 parameter-study/run_study.py sushiGoDecoupled
```

When it finishes, open `parameter-study/results/sushiGoDecoupled/report.html` in a browser. Print it to PDF from there if needed.

**Keep the computer awake and plugged in.** On macOS the script does this for you through `caffeinate`. On Windows or Linux, turn off sleep in the power settings for the night.

## 4. The study design

### 4.1 The table (identical in every run)
- 4-player Sushi Go, with **the player under test + BasicMCTS × 2 + Random**.
- **BasicMCTS** uses TAG's own default parameters (K √2, rollout 10, max depth 100, the game's default heuristic), so the baseline is not hand-tuned. It gets the same time budget as the player under test.
- **Random** plays a uniformly random legal move.
- BasicMCTS fills two seats because TAG's exhaustive mode needs at least as many agents as seats.
- **Exhaustive seating:** all 24 seat orders are played equally often. **504 games per run** (21 × 24).
- **Paired design:** within a stage, every run uses the same seed and the same file names. So every run plays **exactly the same deals in the same seatings**, and runs are compared game by game.

### 4.2 Stage 1: one factor at a time, 100 ms

The reference point is fixed in advance from the game's structure. In TAG each player acts in turn, so 1 trick = 4 actions and 1 round = 8 tricks = 32 actions.

| Run | K | Max depth | Rollout | Varies |
|---|---|---|---|---|
| K1 | **1** | 8 | 32 | K |
| K5 | **5** | 8 | 32 | K |
| **REF** | **10** | **8** (2 tricks) | **32** (1 round) | (reference) |
| K20 | **20** | 8 | 32 | K |
| D4 | 10 | **4** (1 trick) | 32 | depth |
| D16 | 10 | **16** (4 tricks) | 32 | depth |
| R16 | 10 | 8 | **16** (½ round) | rollout |
| R64 | 10 | 8 | **64** (2 rounds) | rollout |

Depth and rollout use three levels that double each time (×½, ×1, ×2), and REF is the shared middle point of every sweep.

### 4.3 Selection
The **3 runs with the highest win rate** in stage 1 go to stage 2. Ties are broken by mean score. The script makes this choice, not the author. The result is saved in `selection.json`.

### 4.4 Stage 2: 500 ms
The 3 selected configurations, 504 games each, at **500 ms** per decision, with a **new seed**. The stage-2 games are therefore independent of the games used to pick them, which avoids the "winner's curse". The 3 runs share that seed, so they are paired with each other.

### 4.5 Metrics and tests
All metrics are from the point of view of the player under test:

| Metric | Meaning |
|---|---|
| **Win rate** (primary) | Share of games finished 1st, with a shared 1st place splitting the win. Shown with a 95% Wilson confidence interval. |
| Mean score | Final score |
| Mean placing | 1 = best, 4 = worst |
| Margin | Own final score minus the better BasicMCTS's score in the same game |

- **Stage 1:** every run is compared with REF on the per-game difference in wins, paired by game, using a two-sided z-test. The p-values are **Holm-corrected** across the 7 comparisons.
- **Stage 2:** all 3 pairs are compared the same way, with Holm correction across 3.
- A `*` marks results that stay significant (p < 0.05) after correction.

## 5. Running time

A game takes about 24 decisions × 3 MCTS players × the budget per decision.

| Stage | Runs | Per run | In parallel | Wall-clock |
|---|---|---|---|---|
| Stage 1 (100 ms) | 8 | ~1 h | 4 (`--parallel1`) | **~2 h** |
| Stage 2 (500 ms) | 3 | ~5 h | 3 (`--parallel2`) | **~5 h** |
| **Total** | | | | **~7 h** |

Every run is a separate Java process using about 1 CPU core. **Don't run more in parallel than the machine has free cores.** Budgets are wall-clock time, so an overloaded CPU means fewer MCTS iterations per decision. Every run in a stage uses the same level of parallelism, so conditions stay equal within a stage.

## 6. Output (`parameter-study/results/<package>/`)

| File / folder | Contents |
|---|---|
| `progress.txt` | Timestamped log: start and end of every run, the selection, any failures |
| `report.html` | **The report**: all tables, ready to view or print to PDF |
| `results.md` | The same report in Markdown |
| `results.csv` | One row per run with every metric, for plots and tables in the paper |
| `selection.json` | The configurations promoted to stage 2, and the rule used |
| `study.json` | Exactly what was run: package, parameter class, heuristic, every option |
| `stage1/<run>/`, `stage2/<run>/` | Per run: `players/*.json` (agent configs), `config.json` (TAG tournament config), `params.json`, `log.txt` (TAG output) and `results/` (TAG output, including `GAME_OVER.csv`, one row per game) |

## 7. Command reference

```
python3 parameter-study/run_study.py PACKAGE [options]
```

**`PACKAGE`** is the folder under `src/main/java/players/`, e.g. `sushiGoDecoupled`, `sushiGoCardCountingAgent` or `sushiGoSushiHeuristicAgent`. The script finds the class in it that extends `PlayerParameters`. It checks that the class has tunable `K`, `rolloutLength` and `maxTreeDepth` parameters, and that `instantiate()` passes `this.copy()` to the player. Without that, the JSON settings would be silently ignored.

| Option | Default | Meaning |
|---|---|---|
| `--stage` | `all` | `smoke` = 24-game check · `stage1` = stage 1 only · `stage2` = selection + stage 2 (needs stage 1) · `analyse` = rebuild the report only · `all` = everything |
| `--heuristic` | `players.heuristics.ScoreHeuristic` | Heuristic class for the player under test, or `default` to keep the package's own default |
| `--param NAME=VALUE` | none | Extra parameter for the player under test, read as JSON. Can be repeated, e.g. `--param restrictToCurrentRound=true` |
| `--ref-k` / `--ref-depth` / `--ref-rollout` | 10 / 8 / 32 | The reference point |
| `--k-values` | `1 5 20` | Other K levels |
| `--depth-values` | `4 16` | Other depth levels |
| `--rollout-values` | `16 64` | Other rollout levels |
| `--games` | 504 | Games per run, a multiple of 24 |
| `--budget1` / `--budget2` | 100 / 500 | ms per decision in stage 1 / stage 2 |
| `--top` | 3 | Configurations promoted to stage 2 |
| `--seed1` / `--seed2` | 1001 / 2001 | Master seed for stage 1 / stage 2 |
| `--parallel1` / `--parallel2` | 4 / 3 | Runs at the same time in stage 1 / stage 2 |
| `--out` | `parameter-study/results/<package>` | Output folder |
| `--m2` | `~/.m2/repository` | Local Maven cache that holds the dependency jars |
| `--repo` | parent of this folder | Repository root |
| `--no-compile` | off | Reuse `build/classes` from the last run instead of recompiling |

### Examples

```bash
# The decoupled UCT player, default design
python3 parameter-study/run_study.py sushiGoDecoupled

# The card-counting player with its own heuristic and round-limited rollouts
python3 parameter-study/run_study.py sushiGoCardCountingAgent \
    --heuristic players.heuristics.CountingCardsHeuristic --param restrictToCurrentRound=true

# The SushiGoHeuristic player. Its heuristic returns values in [-1, 1] rather than raw points,
# so K needs a correspondingly smaller scale
python3 parameter-study/run_study.py sushiGoSushiHeuristicAgent \
    --heuristic players.heuristics.SushiGoHeuristic --ref-k 1 --k-values 0.1 0.5 2

# Only 2 cores free: run fewer at a time (takes longer)
python3 parameter-study/run_study.py sushiGoDecoupled --parallel1 2 --parallel2 2

# Run stage 1 tonight, stage 2 another night
python3 parameter-study/run_study.py sushiGoDecoupled --stage stage1
python3 parameter-study/run_study.py sushiGoDecoupled --stage stage2
```

Studies of different packages go to different output folders and can coexist. **Run only one study at a time**, though, because they share CPU and `build/`.

## 8. Interruptions and reruns

- TAG writes a run's results only when the run **finishes**. Interrupting a run loses the games already played in that run.
- **Rerunning the same command resumes.** Runs that already have all their games are skipped, and only missing or failed runs are played again.
- To start a study from scratch, delete `parameter-study/results/<package>/`.
- If a run fails, the script stops with a message. That run's `log.txt` has the Java error.

## 9. Troubleshooting

| Message | Fix |
|---|---|
| `'javac' not found` | Install a JDK 21+, or set `JAVA_HOME` to it. A JRE alone has no `javac`. |
| `N of 311 dependency jars are not in the Maven cache` | Build the project once on this machine (e.g. let IntelliJ import the Maven project), or pass the right cache with `--m2`. |
| `compilation failed` | The code on this branch doesn't compile. The message shows the errors. |
| `expected exactly one class extending PlayerParameters` | The package has none, or more than one, parameters class. |
| `instantiate() does not pass this.copy()` | Fix the player's parameters class: `return new <Player>((<Params>) this.copy());` |
| `does not look like the TAG repository` | Run from the repository root, or pass `--repo`. |

**If `pom.xml` changes** (dependencies added or updated), regenerate the jar list on a machine that has Maven:

```bash
mvn -q dependency:build-classpath -Dmdep.outputFile=/tmp/cp.txt
tr ':' '\n' < /tmp/cp.txt | sed 's|^.*/\.m2/repository/||' > parameter-study/maven-dependencies.txt
```

## 10. Limitations to report

- **One factor at a time** does not capture interactions between parameters, for example a different best K at a different depth.
- **Wall-clock budgets** depend on CPU load. This is mitigated by using the same parallelism for every run in a stage.
- **Two seats hold the same BasicMCTS configuration.** The results measure strength against TAG-default BasicMCTS and Random only.
