#!/usr/bin/env python3
"""Génère un pool reproductible de bots variés avec ohpg (OpenHoldem Profile Generator).

15 bots = 6 styles x 2-3 variantes (paramètres perturbés autour du preset).
10 bots d'entraînement, 5 bots de validation (jamais utilisés pour optimiser).

Usage :
    python make_pool.py --ohpg /chemin/vers/OpenHoldem-Profile-Generator-WIP --out pool --seed 1
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

# (style ohpg, nom court, nombre de variantes, variantes réservées à la validation (indices 1-based))
POOL_SPEC = [
    ("Balanced 6-max", "BAL", 2, {2}),
    ("TAG", "TAG", 3, {3}),
    ("LAG", "LAG", 3, {3}),
    ("Nit", "NIT", 2, {2}),
    ("Maniac", "MAN", 2, set()),
    ("Calling Station (fish)", "FISH", 3, {3}),
]
FORMAT = "Cash 6-max"
SPREADS = {1: 0.15, 2: 0.25, 3: 0.35}   # amplitude de perturbation selon le numéro de variante
SIZE_SPREAD_FACTOR = 0.4                # les tailles de mise varient moins que les fréquences


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


def perturbable_keys(params):
    """Paramètres de style (fréquences, ranges, tailles) : on exclut format, push/fold, stats, gestion de table."""
    groups = {params.G_OPEN, params.G_DEF, params.G_BIG, params.G_FLOP, params.G_TURN, params.G_RIVER, params.G_SIZE}
    skip = {"table_size", "ante_bb", "sb_limp_pct", "payout"}
    return [p for p in params.SCHEMA if p.group in groups and p.kind in ("pct", "int", "float") and p.key not in skip]


def make_variant(presets, params, keys, style: str, name: str, variant: int, rng: random.Random):
    base = presets.build(style, FORMAT)
    spread = SPREADS.get(variant, 0.3)
    overrides = {"name": name}
    for p in keys:
        v = base[p.key]
        if v == 0:
            continue
        s = spread * (SIZE_SPREAD_FACTOR if p.group == params.G_SIZE else 1.0)
        new = v * (1.0 + rng.uniform(-s, s))
        new = params.coerce(p.key, new)
        if p.kind != "int":
            new = round(new, 2)
        if new != v:
            overrides[p.key] = new
    return overrides


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ohpg", help="dossier du dépôt OpenHoldem-Profile-Generator-WIP")
    ap.add_argument("--out", default="pool", help="dossier de sortie (profils + pool.json)")
    ap.add_argument("--seed", type=int, default=1, help="graine : même graine = même pool")
    a = ap.parse_args()

    load_ohpg(a.ohpg)
    from ohpg import generator, params, presets
    from ohpg.validator import errors

    out = Path(a.out)
    (out / "profiles").mkdir(parents=True, exist_ok=True)
    keys = perturbable_keys(params)
    manifest = {"seed": a.seed, "format": FORMAT, "bots": []}

    for si, (style, short, n, val_idx) in enumerate(POOL_SPEC):
        for v in range(1, n + 1):
            name = f"{short}_{v}"
            rng = random.Random(f"{a.seed}:{name}")
            overrides = make_variant(presets, params, keys, style, name, v, rng)
            cfg = presets.build(style, FORMAT, overrides)
            text, issues = generator.generate_checked(cfg)
            errs = errors(issues)
            if errs:
                sys.exit(f"{name} : profil invalide\n" + "\n".join(str(e) for e in errs[:10]))
            path = out / "profiles" / f"{name}.ohf"
            path.write_text(text, encoding="utf-8", newline="\n")
            manifest["bots"].append({
                "name": name, "style": style, "variant": v,
                "split": "validation" if v in val_idx else "train",
                "file": str(path.relative_to(out)),
                "changed_params": {k: val for k, val in overrides.items() if k != "name"},
            })

    (out / "pool.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    tr = sum(b["split"] == "train" for b in manifest["bots"])
    print(f"{len(manifest['bots'])} bots écrits dans {out}/ ({tr} entraînement, {len(manifest['bots']) - tr} validation)")


if __name__ == "__main__":
    main()
