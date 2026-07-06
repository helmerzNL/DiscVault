# Branch- & release-strategie

DiscVault gebruikt één schone promotieketen. Alle ontwikkeling gebeurt in de
beta-branch; features stromen daarna gecontroleerd door naar productie.

```
feature-branch  ──PR──▶  release/v26-beta   ──(release)──▶  main
   (van main)            (integratie/test)                  (productie)
                          image :v26-beta                    image :latest + :v26
                                                             tag v26.x.x = :stable
```

## Branches

| Branch | Rol | Docker-tags (CI) |
|---|---|---|
| `main` | **Productie**. Beschermd; alleen wijzigingen via PR-merge vanuit `release/v26-beta` (of hotfix). | `:latest`, `:v26` |
| `release/v26-beta` | **Integratie / actieve ontwikkeling**. Alle feature-PR's landen hier eerst. | `:v26-beta` |
| git-tag `v26.x.x` | **Release-markering** op een productie-commit. | `:v26.x.x`, `:v26`, `:stable` |

> `release/v26` is uitgefaseerd: het produceerde dezelfde `:latest`/`:v26`
> images als `main` en veroorzaakte tag-races. Gebruik `main`.

## Dagelijkse workflow

1. Vertak een feature-branch **vanaf `main`**.
2. Open een PR naar **`release/v26-beta`**. CI bouwt de `:v26-beta` image om te
   testen.
3. Merge de PR in `release/v26-beta` zodra hij groen en gereviewd is.

## Promoveren naar productie (release-train)

Ontwikkel alles in beta en promoveer op release-momenten door de hele
`release/v26-beta` naar `main` te mergen:

```sh
git checkout main
git pull
git merge --no-ff origin/release/v26-beta
git push origin main
```

`main` bouwt dan `:latest` + `:v26`. Zet daarna een release-tag:

```sh
git tag v26.<minor>.<patch>
git push origin v26.<minor>.<patch>
```

Wil je een specifieke feature nog **niet** meesturen? Zet die achter een
**feature-flag** in plaats van hem via cherry-pick terug te houden — zo blijft
`main` altijd een ancestor van beta en voorkom je drift.

> Vermijd het handmatig cherry-picken/porten van losse commits tussen branches:
> dat was de oorzaak van de eerdere divergentie tussen `main`, `v26` en
> `v26-beta`.

## Versiebeheer

Wijzigingen aan beschermde paden (`app/**`, `.github/workflows/`, `app/deploy/`,
`dist/plugins/`, …) vereisen een bump van [`app/VERSION`](../app/VERSION).
Zie de version-guard workflow. Documentatie (`*.md`, `*.txt`) is vrijgesteld.

Automatisch bumpen:

```sh
git config core.hooksPath .githooks   # eenmalig per clone/worktree
```
