"""Briques communes : génome -> profil .ohf, lancement de tables pokersim, statistiques (moyenne, erreur-type, IC 95 %)."""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


def load_ohpg(path: str | None):
    candidates = [path, os.environ.get("OHPG_PATH"), str(Path(__file__).resolve().parent.parent / "OpenHoldem-Profile-Generator-WIP")]
    for c in candidates:
        if c and (Path(c) / "ohpg" / "params.py").is_file():
            sys.path.insert(0, str(Path(c).resolve()))
            break
    try:
        import ohpg  # noqa: F401
    except ImportError:
        sys.exit("ohpg introuvable : donne son dossier avec --ohpg ou la variable OHPG_PATH")


@dataclass
class Ctx:
    pokersim: str
    oh_root: str
    pool_dir: str
    big_blind: int = 10
    jobs: int = 0            # nombre de simulations en parallèle (0 = nombre de coeurs)
    prwin_max: int = 10000
    rake_pct: float = 0.0    # 0 = jeu à somme nulle : bb/100 > 0 signifie « bat le champ »
    rake_cap: int = 0        # en jetons (avec BB = 10 : 30 = 3 BB) ; 0 = sans plafond

    def n_jobs(self) -> int:
        return self.jobs or os.cpu_count() or 1


class Acc:
    """Accumulateur de bb/100 par session : moyenne, erreur-type, IC 95 %. Sérialisable."""

    def __init__(self, n=0, s=0.0, ss=0.0, hands=0):
        self.n, self.s, self.ss, self.hands = n, s, ss, hands

    def add(self, x: float, hands: int = 0):
        self.n += 1
        self.s += x
        self.ss += x * x
        self.hands += hands

    def merge(self, o: "Acc"):
        self.n += o.n
        self.s += o.s
        self.ss += o.ss
        self.hands += o.hands

    @property
    def mean(self) -> float:
        return self.s / self.n if self.n else 0.0

    @property
    def se(self) -> float:
        if self.n < 2:
            return float("inf")
        var = max(0.0, (self.ss - self.s * self.s / self.n) / (self.n - 1))
        return math.sqrt(var / self.n)

    @property
    def ci95(self) -> float:
        return 1.96 * self.se

    def to_json(self):
        return [self.n, self.s, self.ss, self.hands]

    @classmethod
    def from_json(cls, v):
        return cls(*v)

    def __str__(self):
        ci = "inf" if math.isinf(self.ci95) else f"{self.ci95:.1f}"
        return f"{self.mean:+.1f} ± {ci} bb/100 ({self.hands:,} mains)"


# ---------------------------------------------------------------------------------------------- pool
def load_pool(pool_dir: str) -> dict:
    m = json.loads((Path(pool_dir) / "pool.json").read_text(encoding="utf-8"))
    for b in m["bots"]:
        b["path"] = str(Path(pool_dir) / b["file"])
    return m


def bots_of(pool: dict, split: str | None = None) -> list[dict]:
    return [b for b in pool["bots"] if split is None or b["split"] == split]


# ---------------------------------------------------------------------------------------------- génome
def write_profile(overrides: dict, style: str, fmt: str, name: str, path: str | Path) -> Path:
    from ohpg import generator, presets
    from ohpg.validator import errors
    cfg = presets.build(style, fmt, {**overrides, "name": name})
    text, issues = generator.generate_checked(cfg)
    errs = errors(issues)
    if errs:
        raise RuntimeError("profil invalide : " + "; ".join(str(e) for e in errs[:5]))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------------------------- simulation
def run_table(ctx: Ctx, hero_file: str, hero_name: str, opponents: list[dict], games: int, hands: int, seed: int) -> Acc:
    """Une table : le héros + ses adversaires. Retourne les bb/100 du héros, une valeur par session."""
    cmd = [ctx.pokersim, "--format", "cash", "--players", str(1 + len(opponents)), "--games", str(games), "--hands", str(hands),
           "--workers", "1", "--seed", str(seed), "--oh-root", ctx.oh_root, "--prwin-max", str(ctx.prwin_max),
           "--bb", str(ctx.big_blind), "--sb", str(ctx.big_blind // 2), "--rake", str(ctx.rake_pct), "--rake-cap", str(ctx.rake_cap),
           "--json", "-", "--player", f"{hero_name}={hero_file}"]
    for b in opponents:
        cmd += ["--player", f"{b['name']}={b['path']}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"pokersim a échoué ({r.returncode}) : {r.stderr.strip()[-300:]}")
    d = json.loads(r.stdout.strip().splitlines()[-1])
    acc = Acc()
    for sess in d["sessions"]:
        for p in sess["players"]:
            if p["name"] == hero_name and p["hands"] > 0:
                acc.add(100.0 * p["net_chips"] / p["hands"] / ctx.big_blind, p["hands"])
    return acc


def draw_tables(pool: dict, split: str, n_tables: int, seed: int, size: int = 6) -> list[list[dict]]:
    """Tables d'adversaires tirées au hasard (graine fixe : tous les candidats jouent les mêmes tables)."""
    bots = bots_of(pool, split)
    if len(bots) < size - 1:
        raise SystemExit(f"pas assez de bots '{split}' dans le pool ({len(bots)}) pour une table de {size}")
    rng = random.Random(seed)
    return [rng.sample(bots, size - 1) for _ in range(n_tables)]


def evaluate(ctx: Ctx, hero_file: str, hero_name: str, tables: list[list[dict]], games: int, hands: int, seed: int,
             executor: ThreadPoolExecutor | None = None) -> Acc:
    total = Acc()
    jobs = [(t, seed + i) for i, t in enumerate(tables)]
    if executor is None:
        for opp, sd in jobs:
            total.merge(run_table(ctx, hero_file, hero_name, opp, games, hands, sd))
    else:
        futs = [executor.submit(run_table, ctx, hero_file, hero_name, opp, games, hands, sd) for opp, sd in jobs]
        for f in futs:
            total.merge(f.result())
    return total
