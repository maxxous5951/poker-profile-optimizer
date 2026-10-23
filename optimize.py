#!/usr/bin/env python3
"""Optimisation évolutive d'un profil OpenHoldem contre un pool de bots (pokersim + ohpg).

Chaque génération :
  1. des enfants sont créés (croisement + mutation des survivants) ;
  2. étape 1 : chaque enfant joue quelques tables courtes (adversaires tirés dans les bots d'ENTRAÎNEMENT) ;
  3. étape 2 : les meilleurs enfants reçoivent un gros volume de mains ;
  4. survivants = meilleurs scores, score = moyenne - k x erreur-type (borne basse : évite les gagnants par chance).

Tout est sauvegardé à chaque génération (state.json) : Ctrl+C puis --resume pour reprendre.
Le pool de VALIDATION n'est jamais utilisé ici : voir validate.py.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import simlib
from simlib import Acc, Ctx

STYLE, FORMAT = "Balanced 6-max", "Cash 6-max"
HERO = "HERO"

CORE_GENES = [
    "open_ep", "open_mp", "open_co", "open_btn", "open_sb",
    "threebet_value", "threebet_bluff_ratio", "bb_defend_pct", "call_ip_pct", "fourbet_value", "call_3bet_ip",
    "cbet_bluff_ip", "cbet_bluff_oop", "cbet_semibluff", "barrel_bluff", "river_bluff",
    "call_scale", "river_call_scale", "aggression_scale",
    "size_cbet_dry", "size_cbet_wet", "size_turn", "size_river",
]
EXPLOIT_GENES = [
    "exploit_vs_nit", "exploit_vs_fish", "exploit_vs_maniac", "exploit_size_fish", "exploit_size_nit", "exploit_size_maniac",
    "exploit_bluff_sticky", "exploit_bluff_folder", "exploit_bluff_nit", "exploit_bluff_fish", "exploit_bluff_maniac",
    "exploit_value_fish", "exploit_call_nit", "exploit_call_fish", "exploit_call_maniac", "exploit_iso_fish",
]
SEED_STYLES = ["Balanced 6-max", "TAG", "LAG", "Nit", "Maniac", "Calling Station (fish)"]


# ---------------------------------------------------------------------------------------------- génome
class Space:
    def __init__(self, genes: list[str], use_stats: bool):
        from ohpg import params, presets
        self.params, self.presets = params, presets
        self.genes = genes
        self.use_stats = use_stats
        self.base = presets.build(STYLE, FORMAT)

    def bounds(self, g):
        p = self.params.SCHEMA_BY_KEY[g]
        return p.lo, p.hi

    def clamp(self, g, v):
        v = self.params.coerce(g, v)
        return v if self.params.SCHEMA_BY_KEY[g].kind == "int" else round(v, 3)

    def from_style(self, style):
        full = self.presets.build(style, FORMAT)
        return {g: full[g] for g in self.genes}

    def mutate(self, genome, rng, sigma, p_gene):
        out = dict(genome)
        picked = [g for g in self.genes if rng.random() < p_gene] or [rng.choice(self.genes)]
        for g in picked:
            lo, hi = self.bounds(g)
            out[g] = self.clamp(g, out[g] + rng.gauss(0, sigma) * (hi - lo))
        return out

    def cross(self, a, b, rng):
        return {g: (a[g] if rng.random() < 0.5 else b[g]) for g in self.genes}

    def overrides(self, genome):
        o = dict(genome)
        if self.use_stats:
            o["use_stats"] = True
        return o


# ---------------------------------------------------------------------------------------------- boucle
class Cand:
    _next = 0

    def __init__(self, genome, acc=None, cid=None, origin=""):
        if cid is None:
            Cand._next += 1
            cid = Cand._next
        self.id, self.genome, self.acc, self.origin = cid, genome, acc or Acc(), origin

    def score(self, k):
        return self.acc.mean - k * self.acc.se if self.acc.n >= 2 else float("-inf")

    def to_json(self):
        return {"id": self.id, "genome": self.genome, "acc": self.acc.to_json(), "origin": self.origin}

    @classmethod
    def from_json(cls, d):
        return cls(d["genome"], Acc.from_json(d["acc"]), d["id"], d.get("origin", ""))


def eval_batch(ctx, ex, space, cands, tables, games, hands, seed, tmp: Path):
    """Fait jouer tous les candidats sur les mêmes tables, en parallèle ; ajoute les résultats à leur accumulateur."""
    futs = []
    for c in cands:
        f = simlib.write_profile(space.overrides(c.genome), STYLE, FORMAT, HERO, tmp / f"c{c.id}.ohf")
        for i, opp in enumerate(tables):
            futs.append((c, ex.submit(simlib.run_table, ctx, str(f), HERO, opp, games, hands, seed + i)))
    for c, fut in futs:
        c.acc.merge(fut.result())


def save_state(run: Path, gen, pop, args):
    (run / "state.json").write_text(json.dumps({"gen": gen, "next_id": Cand._next, "pop": [c.to_json() for c in pop],
                                                "args": vars(args)}, indent=1), encoding="utf-8")


def save_best(run: Path, space, best: Cand, gen):
    d = {"generation": gen, "mean_bb100": best.acc.mean, "ci95": best.acc.ci95, "hands": best.acc.hands,
         "genome": best.genome, "use_stats": space.use_stats, "style": STYLE, "format": FORMAT}
    (run / "best.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
    simlib.write_profile(space.overrides(best.genome), STYLE, FORMAT, "OPT_BEST", run / "best.ohf")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pokersim", required=True, help="exécutable pokersim (avec le patch --json)")
    ap.add_argument("--oh-root", required=True, help="dossier OH15-by-nolog (contient OpenPPL/OpenPPL_Library)")
    ap.add_argument("--ohpg", help="dossier OpenHoldem-Profile-Generator-WIP")
    ap.add_argument("--pool", default="pool", help="dossier créé par make_pool.py")
    ap.add_argument("--run", default="runs/run1", help="dossier de sortie de cette optimisation")
    ap.add_argument("--gens", type=int, default=30)
    ap.add_argument("--mu", type=int, default=8, help="survivants")
    ap.add_argument("--lam", type=int, default=8, help="enfants par génération")
    ap.add_argument("--refine", type=int, default=3, help="nombre d'enfants qui passent à l'étape 2")
    ap.add_argument("--t1", type=int, default=4, help="étape 1 : nombre de tables")
    ap.add_argument("--t2", type=int, default=10, help="étape 2 : nombre de tables")
    ap.add_argument("--games", type=int, default=2, help="sessions par table à l'étape 1 (x2 à l'étape 2)")
    ap.add_argument("--hands", type=int, default=1000, help="mains par session")
    ap.add_argument("--k", type=float, default=1.0, help="score = moyenne - k x erreur-type")
    ap.add_argument("--sigma", type=float, default=0.10, help="mutation initiale (fraction de la plage du paramètre)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=0, help="simulations en parallèle (0 = nb de coeurs)")
    ap.add_argument("--prwin-max", type=int, default=10000)
    ap.add_argument("--rake", type=float, default=0.0, help="rake en %% (défaut 0 : somme nulle)")
    ap.add_argument("--rake-cap", type=int, default=0, help="plafond du rake en jetons (BB = 10 jetons)")
    ap.add_argument("--use-stats", action="store_true", help="active use_stats et optimise aussi les paramètres d'exploitation")
    ap.add_argument("--genes", help="liste de paramètres à optimiser, séparés par des virgules (défaut : jeu de base)")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    simlib.load_ohpg(args.ohpg)
    pool = simlib.load_pool(args.pool)
    ctx = Ctx(args.pokersim, args.oh_root, args.pool, jobs=args.jobs, prwin_max=args.prwin_max,
              rake_pct=args.rake, rake_cap=args.rake_cap)
    genes = args.genes.split(",") if args.genes else CORE_GENES + (EXPLOIT_GENES if args.use_stats else [])
    space = Space(genes, args.use_stats)
    run = Path(args.run)
    run.mkdir(parents=True, exist_ok=True)
    tmp = run / "tmp"

    gen0, pop = 0, []
    if args.resume and (run / "state.json").exists():
        st = json.loads((run / "state.json").read_text(encoding="utf-8"))
        gen0, pop = st["gen"], [Cand.from_json(c) for c in st["pop"]]
        Cand._next = st["next_id"]
        print(f"reprise à la génération {gen0} ({len(pop)} survivants)")

    with ThreadPoolExecutor(ctx.n_jobs()) as ex:
        t_start = time.time()
        if not pop:   # génération 0 : presets + copies bruitées
            rng = random.Random(args.seed)
            seeds = [Cand(space.from_style(s), origin=f"preset {s}") for s in SEED_STYLES]
            while len(seeds) < args.mu + args.lam:
                parent = rng.choice(seeds[:len(SEED_STYLES)])
                seeds.append(Cand(space.mutate(parent.genome, rng, args.sigma * 2, 0.4), origin="preset muté"))
            tables = simlib.draw_tables(pool, "train", args.t1, args.seed * 7919)
            eval_batch(ctx, ex, space, seeds, tables, args.games, args.hands, args.seed * 104729, tmp)
            seeds.sort(key=lambda c: -c.acc.mean)
            top = seeds[:args.refine + 2]
            tables2 = simlib.draw_tables(pool, "train", args.t2, args.seed * 7919 + 777)
            eval_batch(ctx, ex, space, top, tables2, args.games * 2, args.hands, args.seed * 104729 + 5000, tmp)
            pop = sorted(seeds, key=lambda c: -c.score(args.k))[:args.mu]
            save_state(run, 0, pop, args)
            save_best(run, space, pop[0], 0)
            print(f"gen 0 (presets) | meilleur : {pop[0].origin} {pop[0].acc}")

        for gen in range(gen0 + 1, args.gens + 1):
            rng = random.Random(args.seed * 1000 + gen)
            sigma = args.sigma * (1 - 0.7 * (gen - 1) / max(1, args.gens - 1))   # mutation décroissante : 100 % -> 30 %
            kids = []
            for _ in range(args.lam):
                a = min(rng.sample(pop, min(2, len(pop))), key=lambda c: -c.score(args.k))
                b = min(rng.sample(pop, min(2, len(pop))), key=lambda c: -c.score(args.k))
                g = space.cross(a.genome, b.genome, rng) if rng.random() < 0.6 else dict(a.genome)
                kids.append(Cand(space.mutate(g, rng, sigma, 0.3), origin=f"g{gen}"))
            gseed = args.seed * 104729 + gen * 10000
            tables = simlib.draw_tables(pool, "train", args.t1, gseed)
            eval_batch(ctx, ex, space, kids, tables, args.games, args.hands, gseed, tmp)
            kids.sort(key=lambda c: -c.acc.mean)
            tables2 = simlib.draw_tables(pool, "train", args.t2, gseed + 777)
            eval_batch(ctx, ex, space, kids[:args.refine], tables2, args.games * 2, args.hands, gseed + 5000, tmp)
            pop = sorted(pop + kids, key=lambda c: -c.score(args.k))[:args.mu]
            save_state(run, gen, pop, args)
            save_best(run, space, pop[0], gen)
            with open(run / "history.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"gen": gen, "best": pop[0].to_json(), "survivors": [(c.id, round(c.acc.mean, 2), c.acc.n) for c in pop]}) + "\n")
            entered = sum(1 for c in pop if c.origin == f"g{gen}")
            el = (time.time() - t_start) / 60
            print(f"gen {gen}/{args.gens} | meilleur : {pop[0].acc} | {entered} nouveau(x) dans les survivants | {el:.1f} min")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"terminé. Meilleur profil : {run / 'best.ohf'} (paramètres : {run / 'best.json'})\nÀ valider avec : python validate.py --run {run} ...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\ninterrompu : relance avec --resume pour reprendre à la dernière génération sauvegardée")
