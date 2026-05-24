# DiscVault Contracts

Deze map bevat clientgerichte implementatienotities voor integraties vanuit
DiscVault.

## Richtlijnen

- Bewaar hier alleen afspraken die DiscVault nodig heeft om als client correct
  met een externe service te praten.
- Bewaar geen secrets, tokens, productiesleutels of private operationele details.
- Bewaar geen server-side broncontracten van externe services. Die horen in de
  repository van de service die het contract aanbiedt.
- Verwijs naar externe broncontracten wanneer relevant, maar kopieer alleen de
  clientgerichte details die nodig zijn voor DiscVault-implementatie en tests.

## MovieVault

MovieVault-contracten in deze repository beschrijven alleen hoe DiscVault de
MovieVault-clientkant implementeert, zoals configuratie, handshake-aanroepen,
tokenopslag, retrygedrag en loggingregels.
