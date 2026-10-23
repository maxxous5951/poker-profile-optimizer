# poker-profile-optimizer

Optimisation automatique de profils OpenHoldem par simulation massive.

Relie deux projets existants :
- [OpenHoldem-Profile-Generator-WIP](https://github.com/maxxous5951/OpenHoldem-Profile-Generator-WIP) (`ohpg`) : génère un profil `.ohf` à partir de paramètres.
- [PokerSim](https://github.com/maxxous5951/OH15-by-nolog/tree/pokersim-cpp/PokerSim) : simule des parties entre profils (bb/100 avec intervalle de confiance, PokerTracker simulé).

## Principe

1. Un vecteur de paramètres `ohpg` donne un profil candidat (`.ohf`).
2. `pokersim` le fait jouer contre un pool de bots (`--prwin-max 10000`).
3. Sélection selon le bb/100 et son intervalle de confiance, mutation, génération suivante.
4. Validation finale sur des bots jamais vus pendant l'entraînement.

## Utilisation

```
# 1. Appliquer le patch --json au simulateur (une seule fois), puis recompiler pokersim
cd OH15-by-nolog && git apply /chemin/vers/poker-profile-optimizer/patches/0001-pokersim-cli-json.patch

# 2. Générer le pool de 15 bots (même graine = même pool)
python make_pool.py --ohpg /chemin/vers/OpenHoldem-Profile-Generator-WIP --out pool --seed 1

# 3. Faire jouer des bots du pool, résultats en JSON
pokersim --format cash --players 6 --games 20 --hands 1000 --oh-root /chemin/vers/OH15-by-nolog \
  --json res.json --player BAL_1=pool/profiles/BAL_1.ohf --player TAG_1=pool/profiles/TAG_1.ohf ...
```

## Pool d'adversaires (~15 bots)

- 6 bases : Balanced (2), TAG (3), LAG (3), Nit (2), Maniac (2), Calling Station (3).
- Chaque variante perturbe ~85 paramètres de style (ranges, fréquences, tailles de mise) de ±15 % (variante 1), ±25 % (2) ou ±35 % (3) ; les tailles de mise varient 2,5 fois moins. Graine fixe.
- 10 bots d'entraînement, 5 bots de validation (BAL_2, TAG_3, LAG_3, NIT_2, FISH_3), jamais utilisés pour optimiser. Chaque style est présent dans l'entraînement.
- Liste complète et paramètres modifiés : `pool.json`.
- Noms uniques (le PokerTracker simulé identifie les joueurs par leur nom).

## Feuille de route

- [x] Mesurer les mains/seconde avec `--prwin-max 10000` (voir « Mesures »)
- [x] `--json` pour la CLI de PokerSim (profils + une entrée par session pour l'intervalle de confiance) : `patches/0001-pokersim-cli-json.patch`
- [x] `make_pool.py` : génération reproductible du pool de bots
- [ ] `optimize.py` : boucle évolutive avec évaluation en deux temps (peu de mains, puis gros volume pour les meilleurs) et sauvegarde à chaque génération
- [ ] Mains dupliquées (mêmes cartes, places permutées) pour réduire la variance
- [ ] Validation sur le pool de test

## Limites connues

- Les variantes ne couvrent que ce que permettent les paramètres d'`ohpg` (tailles de mise fixes, `use_stats` limité à nit / fish / maniaque).
- Un profil qui bat le pool ne bat pas forcément de vrais joueurs : c'est le rôle de la validation.
- Les sites de poker en ligne interdisent généralement les bots : usage hors-ligne, recherche et tests uniquement.

## Mesures

Cash 6-max, 6 profils générés par `ohpg` (TAG, LAG, Nit, Maniac, Fish, Balanced), PokerTracker simulé actif, 2 coeurs, Linux, `pokersim` en Release :

| prwin | mains/s |
|---|---|
| plafonné à 10 000 | ~3 200 |
| sans plafond | ~3 450 |

- Les profils générés n'appellent jamais `prwin` (0 occurrence dans les `.ohf`) : le plafond ne change rien tant que le générateur ne l'utilise pas.
- Ordre de grandeur : 100 millions de mains = environ 8 h sur 2 coeurs, la vitesse étant à peu près proportionnelle au nombre de coeurs.
- Les « milliards de mains en quelques minutes » ne sont donc pas atteignables ; en revanche des dizaines de millions par nuit le sont.
