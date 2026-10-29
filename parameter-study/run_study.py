#!/usr/bin/env python3
"""Methodical parameter study for one Sushi Go MCTS player package (TAG framework).

    python3 parameter-study/run_study.py sushiGoDecoupled

Stage 1  one-factor-at-a-time sweep of K, max tree depth and rollout length around a fixed
         reference, at 100 ms per decision (8 runs by default).
Select   the top 3 stage-1 runs by win rate (ties: mean score), chosen automatically.
Stage 2  those 3 configurations again at 500 ms per decision, with a new seed.
Analyse  win rate with 95% Wilson CI, mean score, mean placing, margin over the best
         BasicMCTS, paired comparisons (vs REF in stage 1, pairwise in stage 2), Holm correction.

Every run uses the same table: the player under test + BasicMCTS x2 (TAG defaults) + Random,
4-player Sushi Go, exhaustive seating. Within a stage every run uses the same seed and the same
file names, so all runs play identical deals in identical seatings (paired design).

Needs only Python 3.8+ and a JDK 21+ (java + javac). No Maven: the dependency jars are taken from
the local Maven cache (~/.m2/repository) that already exists on any machine that has built the repo,
using the list in maven-dependencies.txt. Nothing is downloaded. See README.md.
"""
import argparse
import csv
import datetime
import glob
import html
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = os.path.dirname(HERE)
GAMES_PER_CYCLE = 24  # 4! seat orders in a 4-player exhaustive tournament
LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------------- logging

class Log:
    def __init__(self, path):
        self.path = path

    def __call__(self, msg):
        line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        with LOG_LOCK:
            print(line, flush=True)
            if self.path:
                with open(self.path, "a") as f:
                    f.write(line + "\n")


def fail(msg):
    sys.exit(f"\nERROR: {msg}\n")


# --------------------------------------------------------------------------- environment checks

def find_tool(name):
    """java / javac from JAVA_HOME if set, otherwise from PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    java_home = os.environ.get("JAVA_HOME")
    if java_home and os.path.isfile(os.path.join(java_home, "bin", exe)):
        return os.path.join(java_home, "bin", exe)
    found = shutil.which(name)
    if not found:
        fail(f"'{name}' not found. Install a JDK 21+ and put it on PATH, or set JAVA_HOME.")
    return found


def java_major_version(javac):
    out = subprocess.run([javac, "-version"], capture_output=True, text=True)
    m = re.search(r"(\d+)(?:\.(\d+))?", out.stdout + out.stderr)
    if not m:
        fail(f"could not read the javac version from: {out.stdout + out.stderr!r}")
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 else major  # "1.8" style -> 8


def check_repo(repo):
    for p in ("pom.xml", "src/main/java/evaluation/RunGames.java", "metrics"):
        if not os.path.exists(os.path.join(repo, p)):
            fail(f"{repo} does not look like the TAG repository (missing {p}). Use --repo.")


def build_classpath(m2, deps_file, log):
    with open(deps_file) as f:
        rel = [line.strip() for line in f if line.strip()]
    jars = [os.path.join(m2, *r.split("/")) for r in rel]
    missing = [j for j in jars if not os.path.isfile(j)]
    if missing:
        shown = "\n  ".join(missing[:15]) + (f"\n  ... and {len(missing) - 15} more" if len(missing) > 15 else "")
        fail(f"{len(missing)} of {len(jars)} dependency jars are not in the Maven cache {m2}:\n  {shown}\n"
             "Build the project once on this machine (e.g. open it in IntelliJ and let it import the Maven "
             "project, or run a Maven build) so the jars are cached, or point --m2 at the right folder.")
    log(f"classpath: all {len(jars)} dependency jars found in {m2}")
    return jars


def compile_sources(repo, build_dir, jars, javac, log):
    """Compiles all of src/main/java with plain javac (what 'mvn compile' would produce)."""
    src_root = os.path.join(repo, "src", "main", "java")
    sources = [os.path.join(d, f) for d, _, fs in os.walk(src_root) for f in fs if f.endswith(".java")]
    classes = os.path.join(build_dir, "classes")
    shutil.rmtree(classes, ignore_errors=True)
    os.makedirs(classes)
    # an argument file avoids command-line length limits (Windows) with ~thousands of files
    argfile = os.path.join(build_dir, "javac-args.txt")
    quote = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    with open(argfile, "w") as f:
        f.write("\n".join(["-proc:none", "-nowarn", "-encoding", "UTF-8", "--release", "21",
                           "-d", quote(classes), "-cp", quote(os.pathsep.join(jars))]
                          + [quote(s) for s in sources]))
    log(f"compiling {len(sources)} source files with javac (about 1-2 minutes) ...")
    res = subprocess.run([javac, "@" + argfile], capture_output=True, text=True)
    if res.returncode != 0:
        errors = "\n".join(l for l in (res.stdout + res.stderr).splitlines() if "error" in l.lower())[:4000]
        fail(f"compilation failed:\n{errors}")
    log("compiled OK")
    return classes


# --------------------------------------------------------------------------- the player package

def find_params_class(repo, package):
    """Finds the PlayerParameters subclass in src/main/java/players/<package>/ and checks its tunables."""
    pkg_dir = os.path.join(repo, "src", "main", "java", "players", package)
    if not os.path.isdir(pkg_dir):
        options = sorted(d for d in os.listdir(os.path.join(repo, "src", "main", "java", "players"))
                         if os.path.isdir(os.path.join(repo, "src", "main", "java", "players", d)))
        fail(f"package folder {pkg_dir} not found. Available player packages:\n  " + "\n  ".join(options))
    candidates = []
    for f in sorted(glob.glob(os.path.join(pkg_dir, "*.java"))):
        text = open(f, encoding="utf-8").read()
        m = re.search(r"public\s+class\s+(\w+)\s+extends\s+PlayerParameters\b", text)
        if m:
            candidates.append((m.group(1), text))
    if len(candidates) != 1:
        fail(f"expected exactly one class extending PlayerParameters in {pkg_dir}, found "
             f"{[c[0] for c in candidates] or 'none'}")
    name, text = candidates[0]
    for p in ("K", "rolloutLength", "maxTreeDepth"):
        if f'addTunableParameter("{p}"' not in text:
            fail(f"{name} has no tunable parameter '{p}', so this study cannot vary it.")
    if not re.search(r"instantiate\(\)\s*\{[^}]*this\.copy\(\)", text, re.S):
        fail(f"{name}.instantiate() does not pass this.copy() to the player, so the JSON settings would be "
             f"ignored. It should be: return new <Player>((<{name}>) this.copy());")
    return f"players.{package}.{name}", ('addTunableParameter("restrictToCurrentRound"' in text)


# --------------------------------------------------------------------------- study design

def run_grid(a):
    """Stage-1 runs: name -> (K, depth, rollout). One factor varies at a time around REF."""
    ref = (a.ref_k, a.ref_depth, a.ref_rollout)
    runs = {}
    for k in sorted(set(a.k_values) | {a.ref_k}):
        runs["REF" if k == a.ref_k else f"K{k:g}"] = (k, a.ref_depth, a.ref_rollout)
    for d in sorted(a.depth_values):
        runs[f"D{d}"] = (a.ref_k, d, a.ref_rollout)
    for r in sorted(a.rollout_values):
        runs[f"R{r}"] = (a.ref_k, a.ref_depth, r)
    assert runs["REF"] == ref
    return runs


def candidate_json(a, params_class, k, depth, rollout, budget):
    d = {"class": params_class, "K": float(k), "rolloutLength": rollout, "maxTreeDepth": depth}
    d.update(a.extra_params)
    if a.heuristic != "default":
        d["heuristic"] = {"class": a.heuristic}
    d.update({"budgetType": "BUDGET_TIME", "budget": budget})
    return d


def basic_mcts_json(budget):
    # TAG's BasicMCTSParams defaults: K sqrt(2), rollout 10, max depth 100, game's default heuristic
    return {"class": "players.basicMCTS.BasicMCTSParams", "K": math.sqrt(2), "rolloutLength": 10,
            "maxTreeDepth": 100, "budgetType": "BUDGET_TIME", "budget": budget}


def write_run(a, run_dir, params_class, k, depth, rollout, budget, games, seed):
    os.makedirs(os.path.join(run_dir, "players"), exist_ok=True)
    players = {a.package: candidate_json(a, params_class, k, depth, rollout, budget),
               "BasicMCTS_a": basic_mcts_json(budget), "BasicMCTS_b": basic_mcts_json(budget),
               "Random": {"class": "players.simple.RandomPlayer"}}
    for name, cfg in players.items():
        with open(os.path.join(run_dir, "players", name + ".json"), "w") as f:
            json.dump(cfg, f, indent=2)
    config = {"game": "SushiGo", "nPlayers": 4, "mode": "exhaustive", "matchups": games, "verbose": False,
              "seed": seed, "playerDirectory": os.path.join(run_dir, "players"),
              "destDir": os.path.join(run_dir, "results")}
    with open(os.path.join(run_dir, "config.json"), "w") as f:
        json.dump(config, f, indent=2)
    with open(os.path.join(run_dir, "params.json"), "w") as f:
        json.dump({"K": k, "maxTreeDepth": depth, "rolloutLength": rollout, "budget_ms": budget,
                   "games": games, "seed": seed}, f, indent=2)


def completed_games(run_dir):
    p = os.path.join(run_dir, "results", "GAME_OVER.csv")
    if not os.path.isfile(p):
        return 0
    with open(p) as f:
        return max(0, sum(1 for _ in f) - 1)


def run_tournament(run_dir, games, java, classpath, repo, log, label):
    if completed_games(run_dir) == games:
        log(f"skip  {label} (already complete: {games} games)")
        return True
    shutil.rmtree(os.path.join(run_dir, "results"), ignore_errors=True)
    log(f"start {label}")
    with open(os.path.join(run_dir, "log.txt"), "w") as out:
        rc = subprocess.run([java, "-cp", classpath, "evaluation.RunGames",
                             "config=" + os.path.join(run_dir, "config.json")],
                            cwd=repo, stdout=out, stderr=subprocess.STDOUT).returncode
    n = completed_games(run_dir)
    ok = rc == 0 and n == games
    log(f"{'end  ' if ok else 'FAIL '} {label} (exit {rc}, {n}/{games} games)")
    return ok


def run_many(jobs, parallel, java, classpath, repo, log):
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(lambda j: run_tournament(j[0], j[1], java, classpath, repo, log, j[2]), jobs))
    return all(results)


# --------------------------------------------------------------------------- analysis

def load_run(run_dir, candidate):
    games = {}
    with open(os.path.join(run_dir, "results", "GAME_OVER.csv")) as f:
        for row in csv.DictReader(f):
            n = int(row["PlayerCount"])
            names = [row[f"OrdinalPosition(PlayerName-{i})"] for i in range(n)]
            ranks = [int(row[f"OrdinalPosition(Player-{i})"]) for i in range(n)]
            scores = [float(row[f"FinalScore(Player-{i})"]) for i in range(n)]
            c = names.index(candidate)
            winners = [i for i in range(n) if ranks[i] == 1]
            best_basic = max(scores[i] for i in range(n) if names[i].startswith("BasicMCTS"))
            key = (row["GameSeed"], tuple(names))
            games[key] = {"win": (1 / len(winners)) if c in winners else 0.0, "rank": ranks[c],
                          "score": scores[c], "margin": scores[c] - best_basic}
    return games


def mean_se(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return m, sd / math.sqrt(n)


def wilson(p, n, z=1.96):
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    adj, running = [0.0] * len(pvals), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvals) - rank) * pvals[i]))
        adj[i] = running
    return adj


def summarise(games):
    g = list(games.values())
    w = sum(x["win"] for x in g) / len(g)
    lo, hi = wilson(w, len(g))
    return {"n": len(g), "win": w, "lo": lo, "hi": hi, "score": mean_se([x["score"] for x in g]),
            "rank": mean_se([x["rank"] for x in g]), "margin": mean_se([x["margin"] for x in g])}


def paired(a, b):
    keys = sorted(set(a) & set(b))
    d, se = mean_se([a[k]["win"] - b[k]["win"] for k in keys])
    z = d / se if se > 0 else 0.0
    return len(keys), d, se, z, math.erfc(abs(z) / math.sqrt(2))


def load_stage(stage_dir, names, candidate):
    data = {}
    for n in names:
        d = os.path.join(stage_dir, n)
        if os.path.isfile(os.path.join(d, "results", "GAME_OVER.csv")):
            games = load_run(d, candidate)
            with open(os.path.join(d, "params.json")) as f:
                data[n] = {"games": games, "summary": summarise(games), "params": json.load(f)}
    return data


def top_n(data, n):
    return sorted(data, key=lambda r: (data[r]["summary"]["win"], data[r]["summary"]["score"][0]), reverse=True)[:n]


def run_table(data):
    out = ["| Run | K | Max depth | Rollout | Budget | Games | Win rate (95% CI) | Mean score | Mean placing | Margin vs best BasicMCTS |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for n, d in data.items():
        p, s = d["params"], d["summary"]
        out.append(f"| {n} | {p['K']:g} | {p['maxTreeDepth']} | {p['rolloutLength']} | {p['budget_ms']} ms | {s['n']} | "
                   f"{s['win']:.1%} ({s['lo']:.1%} – {s['hi']:.1%}) | {s['score'][0]:.1f} ± {s['score'][1]:.1f} | "
                   f"{s['rank'][0]:.2f} ± {s['rank'][1]:.2f} | {s['margin'][0]:+.1f} ± {s['margin'][1]:.1f} |")
    return out


def comparison_table(pairs, data):
    rows = [(a, b, *paired(data[a]["games"], data[b]["games"])) for a, b in pairs]
    if not rows:
        return []
    adj = holm([r[6] for r in rows])
    out = ["| Comparison | Paired games | Win-rate difference | SE | z | p | Holm p |", "|---|---|---|---|---|---|---|"]
    for (a, b, n, d, se, z, p), pa in zip(rows, adj):
        out.append(f"| {a} − {b} | {n} | {d * 100:+.1f} pts | {se * 100:.1f} | {z:+.2f} | {p:.4f} | "
                   f"{pa:.4f}{' *' if pa < 0.05 else ''} |")
    return out


def write_report(a, out_dir, stage1_names, stage2_names):
    s1 = load_stage(os.path.join(out_dir, "stage1"), stage1_names, a.package)
    s2 = load_stage(os.path.join(out_dir, "stage2"), stage2_names, a.package)
    md = [f"# Parameter study: `{a.package}`", "",
          f"Table: `{a.package}` + BasicMCTS × 2 (TAG defaults) + Random; 4-player Sushi Go; exhaustive seating; "
          f"{a.games} games per run. Heuristic: `{a.heuristic}`."
          + (f" Extra parameters: `{json.dumps(a.extra_params)}`." if a.extra_params else ""), "",
          f"Reference: K {a.ref_k:g}, max depth {a.ref_depth}, rollout {a.ref_rollout}. "
          "Win = finishing 1st (a shared 1st place splits the win). Comparisons are paired by game "
          "(same deal, same seating); * = significant after Holm correction (p < 0.05).", ""]
    if s1:
        md += [f"## Stage 1: {a.budget1} ms per decision", "", *run_table(s1), ""]
        pairs = [(n, "REF") for n in s1 if n != "REF"] if "REF" in s1 else []
        if pairs:
            md += ["### Each run vs REF", "", *comparison_table(pairs, s1), ""]
        if len(s1) == len(stage1_names):
            md += [f"**Selected for stage 2 (top {a.top} by win rate, ties by mean score):** "
                   f"{', '.join(top_n(s1, a.top))}", ""]
    if s2:
        md += [f"## Stage 2: {a.budget2} ms per decision", "", *run_table(s2), ""]
        if len(s2) > 1:
            md += ["### Pairwise", "", *comparison_table(list(itertools.combinations(list(s2), 2)), s2), ""]
    with open(os.path.join(out_dir, "results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(markdown_to_html(md, f"Parameter study: {a.package}"))
    with open(os.path.join(out_dir, "results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stage", "run", "K", "maxTreeDepth", "rolloutLength", "budget_ms", "games", "win_rate",
                    "win_ci_low", "win_ci_high", "mean_score", "mean_score_se", "mean_placing", "mean_placing_se",
                    "margin", "margin_se"])
        for stage, data in (("1", s1), ("2", s2)):
            for n, d in data.items():
                p, s = d["params"], d["summary"]
                w.writerow([stage, n, p["K"], p["maxTreeDepth"], p["rolloutLength"], p["budget_ms"], s["n"],
                            round(s["win"], 4), round(s["lo"], 4), round(s["hi"], 4), round(s["score"][0], 2),
                            round(s["score"][1], 2), round(s["rank"][0], 3), round(s["rank"][1], 3),
                            round(s["margin"][0], 2), round(s["margin"][1], 2)])
    return s1


def markdown_to_html(lines, title):
    def inline(s):
        s = html.escape(s, quote=False)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    body, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("#"):
            n = len(line) - len(line.lstrip("#"))
            body.append(f"<h{n}>{inline(line[n:].strip())}</h{n}>")
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            body.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in rows[0]) + "</tr>"
                        + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                                  for r in rows[2:]) + "</table>")
        else:
            body.append(f"<p>{inline(line)}</p>")
            i += 1
    css = ("body{font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45;margin:24px;"
           "color:#1f2328;background:#fff}table{border-collapse:collapse;margin:8px 0 16px}th,td{border:1px solid #d0d7de;"
           "padding:4px 8px;text-align:left}th{background:#f3f4f6}code{background:#f3f4f6;padding:0 3px}"
           "@media print{body{margin:0;font-size:11px}}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{css}</style></head><body>{''.join(body)}</body></html>")


# --------------------------------------------------------------------------- main

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("package", help="player package under src/main/java/players/, e.g. sushiGoDecoupled")
    p.add_argument("--stage", choices=["all", "smoke", "stage1", "stage2", "analyse"], default="all",
                   help="what to run (default: all = stage1, selection, stage2, analysis)")
    p.add_argument("--heuristic", default="players.heuristics.ScoreHeuristic",
                   help="heuristic class for the player under test, or 'default' to keep the package's own default")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                   help="extra parameter for the player under test, e.g. --param restrictToCurrentRound=true "
                        "(VALUE is read as JSON; repeatable)")
    g = p.add_argument_group("design (defaults = the pre-registered study)")
    g.add_argument("--ref-k", type=float, default=10.0)
    g.add_argument("--ref-depth", type=int, default=8, help="2 tricks of 4 actions")
    g.add_argument("--ref-rollout", type=int, default=32, help="1 round = 8 tricks x 4 actions")
    g.add_argument("--k-values", type=float, nargs="+", default=[1, 5, 20], help="K levels besides the reference")
    g.add_argument("--depth-values", type=int, nargs="+", default=[4, 16])
    g.add_argument("--rollout-values", type=int, nargs="+", default=[16, 64])
    g.add_argument("--games", type=int, default=504, help="games per run, a multiple of 24 (default 504)")
    g.add_argument("--budget1", type=int, default=100, help="stage-1 ms per decision (default 100)")
    g.add_argument("--budget2", type=int, default=500, help="stage-2 ms per decision (default 500)")
    g.add_argument("--top", type=int, default=3, help="configurations promoted to stage 2 (default 3)")
    g.add_argument("--seed1", type=int, default=1001)
    g.add_argument("--seed2", type=int, default=2001)
    r = p.add_argument_group("machine")
    r.add_argument("--parallel1", type=int, default=4, help="stage-1 runs at the same time (default 4)")
    r.add_argument("--parallel2", type=int, default=3, help="stage-2 runs at the same time (default 3)")
    r.add_argument("--repo", default=DEFAULT_REPO, help="TAG repository root (default: parent of this folder)")
    r.add_argument("--m2", default=os.path.join(os.path.expanduser("~"), ".m2", "repository"),
                   help="local Maven cache with the dependency jars (default ~/.m2/repository)")
    r.add_argument("--out", help="output folder (default parameter-study/results/<package>)")
    r.add_argument("--no-compile", action="store_true", help="reuse the classes from the last compile")
    a = p.parse_args()
    if a.games % GAMES_PER_CYCLE:
        p.error(f"--games must be a multiple of {GAMES_PER_CYCLE} so every seat order is played equally")
    try:
        a.extra_params = {k: json.loads(v) for k, v in (s.split("=", 1) for s in a.param)}
    except ValueError:
        p.error("--param must look like NAME=VALUE with a JSON value, e.g. restrictToCurrentRound=true")
    return a


def main():
    a = parse_args()
    repo = os.path.abspath(a.repo)
    check_repo(repo)
    out_dir = os.path.abspath(a.out or os.path.join(HERE, "results", a.package))
    os.makedirs(out_dir, exist_ok=True)
    log = Log(os.path.join(out_dir, "progress.txt"))
    log(f"=== {a.package}: stage '{a.stage}' -> {out_dir}")

    params_class, has_round_flag = find_params_class(repo, a.package)
    if "restrictToCurrentRound" in a.extra_params and not has_round_flag:
        fail(f"{params_class} has no restrictToCurrentRound parameter")
    log(f"player parameters class: {params_class}")
    stage1 = run_grid(a)
    with open(os.path.join(out_dir, "study.json"), "w") as f:
        json.dump({"package": a.package, "params_class": params_class, "heuristic": a.heuristic,
                   "extra_params": a.extra_params, "stage1_runs": stage1, "args": {k: v for k, v in vars(a).items()
                                                                                   if k != "extra_params"}}, f, indent=2)

    java = classpath = None
    if a.stage != "analyse":
        java, javac = find_tool("java"), find_tool("javac")
        if java_major_version(javac) < 21:
            fail("a JDK 21 or newer is needed (javac -version)")
        jars = build_classpath(os.path.expanduser(a.m2), os.path.join(HERE, "maven-dependencies.txt"), log)
        build_dir = os.path.join(HERE, "build")
        os.makedirs(build_dir, exist_ok=True)
        classes = os.path.join(build_dir, "classes")
        if not (a.no_compile and os.path.isdir(classes)):
            classes = compile_sources(repo, build_dir, jars, javac, log)
        classpath = os.pathsep.join([classes] + jars)
        if shutil.which("caffeinate"):  # macOS: keep the machine awake while this script runs
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

    if a.stage == "smoke":
        d = os.path.join(out_dir, "smoke")
        k, depth, rollout = stage1["REF"]
        write_run(a, d, params_class, k, depth, rollout, a.budget1, GAMES_PER_CYCLE, a.seed1)
        ok = run_tournament(d, GAMES_PER_CYCLE, java, classpath, repo, log, "smoke (REF, 24 games)")
        if ok:
            s = summarise(load_run(d, a.package))
            log(f"smoke OK: {a.package} won {s['win']:.0%} of 24 games, mean score {s['score'][0]:.1f}")
        sys.exit(0 if ok else 1)

    if a.stage in ("all", "stage1"):
        jobs = []
        for name, (k, depth, rollout) in stage1.items():
            d = os.path.join(out_dir, "stage1", name)
            write_run(a, d, params_class, k, depth, rollout, a.budget1, a.games, a.seed1)
            jobs.append((d, a.games, f"stage1/{name}"))
        if not run_many(jobs, a.parallel1, java, classpath, repo, log):
            write_report(a, out_dir, list(stage1), [])
            fail("a stage-1 run failed; see its log.txt. Rerun the same command to retry only the failed runs.")

    s1 = write_report(a, out_dir, list(stage1), [])
    selected = []
    if a.stage in ("all", "stage2"):
        if len(s1) != len(stage1):
            fail(f"stage 1 is incomplete ({len(s1)}/{len(stage1)} runs); run --stage stage1 first")
        selected = top_n(s1, a.top)
        with open(os.path.join(out_dir, "selection.json"), "w") as f:
            json.dump({"selected": selected, "rule": f"top {a.top} by stage-1 win rate, ties by mean score"}, f, indent=2)
        log(f"selected for stage 2: {', '.join(selected)}")
        jobs = []
        for name in selected:
            k, depth, rollout = stage1[name]
            d = os.path.join(out_dir, "stage2", name)
            write_run(a, d, params_class, k, depth, rollout, a.budget2, a.games, a.seed2)
            jobs.append((d, a.games, f"stage2/{name}"))
        if not run_many(jobs, a.parallel2, java, classpath, repo, log):
            write_report(a, out_dir, list(stage1), selected)
            fail("a stage-2 run failed; see its log.txt. Rerun the same command to retry only the failed runs.")
    elif os.path.isfile(os.path.join(out_dir, "selection.json")):
        with open(os.path.join(out_dir, "selection.json")) as f:
            selected = json.load(f)["selected"]

    write_report(a, out_dir, list(stage1), selected)
    log(f"done: {os.path.join(out_dir, 'results.md')} (also report.html and results.csv)")


if __name__ == "__main__":
    main()
