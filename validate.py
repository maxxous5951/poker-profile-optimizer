#!/usr/bin/env python3
"""Valide un profil sur des bots JAMAIS vus pendant l'optimisation, et le compare aux presets d'ohpg.

Toutes les évaluations utilisent les mêmes tables et les mêmes graines (comparaison équitable) et des graines
différentes de celles de l'entraînement. Résultats en bb/100 avec intervalle de confiance à 95 %.

Usage :
    python validate.py --pokersim ... --oh-root ... --pool pool --run runs/run1
    python validate.py --pokersim ... --oh-root ... --pool pool --profile mon_profil.ohf
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import simlib
from simlib import Acc, Ctx

REF_STYLES = ["Balanced 6-max", "TAG", "LAG", "Nit", "Maniac", "Calling Station (fish)"]
VALIDATION_SEED = 987_654


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pokersim", required=True)
    ap.add_argument("--oh-root", required=True)
    ap.add_argument("--ohpg")
    ap.add_argument("--pool", default="pool")
    ap.add_argument("--run", help="dossier d'une optimisation (utilise run/best.ohf)")
    ap.add_argument("--profile", help="ou n'importe quel profil .ohf à valider")
    ap.add_argument("--tables", type=int, default=12)
    ap.add_argument("--games", type=int, default=4, help="sessions par table")
    ap.add_argument("--hands", type=int, default=1000, help="mains par session")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--prwin-max", type=int, default=10000)
    ap.add_argument("--rake", type=float, default=0.0, help="rake en %% (défaut 0 : somme nulle ; essaie 5 avec --rake-cap 30)")
    ap.add_argument("--rake-cap", type=int, default=0, help="plafond du rake en jetons (BB = 10 jetons)")
    ap.add_argument("--no-refs", action="store_true", help="ne pas évaluer les presets de référence")
    a = ap.parse_args()

    if not (a.run or a.profile):
        ap.error("donne --run ou --profile")
    simlib.load_ohpg(a.ohpg)
    pool = simlib.load_pool(a.pool)
    ctx = Ctx(a.pokersim, a.oh_root, a.pool, jobs=a.jobs, prwin_max=a.prwin_max, rake_pct=a.rake, rake_cap=a.rake_cap)
    out = Path(a.run) if a.run else Path(".")
    tmp = out / "tmp_validate"
    tmp.mkdir(parents=True, exist_ok=True)

    heroes = {}
    cand = Path(a.profile) if a.profile else out / "best.ohf"
    if not cand.is_file():
        raise SystemExit(f"profil introuvable : {cand}")
    heroes["CANDIDAT"] = str(cand)
    if not a.no_refs:
        for s in REF_STYLES:
            heroes[f"ref {s}"] = str(simlib.write_profile({}, s, "Cash 6-max", "REF", tmp / (s.split()[0] + ".ohf")))

    splits = {"entraînement": "train", "validation (jamais vus)": "validation"}
    tables = {label: simlib.draw_tables(pool, sp, a.tables, VALIDATION_SEED + (0 if sp == "train" else 1)) for label, sp in splits.items()}
    results: dict[str, dict[str, Acc]] = {h: {} for h in heroes}
    with ThreadPoolExecutor(ctx.n_jobs()) as ex:
        futs = []
        for h, f in heroes.items():
            for label, tbs in tables.items():
                for i, opp in enumerate(tbs):
                    futs.append((h, label, ex.submit(simlib.run_table, ctx, f, "HERO", opp, a.games, a.hands, VALIDATION_SEED + 100 * i)))
        for h, label, fut in futs:
            results[h].setdefault(label, Acc()).merge(fut.result())

    w = max(len(h) for h in heroes)
    labels = list(splits)
    print(f"\n{'profil':<{w}}  " + "  ".join(f"{l:<36}" for l in labels))
    for h in heroes:
        print(f"{h:<{w}}  " + "  ".join(f"{str(results[h][l]):<36}" for l in labels))
    c = results["CANDIDAT"]
    gap = c["entraînement"].mean - c["validation (jamais vus)"].mean
    print(f"\nÉcart entraînement - validation du candidat : {gap:+.1f} bb/100 (un grand écart positif = surapprentissage)")
    if not a.no_refs:
        best_ref = max((h for h in heroes if h != "CANDIDAT"), key=lambda h: results[h]["validation (jamais vus)"].mean)
        v, r = c["validation (jamais vus)"], results[best_ref]["validation (jamais vus)"]
        diff = v.mean - r.mean
        se = (v.se ** 2 + r.se ** 2) ** 0.5
        verdict = "significativement meilleur" if diff > 1.96 * se else ("significativement moins bon" if diff < -1.96 * se else "pas de différence significative")
        print(f"Candidat vs meilleure référence ({best_ref}) sur la validation : {diff:+.1f} ± {1.96 * se:.1f} bb/100 -> {verdict}")

    (out / "validation.json").write_text(json.dumps(
        {h: {l: {"mean": r.mean, "ci95": r.ci95, "hands": r.hands} for l, r in rs.items()} for h, rs in results.items()}, indent=1), encoding="utf-8")
    print(f"résultats écrits dans {out / 'validation.json'}")


if __name__ == "__main__":
    main()
