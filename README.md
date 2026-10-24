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

## Interface graphique

La branche [`gui-optimizer`](https://github.com/maxxous5951/OH15-by-nolog/tree/gui-optimizer) de PokerSim ajoute un onglet **Optimisation** qui pilote ces scripts : environnement, création du pool, lancement / arrêt / reprise, courbe de progression, validation et export du meilleur profil. Elle contient déjà le `--json` (le patch ci-dessous n'est alors pas nécessaire).
Les dossiers `poker-profile-optimizer` et `OpenHoldem-Profile-Generator-WIP` placés à côté de `pokersim_gui.exe` (ou un ou plusieurs niveaux au-dessus) sont trouvés automatiquement.

## Utilisation en ligne de commande

```
# 1. Appliquer le patch --json au simulateur (une seule fois), puis recompiler pokersim
cd OH15-by-nolog && git apply /chemin/vers/poker-profile-optimizer/patches/0001-pokersim-cli-json.patch

# 2. Générer le pool de 15 bots (même graine = même pool)
python make_pool.py --ohpg /chemin/vers/OpenHoldem-Profile-Generator-WIP --out pool --seed 1

# 3. Faire jouer des bots du pool, résultats en JSON
pokersim --format cash --players 6 --games 20 --hands 1000 --oh-root /chemin/vers/OH15-by-nolog \
  --json res.json --player BAL_1=pool/profiles/BAL_1.ohf --player TAG_1=pool/profiles/TAG_1.ohf ...
```

### Lancer l'optimisation puis la validation

```
python optimize.py --pokersim /chemin/pokersim --oh-root /chemin/OH15-by-nolog --ohpg /chemin/OpenHoldem-Profile-Generator-WIP \
  --pool pool --run runs/run1 --gens 30            # Ctrl+C puis --resume pour reprendre
python validate.py --pokersim ... --oh-root ... --ohpg ... --pool pool --run runs/run1 --tables 30 --games 10 --hands 2000
```

- Génération 0 : les 6 presets d'`ohpg` + copies mutées. Ensuite : croisement + mutation, étape 1 (tables courtes) sur tous les enfants, étape 2 (gros volume) sur les `--refine` meilleurs. Score de sélection = moyenne - k x erreur-type (`--k`, défaut 1).
- Gènes optimisés : 23 paramètres de style par défaut (ranges d'ouverture, 3bet, défense, c-bet, barrels, bluffs river, tolérance de call, tailles de mise). `--genes a,b,c` pour choisir ; `--use-stats` active `use_stats` et ajoute 16 paramètres d'exploitation.
- Les bots de validation ne sont **jamais** utilisés par `optimize.py`. `validate.py` joue le candidat et les 6 presets sur les mêmes tables et les mêmes graines (graines différentes de l'entraînement) et affiche l'écart entraînement - validation (surapprentissage).
- Rake à 0 par défaut (somme nulle : bb/100 > 0 = bat le champ). `--rake 5 --rake-cap 30` pour inclure un rake de 5 % plafonné à 3 BB. Attention : `pokersim` applique 5 % sans plafond si on ne précise rien.
- Précision : sur 8 000 mains l'intervalle de confiance est d'environ ±30 bb/100 ; il faut de l'ordre de 2 millions de mains pour descendre vers ±2 bb/100 (l'erreur diminue avec la racine du nombre de mains).

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
- [x] `optimize.py` : boucle évolutive avec évaluation en deux temps et sauvegarde/reprise à chaque génération
- [ ] Mains dupliquées (mêmes cartes, places permutées) pour réduire la variance
- [x] `validate.py` : validation sur les bots jamais vus + comparaison aux presets d'ohpg

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
