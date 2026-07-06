# Branch- & release-strategie

DiscVault gebruikt één schone promotieketen met **twee logische images**. Alle
ontwikkeling gebeurt in de beta-branch; features stromen daarna gecontroleerd —
per feature — door naar productie.

```
feature-branch  ──PR──▶  release/v26-beta   (integratie/test)   image :v26-beta + :beta
   (van main)     │
                  └──PR──▶  main             (productie)         image :latest + :v26 + :stable
                            └─ release.yml ──▶ tag v26.x.x         image :v26.x.x (+ :v26, :stable)
```

## Twee images

| Image | Bron | Docker-tags |
|---|---|---|
| **Productie** | branch `main` | `:latest`, `:v26`, `:stable` |
| **Ontwikkeling** | branch `release/v26-beta` | `:v26-beta`, `:beta` |
| **Release-snapshot** | git-tag `v26.x.x` op een `main`-commit | `:v26.x.x`, `:v26`, `:stable` |
| **Handmatige escape** | workflow_dispatch (Actions) | `:dev`, `:dev-<sha>` |

`:stable` volgt sinds het 2-image-model **`main`** (niet langer alleen een
losse tag). Daardoor zijn `:latest` en `:stable` altijd hetzelfde image en kan
`:stable` niet meer per ongeluk vooruitlopen op productie doordat een tag vanaf
beta werd gezet.

> `release/v26` is uitgefaseerd: het produceerde dezelfde `:latest`/`:v26`
> images als `main` en veroorzaakte tag-races. Gebruik `main`.

## Update-kanalen (in de app)

De in-app update-check kent drie kanalen. Elk kanaal leest zijn "laatste versie"
uit `app/VERSION` op een branch — geen afhankelijkheid meer van los geknipte
GitHub Releases:

| Kanaal | Bron-branch | Image |
|---|---|---|
| `stable` | `main` | `:stable` |
| `beta` | `release/v26-beta` | `:v26-beta` |
| `auto` | heuristiek (kiest stable of beta) | — |

Het oude `v26`-kanaal is vervallen; een opgeslagen `v26`-voorkeur wordt
automatisch als `stable` behandeld (`main` == de oude v26-lijn).

## Dagelijkse workflow (per feature)

1. Vertak een feature-branch **vanaf `main`**.
2. Open een PR naar **`release/v26-beta`**. CI bouwt de `:v26-beta`/`:beta`
   image om integraal te testen.
3. Merge de PR in `release/v26-beta` zodra hij groen en gereviewd is.

Omdat elke feature-branch vanaf `main` vertakt, kun je hem later **individueel**
promoveren zonder de rest van beta mee te nemen.

## Een feature promoveren naar productie

Wanneer een feature productie-klaar is, open je een **tweede PR** vanuit
diezelfde feature-branch naar `main`:

```sh
gh pr create --base main --head <feature-branch> \
  --title "promote: <feature>" --fill
# na groene checks + review:
gh pr merge --merge
```

`main` is beschermd (PR vereist, `version-guard` moet groen zijn, Copilot-review),
dus promotie loopt altijd via een PR — nooit via een directe push. Na de merge
bouwt `main` `:latest` + `:v26` + `:stable`.

> Werkt een feature nog niet los te koppelen van andere beta-wijzigingen? Zet
> hem dan achter een **feature-flag** in plaats van commits te cherry-picken —
> zo blijft de lineage schoon en voorkom je drift.

## Een release knippen

`app/VERSION` is de enige bron van waarheid voor het versienummer. Nadat
promotie-PR('s) op `main` zijn geland en de version-guard `app/VERSION` heeft
gebumpt, knip je een release via de **`Release (tag main)`**-workflow:

1. GitHub -> **Actions** -> **Release (tag main)** -> **Run workflow**.
2. Kies branch **`main`** en start.

De workflow leest `app/VERSION`, controleert dat de tag nog niet bestaat, zet
`v<VERSION>` op de huidige `main`-commit en publiceert een GitHub Release met
gegenereerde notes. De tag-push triggert vervolgens `docker-publish.yml`, die de
release-snapshot (`:v26.x.x` + ververste `:v26`/`:stable`) bouwt vanaf exact die
commit.

> Vermijd het handmatig cherry-picken/porten van losse commits tussen branches:
> dat was de oorzaak van de eerdere divergentie tussen `main`, `v26` en
> `v26-beta`.

## Versiebeheer

Wijzigingen aan beschermde paden (`app/**`, `.github/workflows/`, `app/deploy/`,
`dist/plugins/`, ...) vereisen een bump van [`app/VERSION`](../app/VERSION).
Zie de version-guard workflow. Documentatie (`*.md`, `*.txt`) is vrijgesteld.

Automatisch bumpen:

```sh
git config core.hooksPath .githooks   # eenmalig per clone/worktree
```
