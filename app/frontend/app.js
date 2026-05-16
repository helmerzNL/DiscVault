// ── i18n ──────────────────────────────────────────────────────────────────────
const LANGS = {
nl: {
  // nav
  'nav.collection':'Collectie','nav.import':'Import','nav.search':'Zoeken','nav.add':'Toevoegen','nav.logs':'Logs','nav.settings':'Instellingen',
  // general
  'general.back':'Terug',
  // header
  'header.loading':'Laden...','header.offline':'Offline modus','header.queue':'Wachtrij: {0}','header.logout':'↩ Uitloggen','header.menuOpen':'Menu openen',
  // scan
  'scan.title':'Barcode Scannen','scan.placeholder':'Camera starten om te scannen','scan.startCamera':'▶ Camera Starten','scan.stop':'⏹ Stoppen',
  'scan.manualLabel':'Of voer barcode handmatig in','scan.barcodePlaceholder':'Bijv. 5051892198103','scan.search':'Zoeken',
  'scan.movieInfo':'Film Informatie','scan.format':'Formaat','scan.location':'Locatie (optioneel)','scan.locationPlaceholder':'Bijv. Rek B plank 3',
  'scan.hdr':'Dolby Vision / HDR','scan.hdrPlaceholder':'Bijv. Dolby Vision, HDR10+, HDR10',
  'scan.audioTracks':'Audiosporen','scan.audioPlaceholder':'Bijv. Dolby Atmos, DTS-HD MA 5.1',
  'scan.subtitles':'Ondertiteltracks','scan.subtitlesPlaceholder':'Bijv. NL, EN, DE',
  'scan.save':'💾 Opslaan in Collectie','scan.noResult':'Scan een barcode of voer hem handmatig in',
  // collection
  'collection.searchPlaceholder':'Zoek op titel, regisseur, genre...','collection.filterAll':'Alles',
  'collection.allCollections':'Alle collecties','collection.myMovies':'Mijn films',
  'collection.selectMode':'☑ Selecteren','collection.refresh':'↻ Vernieuwen',
  // bulk
  'bulk.count':'{0} geselecteerd','bulk.selectAll':'Alles selecteren','bulk.deselect':'Deselecteer',
  'bulk.refresh':'↺ Bijwerken via TMDb/OMDb','bulk.delete':'🗑 Verwijderen','bulk.cancel':'✕ Annuleren',
  'bulk.noSelection':'Geen selectie',
  // progress
  'progress.busy':'Bezig...','progress.close':'Sluiten',
  // add
  'add.title':'Snel Handmatig Toevoegen',
  'add.description':'Gebruik dit onderdeel als scannen niet lukt: vul barcode en titel in, of gebruik Auto-fill op basis van titel.',
  'add.barcodeLabel':'Barcode / Eigen ID *','add.barcodePlaceholder':'Barcode of uniek ID',
  'add.titleLabel':'Titel *','add.titlePlaceholder':'Bijv. The Dark Knight','add.autofill':'🔍 Auto-fill',
  'add.year':'Jaar','add.format':'Formaat','add.director':'Regisseur','add.directorPlaceholder':'Bijv. Christopher Nolan',
  'add.genre':'Genre','add.genrePlaceholder':'Bijv. Action, Drama',
  'add.runtime':'Speelduur','add.runtimePlaceholder':'Bijv. 152 min',
  'add.rating':'IMDb Rating','add.ratingPlaceholder':'Bijv. 9.0',
  'add.hdr':'Dolby Vision / HDR','add.hdrPlaceholder':'Bijv. Dolby Vision, HDR10+',
  'add.audioTracks':'Audiosporen','add.audioPlaceholder':'Bijv. Dolby Atmos, DTS:X, TrueHD 7.1',
  'add.subtitles':'Ondertiteltracks','add.subtitlesPlaceholder':'Bijv. Nederlands, Engels, Duits',
  'add.location':'Locatie','add.locationPlaceholder':'Bijv. Rek A plank 2',
  'add.notes':'Notities','add.notesPlaceholder':'Steelbook, limited edition, ...',
  'add.posterUrl':'Poster URL','add.submit':'💾 Toevoegen aan Collectie','add.clear':'✕ Wissen',
  // import
  'import.title':'Collectie Importeren',
  'import.description':'Importeer films vanuit een CSV of XML export van CLZ Movies, iCollect, Blu-ray.com of een eigen spreadsheet. Kolomnamen worden automatisch herkend; bekende aliassen (Nederlands & Engels) worden gemapt.',
  'import.dropHere':'Sleep een bestand hierheen','import.clickBrowse':'of klik om te bladeren · CSV, TSV of XML',
  'import.preview':'Voorbeeld','import.duplicates':'Bij duplicaten:','import.skip':'Overslaan','import.overwrite':'Overschrijven',
  'import.enrich':'Auto-aanvullen via TMDb','import.downloadCovers':'Covers downloaden',
  'import.import':'⬆ Importeren','import.cancel':'⏹ Import afbreken','import.otherFile':'← Ander bestand',
  'import.result':'Import Resultaat','import.viewCollection':'📀 Bekijk Collectie','import.importAnother':'Nog een bestand importeren',
  'import.supportedColumns':'📋 Ondersteunde kolomnamen',
  'import.csvHelp':'komma, puntkomma, tab of pipe als scheidingsteken — automatisch gedetecteerd.',
  'import.xmlHelp':'elk herhalend element wordt als film-rij behandeld; geneste tags worden samengevoegd.',
  // logs
  'logs.allLevels':'Alle niveaus','logs.errors':'🔴 Fouten','logs.warnings':'🟡 Waarschuwingen','logs.success':'🟢 Succes','logs.info':'🔵 Info',
  'logs.allCategories':'Alle categorieën','logs.catImport':'⬆ Import','logs.catRefresh':'↺ Refresh','logs.catLookup':'🔍 Lookup',
  'logs.catAdd':'＋ Toevoegen','logs.catDelete':'🗑 Verwijderen','logs.catGeneral':'⚙ Algemeen',
  'logs.refresh':'↻ Vernieuwen','logs.clear':'🗑 Wis logs',
  'logs.thTimestamp':'Tijdstip','logs.thLevel':'Niveau','logs.thCategory':'Categorie','logs.thBackends':'Backends','logs.thMessage':'Bericht',
  'logs.loading':'Laden...',
  // settings
  'settings.menuGeneral':'Algemeen','settings.menuSecurity':'Veiligheid','settings.menuBackup':'Backup','settings.menuLogs':'Logs','settings.menuAdvanced':'Geavanceerd','settings.menuNotifications':'Meldingen',
  'settings.notifTitle':'🔔 Push-meldingen','settings.notifDesc':'Ontvang meldingen op dit apparaat voor voltooide imports en uitnodigingen.','settings.pushEnable':'🔔 Inschakelen','settings.pushDisable':'🔕 Uitschakelen','settings.pushTest':'📣 Test versturen','settings.pushActive':'Meldingen actief op dit apparaat.','settings.pushInactive':'Meldingen uitgeschakeld op dit apparaat.','settings.pushBlocked':'Meldingen geblokkeerd — pas aan in browserinstellingen.','settings.pushUnsupported':'Push-meldingen worden niet ondersteund door deze browser.',
  'settings.menuProfile':'Profiel',
  'settings.profileTitle':'👤 Gebruikersprofiel',
  'settings.profilePhoto':'Profielfoto','settings.profilePhotoDesc':'Klik om een foto te uploaden (max 2MB)','settings.profilePhotoRemove':'Verwijderen',
  'settings.profileUsername':'Gebruikersnaam','settings.profileFirstName':'Voornaam','settings.profileLastName':'Achternaam',
  'settings.profileSave':'💾 Opslaan','settings.profileSaved':'✓ Profiel opgeslagen',
  'settings.profileUsernameRequired':'Vul een gebruikersnaam in','settings.profileUsernameTaken':'Deze gebruikersnaam is al in gebruik',
  'settings.profilePhotoUpdated':'✓ Foto bijgewerkt','settings.profilePhotoRemoved':'✓ Foto verwijderd','settings.profilePhotoTooLarge':'Foto is te groot (max 2MB)',
  'settings.profilePasskeys':'🔑 Passkeys beheren','settings.profileLogout':'↩ Uitloggen',
  'settings.dbInfo':'Database Informatie','settings.version':'Versie:','settings.versionLoading':'laden...','settings.loading':'Laden...',
  'settings.backup':'Backup & Herstel','settings.createBackup':'💾 Backup Maken','settings.uploadBackup':'📤 Backup Uploaden',
  'settings.metadataSources':'Metadata Bronnen',
  'settings.omdbDesc':'OMDb — filminformatie via IMDb-id of titel',
  'settings.tmdbDesc':'TMDb — filminformatie via titel + jaar',
  'settings.blurayDesc':'Blu-ray.com scraper (experimenteel)',
  'settings.blurayDeDesc':'bluray-disc.de scraper (experimenteel)',
  'settings.sourceHelp':'OMDb en TMDb vereisen een API-key (zie .env). De scrapers proberen HDR/Audiosporen/Ondertitels op te halen zonder officiele API.',
  'settings.offlineQueue':'📶 Offline Wachtrij',
  'settings.offlineQueueDesc':'Acties die offline zijn uitgevoerd worden hier tijdelijk bewaard en later gesynchroniseerd.',
  'settings.syncNow':'↻ Nu synchroniseren','settings.clearQueue':'🗑 Wachtrij legen',
  'settings.language':'Taal','settings.languageDesc':'Kies de taal voor de interface.',
  'settings.advanced':'Geavanceerd','settings.openLogs':'Open Logs',
  'settings.debugEnabled':'Debug modus (toon IDs)',
  'settings.debugDesc':'Toon Film ID en Persoon ID in detailvensters voor troubleshooting en debug.',
  'settings.mcpEnabled':'MCP Server ingeschakeld',
  'settings.mcpDesc':'Schakel de MCP server uit als je deze niet gebruikt. Hiermee wordt de API-toegang voor MCP-clients geblokkeerd.',
  'settings.resetTitle':'⚠ Database Resetten',
  'settings.resetDesc':'Verwijdert alle films, posters en logs. Maak eerst een backup! Authenticatie-instellingen en passkeys blijven bewaard.',
  'settings.resetButton':'🗑 Database Wissen',
  'settings.authTitle':'🔐 Authenticatie (Passkey)','settings.authActive':'Authenticatie actief','settings.logout':'↩ Uitloggen',
  'settings.noPasskey':'Nog geen passkey geregistreerd. Registreer er één om authenticatie in te schakelen.',
  'settings.username':'Gebruikersnaam','settings.passkeyName':'Passkey naam','settings.myPasskey':'Mijn Passkey',
  'settings.passkeyPlaceholder':'Bijv. MacBook Touch ID','settings.registerPasskey':'🔑 Passkey Registreren',
  'settings.addPasskey':'Extra passkey toevoegen','settings.name':'Naam','settings.addPasskeyPlaceholder':'Bijv. iPhone','settings.addPasskeyBtn':'🔑 Toevoegen',
  'settings.usersTitle':'👥 Gebruikersbeheer','settings.groupsTitle':'📁 Groepen','settings.groupName':'Groepsnaam',
  'settings.registrationEnabled':'Nieuwe gebruikers mogen zich registreren',
  'settings.registrationDesc':'Als dit uitstaat kunnen nieuwe gebruikers geen account aanmaken via het login-scherm.',
  'settings.inviteTitle':'✉️ Uitnodigingscodes',
  'settings.inviteDesc':'Maak tijdelijke codes aan waarmee nieuwe gebruikers een account kunnen registreren.',
  'settings.inviteUsername':'Gebruikersnaam nieuwe gebruiker',
  'settings.inviteCreate':'+ Code aanmaken',
  'settings.inviteCodeLabel':'Code',
  'settings.inviteCopy':'📋 Kopiëren',
  'settings.inviteExpires':'Vervalt',
  'settings.inviteRevoke':'Intrekken',
  'settings.inviteUsed':'Gebruikt',
  'settings.inviteActive':'Actief',
  'settings.inviteExpired':'Verlopen',
  'js.registrationEnabled':'Registratie ingeschakeld',
  'js.registrationDisabled':'Registratie uitgeschakeld',
  'js.inviteCreated':'Uitnodigingscode aangemaakt!',
  'js.inviteCreateError':'Kon geen uitnodigingscode aanmaken.',
  'js.inviteCopied':'Code gekopieerd!',
  'js.inviteRevoked':'Uitnodigingscode ingetrokken.',
  'js.noInviteCodes':'Geen actieve uitnodigingscodes.',
  // login
  'login.prompt':'Log in met je passkey','login.button':'🔑 Inloggen','login.recovery':'🔐 Herstelcode gebruiken',
  'login.recoveryUsername':'Gebruikersnaam','login.recoveryCode':'Herstelcode','login.recoveryBtn':'🔓 Inloggen met herstelcode',
  'login.recoveryFillIn':'Vul gebruikersnaam en herstelcode in',
  'login.recoveryFailed':'Recovery mislukt',
  'login.newRecoveryAlert':'Nieuwe herstelcode: {0}\n\nBewaar deze code veilig! Je passkeys zijn verwijderd, registreer een nieuwe passkey in Instellingen > Beveiliging.',
  'login.register':'✨ Account aanmaken',
  'login.registerWithInvite':'✉️ Registreren met uitnodigingscode',
  'login.registerUsername':'Gebruikersnaam',
  'login.inviteCode':'Uitnodigingscode',
  'login.inviteCodePlaceholder':'XXXX-XXXX-XXXX',
  'login.registerPasskeyName':'Passkey naam',
  'login.registerBtn':'🔑 Registreren',
  'login.registerFillIn':'Vul een gebruikersnaam in',
  'login.registerSuccess':'✓ Account aangemaakt! Je bent nu ingelogd.',
  'login.registrationDisabled':'Registratie is uitgeschakeld door de beheerder.',
  'recovery.title':'🔑 Herstelcode','recovery.desc':'Bewaar deze code veilig. Je kunt deze gebruiken om in te loggen als je je passkey kwijt bent.',
  'recovery.dismiss':'Begrepen, ik heb de code opgeslagen',
  // modal
  'modal.plot':'Verhaal','modal.edit':'Bewerken','modal.refreshMeta':'Refresh metadata',
  'modal.syncAll':'Sync alle bronnen','modal.delete':'Verwijderen',
  'modal.copyId':'Kopieer ID',
  'modal.tabInfo':'Basisinfo','modal.tabCast':'Cast & Crew',
  'modal.noCast':'Geen cast/crew gevonden. Ververs de metadata om deze op te halen.',
  'js.idCopied':'ID gekopieerd','js.copyFailed':'Kopieren mislukt',
  // person
  'person.inCollection':'In jouw collectie','person.born':'Geboren','person.died':'Overleden',
  'person.birthPlace':'Geboorteplaats','person.knownFor':'Bekend voor',
  'person.noBio':'Geen biografie beschikbaar.','person.as':'als',
  // edit
  'edit.title':'Film bewerken','edit.titleLabel':'Titel *','edit.originalTitle':'Originele titel','edit.sortTitle':'Sorteertitel',
  'edit.year':'Jaar','edit.releaseDate':'Releasedatum','edit.director':'Regisseur','edit.actors':'Acteurs',
  'edit.producer':'Producer','edit.studios':"Studio's",'edit.genre':'Genre','edit.format':'Formaat',
  'edit.runtime':'Speelduur (min)','edit.hdr':'HDR','edit.screenRatio':'Schermverhouding','edit.packaging':'Verpakking',
  'edit.regions':"Regio's",'edit.audienceRating':'Kijkwijzer','edit.imdbRating':'IMDb Rating',
  'edit.audioTracks':'Audio tracks','edit.subtitles':'Ondertitels','edit.distributor':'Distributeur',
  'edit.country':'Land','edit.language':'Taal','edit.boxSet':'Box Set','edit.edition':'Editie',
  'edit.editionYear':'Editie jaar','edit.editionDate':'Editie datum',
  'edit.purchasePrice':'Aankoopprijs','edit.purchaseDate':'Aankoopdatum','edit.location':'Locatie',
  'edit.extras':"Extra's",'edit.plot':'Verhaal','edit.notes':'Notities','edit.posterUrl':'Poster URL',
  'edit.coverUpload':'Eigen Cover Upload','edit.uploadCover':'🖼 Upload cover',
  'edit.coverHelp':'Afbeelding wordt automatisch geschaald en gecomprimeerd.',
  'edit.imdbId':'IMDb ID','edit.imdbUrl':'IMDb URL','edit.save':'Opslaan','edit.cancel':'Annuleren',
  // detail labels
  'd.genre':'Genre','d.origTitle':'Originele titel','d.country':'Land','d.language':'Taal',
  'd.releaseDate':'Releasedatum','d.runtime':'Speelduur','d.min':' min','d.aspectRatio':'Schermverhouding',
  'd.packaging':'Verpakking','d.region':'Regio','d.imdbRating':'IMDb Rating','d.contentRating':'Kijkwijzer',
  'd.distributor':'Distributeur','d.studio':'Studio','d.boxSet':'Box Set','d.edition':'Editie',
  'd.editionYear':'Editie jaar','d.editionDate':'Editie datum','d.actors':'Acteurs','d.producers':'Producenten',
  'd.audio':'Audio','d.subtitles':'Ondertitels','d.extras':"Extra's",'d.purchasePrice':'Aankoopprijs',
  'd.purchaseDate':'Aankoopdatum','d.location':'Locatie','d.barcode':'Barcode','d.added':'Toegevoegd','d.notes':'Notities',
  // JS messages
  'js.lookingUp':'<span class="spinner"></span> Opzoeken...',
  'js.backendError':'Backend fout (HTTP {0}). Controleer de logs.',
  'js.error':'Fout: {0}',
  'js.alreadyInCollection':'✓ Al in collectie: "{0}"',
  'js.movieFound':'✓ Film gevonden via database',
  'js.movieNotFound':'Film niet gevonden voor barcode {0}. Voeg handmatig toe.',
  'js.unknown':'(onbekend)','js.connectionError':'Verbindingsfout: {0}',
  'js.alreadyInCollectionBtn':'✓ Al in collectie',
  'js.saving':'<span class="spinner"></span> Opslaan...',
  'js.savedToCollection':'✓ "{0}" opgeslagen in collectie!',
  'js.saveFailed':'Opslaan mislukt','js.saveToCollectionBtn':'💾 Opslaan in Collectie',
  'js.moviesSelected':'{0} film(s) geselecteerd',
  'js.noMoviesFound':'Geen films gevonden','js.addFirstDisc':'Voeg je eerste schijfje toe via het Toevoegen tabblad',
  'search.emptyTitle':'Zoek in je collectie','search.emptyDesc':'Typ een titel, regisseur, acteur of genre.','js.noResultsFor':'Geen resultaten voor "{0}".',
  'js.confirmBulkDelete':'{0} film(s) definitief verwijderen?',
  'js.deleting':'Verwijderen...','js.deleted':'✓ {0} film(s) verwijderd',
  'js.fetchingMetadata':'Metadata ophalen voor {0} films…',
  'js.refreshErrors':'⚠ {0} fout(en)',
  'js.refreshResult':'✓ {0} bijgewerkt · {1} overgeslagen',
  'js.confirmDelete':'"{0}" verwijderen uit collectie?',
  'js.fetching':'<span class="spinner"></span> Ophalen...',
  'js.updated':'✓ Bijgewerkt','js.noNewData':'— Geen nieuwe data','js.errorShort':'✕ Fout',
  'js.syncing':'<span class="spinner"></span> Syncen...',
  'js.synced':'✓ Gesynchroniseerd','js.noSourceData':'— Geen brondata',
  'js.saved':'✓ Opgeslagen','js.saveError':'Fout bij opslaan','js.saveBtn':'💾 Opslaan',
  'js.unsavedChanges':'Je hebt onopgeslagen wijzigingen. Wil je opslaan?',
  'js.chooseCover':'Kies eerst een afbeelding om te uploaden',
  'js.uploadingCover':'<span class="spinner"></span> Cover uploaden...',
  'js.uploadFailed':'Upload mislukt','js.coverUploaded':'✓ Eigen cover geupload',
  'js.searchingMovie':'<span class="spinner"></span> Filminfo zoeken...',
  'js.infoFound':'✓ Info gevonden voor "{0}"',
  'js.noInfoFound':'Geen filminfo gevonden. Vul handmatig in.',
  'js.barcodeRequired':'Barcode en titel zijn verplicht',
  'js.added':'✓ "{0}" toegevoegd!',
  'js.processingFile':'<span class="spinner"></span> "{0}" verwerken...',
  'js.previewTitle':'Voorbeeld — {0} ({1} rijen)',
  'js.columnsRecognized':'{0} kolom(men) herkend',
  'js.columnsUnknown':'{0} onbekend (worden genegeerd)',
  'js.showingRows':'Toont {0} van {1} rijen',
  'js.importing':'<span class="spinner"></span> Importeren...',
  'js.importProgress':'Importeren: {0}/{1} (✓{2} ◆{3} ○{4} ✕{5})',
  'js.importCancelled':'⚠ Import handmatig afgebroken.',
  'js.importBtn':'⬆ Importeren',
  'js.importAdded':'Toegevoegd','js.importUpdated':'Bijgewerkt','js.importSkipped':'Overgeslagen','js.importErrors':'Fouten',
  'js.cancellingImport':'⏳ Import wordt afgebroken...',
  'js.cancelFailed':'Stopverzoek mislukt','js.cancelSent':'⏳ Stopverzoek verstuurd. Wacht op afronding...',
  'js.enrichTitle':'Metadata ophalen via TMDb/OMDb... (0/{0})',
  'js.enrichProgress':'Metadata ophalen... ({0}/{1})',
  'js.enrichDone':'Metadata ophalen voltooid!',
  'js.enrichUpdated':'bijgewerkt','js.enrichNotFound':'niet gevonden','js.enrichErrors':'fouten',
  'js.logCount':'{0} logbericht(en)',
  'js.noLogs':'Geen logs gevonden','js.logsLoadError':'Kan logs niet laden: {0}',
  'js.confirmClearLogs':'Alle logberichten definitief wissen?',
  'js.waitingPasskey':'<span class="spinner"></span> Wachten op passkey...',
  'js.noChallenge':'Geen challenge ontvangen van server',
  'js.loginFailed':'Login mislukt','js.passkeyCancelled':'Passkey geannuleerd','js.loginBtn':'🔑 Inloggen',
  'js.loggedOut':'Je bent uitgelogd.',
  'js.passkeyRegistered':'✓ Passkey "{0}" geregistreerd!',
  'js.registrationFailed':'Registratie mislukt','js.passkeyRegCancelled':'Passkey registratie geannuleerd',
  'js.authActive':'ACTIEF','js.authOff':'UIT','js.logins':'logins',
  'js.confirmDeletePasskey':'Deze passkey verwijderen?',
  'js.authEnabled':'Authenticatie ingeschakeld','js.authDisabled':'Authenticatie uitgeschakeld',
  'js.movies':'Films','js.posters':'Posters','js.database':'Database','js.postersSize':'Posters grootte',
  'js.creatingBackup':'<span class="spinner"></span> Backup maken...',
  'js.backupCreated':'✓ Backup "{0}" aangemaakt ({1} KB)',
  'js.noBackups':'Geen backups beschikbaar',
  'js.restore':'↩ Herstellen','js.download':'⬇ Download',
  'js.downloadFailed':'Download mislukt',
  'js.tarGzOnly':'Alleen .tar.gz bestanden worden geaccepteerd',
  'js.uploading':'<span class="spinner"></span> Uploaden...',
  'js.uploadDone':'✓ Backup geüpload',
  'js.confirmRestore':'Backup "{0}" herstellen? Dit overschrijft de huidige films en posters!',
  'js.restoring':'<span class="spinner"></span> Herstellen...',
  'js.restored':'✓ Backup "{0}" hersteld!',
  'js.confirmDeleteBackup':'Backup "{0}" verwijderen?',
  'js.groupConflictTitle':'Groepen niet gevonden',
  'js.groupConflictDesc':'De backup bevat {0} films. De volgende groepen bestaan niet in je huidige database. Kies per groep wat je wilt doen:',
  'js.groupActionCreate':'Groep aanmaken',
  'js.groupActionSkip':'Overslaan (films niet toewijzen)',
  'js.groupActionAssign':'Toewijzen aan: {0}',
  'js.groupConflictApply':'Herstellen',
  'js.cancel':'Annuleren',
  'js.confirmReset1':'ALLE films, posters en logs verwijderen?',
  'js.confirmReset2':'Weet je het zeker? Dit kan niet ongedaan worden gemaakt!',
  'js.resetComplete':'Database gereset!',
  'js.unknownAction':'Onbekende actie','js.actionAdd':'Film toevoegen','js.actionUpdate':'Film bijwerken',
  'js.actionDelete':'Film verwijderen','js.actionBulkDelete':'Bulk verwijderen',
  'js.noQueuedActions':'Geen acties in wachtrij.',
  'js.confirmClearQueue':'Hele offline wachtrij wissen?','js.queueCleared':'Wachtrij geleegd',
  'js.offlineSyncError':'Offline: synchroniseren kan pas zodra je online bent.',
  'js.synchronizing':'<span class="spinner"></span> Synchroniseren...',
  'js.syncComplete':'✓ Synchronisatie voltooid ({0} acties verwerkt)',
  'js.syncPartial':'⚠ Gedeeltelijk gelukt: {0} verwerkt, {1} over',
  'js.syncFailed':'Geen acties verwerkt (backend mogelijk nog niet bereikbaar)',
  'js.sourceSettingsSaved':'Broninstellingen opgeslagen',
  'js.cameraError':'Camera fout: {0}',
  'js.statsTotal':'Totaal: <span>{0}</span> schijfjes | {1}',
  'js.filterCount':'{0} films gevonden',
  'js.queuedMessage':'Actie in wachtrij geplaatst (sync zodra online).',
  'js.unknownMovie':'Onbekende film',
  'js.confirmDeleteCurrent':'"{0}" verwijderen?',
  'js.confirmEditOther':'Deze film is van {0}. Wil je als admin toch bewerken?',
  'js.queuedSave':'⏳ "{0}" staat in wachtrij.',
  'js.inQueue':'⏳ In wachtrij',
  'js.queuedEdit':'⏳ Wijziging in wachtrij.',
  'js.queuedAdd':'⏳ "{0}" in wachtrij.',
  'js.queuedDelete':'⏳ {0} film(s) in wachtrij voor verwijderen',
  'js.mcpSettingsSaved':'MCP instelling opgeslagen',
  'js.advancedSettingsSaved':'Geavanceerde instelling opgeslagen',
  'js.pageTitle':'DiscVault — Collectie Beheer',
  'js.offlineNoCache':'Offline en geen lokale cache beschikbaar. Open de app een keer online om data lokaal op te slaan.',
  'js.backendError':'Backend niet bereikbaar — je ziet gecachte gegevens.',
  'js.offlineCache':'Offline — je ziet gecachte gegevens.',
  'js.confirmDeleteUser':'Weet je zeker dat je gebruiker "{0}" wilt verwijderen? Alle films van deze gebruiker worden ook verwijderd.',
  'js.confirmResetPasskey':'Passkey(s) van "{0}" resetten? De gebruiker moet dan opnieuw een passkey registreren.',
  'js.noGroups':'Geen groepen aangemaakt.',
  'js.confirmDeleteGroup':'Groep "{0}" verwijderen? Films worden ontkoppeld maar niet verwijderd.',
  'edit.groups':'Groepen','edit.noGroup':'Geen groep',
  'bulk.assignGroup':'📁 Groepen beheren','bulk.selectGroups':'Groepen voor geselecteerde films:',
  'bulk.applyGroup':'✓ Opslaan','bulk.cancelGroup':'Annuleren',
  'bulk.selectAtLeastOne':'Selecteer minstens één groep','bulk.assigning':'Toewijzen...',
  'bulk.assignDone':'✓ {0} film(s) aan groep(en) gekoppeld',
  'pwa.title':'Installeer DiscVault','pwa.subtitle':'Voeg toe aan je startscherm voor de beste ervaring','pwa.install':'Installeren',
  'pwa.iosHint':'Tik op <strong style="color:var(--accent)">⬆ Deel</strong> en dan <strong style="color:var(--accent)">Zet op beginscherm</strong>',
  'pwa.close':'Sluiten',
  // nav nieuw
  'nav.lists':'Lijsten',
  // toevoegen submenu
  'toevoegen.scan':'Barcode scannen','toevoegen.manual':'Handmatig','toevoegen.import':'Importeren',
  // lists panel
  'lists.menuWatchlist':'Watchlist','lists.menuWatchHistory':'Kijkgeschiedenis',
  'lists.menuCustomLists':'Eigen lijsten','lists.menuTags':'Labels',
  'lists.watchlistEmpty':'Je watchlist is leeg.<br>Voeg films toe via het detailscherm of via Selecteren in de collectie.',
  'lists.watchHistoryEmpty':'Je hebt nog geen films als bekeken gemarkeerd.',
  // general
  'general.comingSoon':'Binnenkort beschikbaar','general.loading':'Laden...','general.close':'Sluiten',
  // watched popup
  'watched.yesterday':'Gisteren','watched.today':'Vandaag',
  'watched.confirmDate':'Bevestig datum','watched.historyLabel':'Historie',
  // modal watchlist/watched knoppen
  'modal.watchlist':'🔖 Watchlist','modal.inWatchlist':'🔖 In watchlist',
  'modal.watched':'👁 Bekeken','modal.watchedOn':'👁 Bekeken {0}',
  // bulk watchlist
  'bulk.watchlist':'🔖 Watchlist',
  // JS watchlist / datums
  'js.bulkWatchlistAdded':'✓ {0} film(s) toegevoegd aan watchlist',
  'js.onWatchlist':'Op watchlist',
  'js.watchedOn':'Bekeken {0}',
  'js.watchedOnDate':'Bekeken op {0}',
  'js.deleteTitle':'Verwijderen',
  // maanden (voor datumopmaak)
  'months.short':'jan,feb,mrt,apr,mei,jun,jul,aug,sep,okt,nov,dec',
  // groepen (mijn groepen)
  'settings.myGroupsTitle':'📁 Mijn Groepen',
  'settings.myGroupsDesc':'Maak je eigen groep aan en nodig anderen uit om samen een collectie te delen.',
  'settings.myGroupName':'Groepsnaam',
  'settings.myGroupCreate':'+ Aanmaken',
  'settings.groupCreateBtn':'+ Groep aanmaken',
  // uitnodigingen
  'invite.title':'🔔 Uitnodigingen',
  // logs
  'logs.autoRefresh':'Auto-refresh',
  // JS groepen
  'js.noMyGroups':'Je hebt nog geen groepen.',
  'js.myGroupsLoadError':'Fout bij laden groepen.',
  'js.myGroupsOwned':'Beheerd door jou',
  'js.myGroupsMember':'Lid van',
  'js.groupStats':'{0} leden · {1} films',
  'js.groupStatsByOwner':'door {0} · {1} films',
  'js.manageMembersTitle':'Leden & uitnodigingen',
  'js.leaveGroup':'Verlaten',
  'js.myGroupCreated':'Groep "{0}" aangemaakt!',
  'js.confirmDeleteMyGroup':'Groep "{0}" verwijderen? Dit verwijdert ook alle groepskoppelingen.',
  'js.confirmLeaveGroup':'Groep "{0}" verlaten?',
  'js.membersOf':'👥 Leden van "{0}"',
  'js.noMembers':'Geen leden.',
  'js.selfIndicator':' (jij)',
  'js.inviteUser':'Gebruiker uitnodigen',
  'js.usernamePlaceholder':'gebruikersnaam',
  'js.inviteBtn':'📨 Uitnodigen',
  'js.close':'Sluiten',
  'js.groupStatsFull':'{0} leden · {1} films · door {2}',
  'js.manageMembersBtn':'Leden beheren',
  'js.addMemberBtn':'+ Toevoegen',
  'js.inviteSent':'✓ Uitnodiging verstuurd naar {0}',
  'js.noInvites':'Geen openstaande uitnodigingen.',
  'js.inviteFrom':'Uitnodiging van {0}',
  'js.inviteAccept':'✓ Accepteren',
  'js.inviteDecline':'✕ Weigeren',
  'js.inviteLoadError':'Fout bij laden.',
  'js.groupsLoadError':'Fout bij laden groepen',
  'js.passkeyCount':'{0} passkey(s)',
},
en: {
  // nav
  'nav.collection':'Collection','nav.import':'Import','nav.search':'Search','nav.add':'Add','nav.logs':'Logs','nav.settings':'Settings',
  // general
  'general.back':'Back',
  'header.loading':'Loading...','header.offline':'Offline mode','header.queue':'Queue: {0}','header.logout':'↩ Log out','header.menuOpen':'Open menu',
  'scan.title':'Barcode Scanner','scan.placeholder':'Start camera to scan','scan.startCamera':'▶ Start Camera','scan.stop':'⏹ Stop',
  'scan.manualLabel':'Or enter barcode manually','scan.barcodePlaceholder':'E.g. 5051892198103','scan.search':'Search',
  'scan.movieInfo':'Movie Information','scan.format':'Format','scan.location':'Location (optional)','scan.locationPlaceholder':'E.g. Shelf B row 3',
  'scan.hdr':'Dolby Vision / HDR','scan.hdrPlaceholder':'E.g. Dolby Vision, HDR10+, HDR10',
  'scan.audioTracks':'Audio Tracks','scan.audioPlaceholder':'E.g. Dolby Atmos, DTS-HD MA 5.1',
  'scan.subtitles':'Subtitle Tracks','scan.subtitlesPlaceholder':'E.g. NL, EN, DE',
  'scan.save':'💾 Save to Collection','scan.noResult':'Scan a barcode or enter one manually',
  'collection.searchPlaceholder':'Search by title, director, genre...','collection.filterAll':'All',
  'collection.allCollections':'All collections','collection.myMovies':'My movies',
  'collection.selectMode':'☑ Select','collection.refresh':'↻ Refresh',
  'bulk.count':'{0} selected','bulk.selectAll':'Select all','bulk.deselect':'Deselect',
  'bulk.refresh':'↺ Refresh via TMDb/OMDb','bulk.delete':'🗑 Delete','bulk.cancel':'✕ Cancel',
  'bulk.noSelection':'No selection',
  'progress.busy':'Processing...','progress.close':'Close',
  'add.title':'Quick Manual Add',
  'add.description':'Use this when scanning doesn\'t work: enter barcode and title, or use Auto-fill based on title.',
  'add.barcodeLabel':'Barcode / Custom ID *','add.barcodePlaceholder':'Barcode or unique ID',
  'add.titleLabel':'Title *','add.titlePlaceholder':'E.g. The Dark Knight','add.autofill':'🔍 Auto-fill',
  'add.year':'Year','add.format':'Format','add.director':'Director','add.directorPlaceholder':'E.g. Christopher Nolan',
  'add.genre':'Genre','add.genrePlaceholder':'E.g. Action, Drama',
  'add.runtime':'Runtime','add.runtimePlaceholder':'E.g. 152 min',
  'add.rating':'IMDb Rating','add.ratingPlaceholder':'E.g. 9.0',
  'add.hdr':'Dolby Vision / HDR','add.hdrPlaceholder':'E.g. Dolby Vision, HDR10+',
  'add.audioTracks':'Audio Tracks','add.audioPlaceholder':'E.g. Dolby Atmos, DTS:X, TrueHD 7.1',
  'add.subtitles':'Subtitle Tracks','add.subtitlesPlaceholder':'E.g. Dutch, English, German',
  'add.location':'Location','add.locationPlaceholder':'E.g. Shelf A row 2',
  'add.notes':'Notes','add.notesPlaceholder':'Steelbook, limited edition, ...',
  'add.posterUrl':'Poster URL','add.submit':'💾 Add to Collection','add.clear':'✕ Clear',
  'import.title':'Import Collection',
  'import.description':'Import movies from a CSV or XML export from CLZ Movies, iCollect, Blu-ray.com or a custom spreadsheet. Column names are automatically recognized.',
  'import.dropHere':'Drop a file here','import.clickBrowse':'or click to browse · CSV, TSV or XML',
  'import.preview':'Preview','import.duplicates':'On duplicates:','import.skip':'Skip','import.overwrite':'Overwrite',
  'import.enrich':'Auto-fill via TMDb','import.downloadCovers':'Download covers',
  'import.import':'⬆ Import','import.cancel':'⏹ Cancel import','import.otherFile':'← Different file',
  'import.result':'Import Result','import.viewCollection':'📀 View Collection','import.importAnother':'Import another file',
  'import.supportedColumns':'📋 Supported column names',
  'import.csvHelp':'comma, semicolon, tab or pipe as delimiter — automatically detected.',
  'import.xmlHelp':'each repeating element is treated as a movie row; nested tags are merged.',
  'logs.allLevels':'All levels','logs.errors':'🔴 Errors','logs.warnings':'🟡 Warnings','logs.success':'🟢 Success','logs.info':'🔵 Info',
  'logs.allCategories':'All categories','logs.catImport':'⬆ Import','logs.catRefresh':'↺ Refresh','logs.catLookup':'🔍 Lookup',
  'logs.catAdd':'＋ Add','logs.catDelete':'🗑 Delete','logs.catGeneral':'⚙ General',
  'logs.refresh':'↻ Refresh','logs.clear':'🗑 Clear logs',
  'logs.thTimestamp':'Timestamp','logs.thLevel':'Level','logs.thCategory':'Category','logs.thBackends':'Backends','logs.thMessage':'Message',
  'logs.loading':'Loading...',
  'settings.menuGeneral':'General','settings.menuSecurity':'Security','settings.menuBackup':'Backup','settings.menuLogs':'Logs','settings.menuAdvanced':'Advanced','settings.menuNotifications':'Notifications',
  'settings.notifTitle':'🔔 Push Notifications','settings.notifDesc':'Receive notifications on this device for completed imports and invitations.','settings.pushEnable':'🔔 Enable','settings.pushDisable':'🔕 Disable','settings.pushTest':'📣 Send test','settings.pushActive':'Notifications active on this device.','settings.pushInactive':'Notifications disabled on this device.','settings.pushBlocked':'Notifications blocked — change in browser settings.','settings.pushUnsupported':'Push notifications are not supported by this browser.',
  'settings.menuProfile':'Profile',
  'settings.profileTitle':'👤 User Profile',
  'settings.profilePhoto':'Profile Photo','settings.profilePhotoDesc':'Click to upload a photo (max 2MB)','settings.profilePhotoRemove':'Remove',
  'settings.profileUsername':'Username','settings.profileFirstName':'First Name','settings.profileLastName':'Last Name',
  'settings.profileSave':'💾 Save','settings.profileSaved':'✓ Profile saved',
  'settings.profileUsernameRequired':'Enter a username','settings.profileUsernameTaken':'This username is already taken',
  'settings.profilePhotoUpdated':'✓ Photo updated','settings.profilePhotoRemoved':'✓ Photo removed','settings.profilePhotoTooLarge':'Photo is too large (max 2MB)',
  'settings.profilePasskeys':'🔑 Manage Passkeys','settings.profileLogout':'↩ Log out',
  'settings.dbInfo':'Database Information','settings.version':'Version:','settings.versionLoading':'loading...','settings.loading':'Loading...',
  'settings.backup':'Backup & Restore','settings.createBackup':'💾 Create Backup','settings.uploadBackup':'📤 Upload Backup',
  'settings.metadataSources':'Metadata Sources',
  'settings.omdbDesc':'OMDb — movie info via IMDb ID or title',
  'settings.tmdbDesc':'TMDb — movie info via title + year',
  'settings.blurayDesc':'Blu-ray.com scraper (experimental)',
  'settings.blurayDeDesc':'bluray-disc.de scraper (experimental)',
  'settings.sourceHelp':'OMDb and TMDb require an API key (see .env). The scrapers try to fetch HDR/Audio/Subtitles without an official API.',
  'settings.offlineQueue':'📶 Offline Queue',
  'settings.offlineQueueDesc':'Actions performed while offline are temporarily stored here and synchronized later.',
  'settings.syncNow':'↻ Sync now','settings.clearQueue':'🗑 Clear queue',
  'settings.language':'Language','settings.languageDesc':'Choose the interface language.',
  'settings.advanced':'Advanced','settings.openLogs':'Open Logs',
  'settings.debugEnabled':'Debug mode (show IDs)',
  'settings.debugDesc':'Show Movie ID and Person ID in detail views for troubleshooting and debugging.',
  'settings.mcpEnabled':'MCP Server enabled',
  'settings.mcpDesc':'Disable the MCP server if you don\'t use it. This blocks API access for MCP clients.',
  'settings.resetTitle':'⚠ Reset Database',
  'settings.resetDesc':'Deletes all movies, posters and logs. Create a backup first! Authentication settings and passkeys are preserved.',
  'settings.resetButton':'🗑 Wipe Database',
  'settings.authTitle':'🔐 Authentication (Passkey)','settings.authActive':'Authentication active','settings.logout':'↩ Log out',
  'settings.noPasskey':'No passkey registered yet. Register one to enable authentication.',
  'settings.username':'Username','settings.passkeyName':'Passkey name','settings.myPasskey':'My Passkey',
  'settings.passkeyPlaceholder':'E.g. MacBook Touch ID','settings.registerPasskey':'🔑 Register Passkey',
  'settings.addPasskey':'Add extra passkey','settings.name':'Name','settings.addPasskeyPlaceholder':'E.g. iPhone','settings.addPasskeyBtn':'🔑 Add',
  'settings.usersTitle':'👥 User Management','settings.groupsTitle':'📁 Groups','settings.groupName':'Group name',
  'settings.registrationEnabled':'Allow new users to register',
  'settings.registrationDesc':'When disabled, new users cannot create an account from the login screen.',
  'settings.inviteTitle':'✉️ Invite Codes',
  'settings.inviteDesc':'Create temporary codes so new users can register an account.',
  'settings.inviteUsername':'New user\'s username',
  'settings.inviteCreate':'+ Create code',
  'settings.inviteCodeLabel':'Code',
  'settings.inviteCopy':'📋 Copy',
  'settings.inviteExpires':'Expires',
  'settings.inviteRevoke':'Revoke',
  'settings.inviteUsed':'Used',
  'settings.inviteActive':'Active',
  'settings.inviteExpired':'Expired',
  'js.registrationEnabled':'Registration enabled',
  'js.registrationDisabled':'Registration disabled',
  'js.inviteCreated':'Invite code created!',
  'js.inviteCreateError':'Failed to create invite code.',
  'js.inviteCopied':'Code copied!',
  'js.inviteRevoked':'Invite code revoked.',
  'js.noInviteCodes':'No active invite codes.',
  'login.prompt':'Log in with your passkey','login.button':'🔑 Log in','login.recovery':'🔐 Use recovery code',
  'login.register':'✨ Create account',
  'login.registerWithInvite':'✉️ Register with invite code',
  'login.registerUsername':'Username',
  'login.inviteCode':'Invite code',
  'login.inviteCodePlaceholder':'XXXX-XXXX-XXXX',
  'login.registerPasskeyName':'Passkey name',
  'login.registerBtn':'🔑 Register',
  'login.registerFillIn':'Enter a username',
  'login.registerSuccess':'✓ Account created! You are now logged in.',
  'login.registrationDisabled':'Registration has been disabled by the administrator.',
  'login.recoveryUsername':'Username','login.recoveryCode':'Recovery code','login.recoveryBtn':'🔓 Log in with recovery code',
  'login.recoveryFillIn':'Enter username and recovery code',
  'login.recoveryFailed':'Recovery failed',
  'login.newRecoveryAlert':'New recovery code: {0}\n\nSave this code securely! Your passkeys have been removed, register a new passkey in Settings > Security.',
  'recovery.title':'🔑 Recovery Code','recovery.desc':'Save this code securely. You can use it to log in if you lose your passkey.',
  'recovery.dismiss':'Got it, I saved the code',
  'modal.plot':'Plot','modal.edit':'Edit','modal.refreshMeta':'Refresh metadata',
  'modal.syncAll':'Sync all sources','modal.delete':'Delete',
  'modal.copyId':'Copy ID',
  'modal.tabInfo':'Basic Info','modal.tabCast':'Cast & Crew',
  'modal.noCast':'No cast/crew found. Refresh metadata to fetch this data.',
  'js.idCopied':'ID copied','js.copyFailed':'Copy failed',
  'person.inCollection':'In your collection','person.born':'Born','person.died':'Died',
  'person.birthPlace':'Place of birth','person.knownFor':'Known for',
  'person.noBio':'No biography available.','person.as':'as',
  'edit.title':'Edit movie','edit.titleLabel':'Title *','edit.originalTitle':'Original title','edit.sortTitle':'Sort title',
  'edit.year':'Year','edit.releaseDate':'Release date','edit.director':'Director','edit.actors':'Actors',
  'edit.producer':'Producer','edit.studios':'Studios','edit.genre':'Genre','edit.format':'Format',
  'edit.runtime':'Runtime (min)','edit.hdr':'HDR','edit.screenRatio':'Aspect ratio','edit.packaging':'Packaging',
  'edit.regions':'Regions','edit.audienceRating':'Content rating','edit.imdbRating':'IMDb Rating',
  'edit.audioTracks':'Audio tracks','edit.subtitles':'Subtitles','edit.distributor':'Distributor',
  'edit.country':'Country','edit.language':'Language','edit.boxSet':'Box Set','edit.edition':'Edition',
  'edit.editionYear':'Edition year','edit.editionDate':'Edition date',
  'edit.purchasePrice':'Purchase price','edit.purchaseDate':'Purchase date','edit.location':'Location',
  'edit.extras':'Extras','edit.plot':'Plot','edit.notes':'Notes','edit.posterUrl':'Poster URL',
  'edit.coverUpload':'Custom Cover Upload','edit.uploadCover':'🖼 Upload cover',
  'edit.coverHelp':'Image will be automatically scaled and compressed.',
  'edit.imdbId':'IMDb ID','edit.imdbUrl':'IMDb URL','edit.save':'Save','edit.cancel':'Cancel',
  'd.genre':'Genre','d.origTitle':'Original title','d.country':'Country','d.language':'Language',
  'd.releaseDate':'Release date','d.runtime':'Runtime','d.min':' min','d.aspectRatio':'Aspect ratio',
  'd.packaging':'Packaging','d.region':'Region','d.imdbRating':'IMDb Rating','d.contentRating':'Content rating',
  'd.distributor':'Distributor','d.studio':'Studio','d.boxSet':'Box Set','d.edition':'Edition',
  'd.editionYear':'Edition year','d.editionDate':'Edition date','d.actors':'Actors','d.producers':'Producers',
  'd.audio':'Audio','d.subtitles':'Subtitles','d.extras':'Extras','d.purchasePrice':'Purchase price',
  'd.purchaseDate':'Purchase date','d.location':'Location','d.barcode':'Barcode','d.added':'Added','d.notes':'Notes',
  'js.lookingUp':'<span class="spinner"></span> Looking up...',
  'js.backendError':'Backend error (HTTP {0}). Check the logs.',
  'js.error':'Error: {0}',
  'js.alreadyInCollection':'✓ Already in collection: "{0}"',
  'js.movieFound':'✓ Movie found in database',
  'js.movieNotFound':'Movie not found for barcode {0}. Add manually.',
  'js.unknown':'(unknown)','js.connectionError':'Connection error: {0}',
  'js.alreadyInCollectionBtn':'✓ Already in collection',
  'js.saving':'<span class="spinner"></span> Saving...',
  'js.savedToCollection':'✓ "{0}" saved to collection!',
  'js.saveFailed':'Save failed','js.saveToCollectionBtn':'💾 Save to Collection',
  'js.moviesSelected':'{0} movie(s) selected',
  'js.noMoviesFound':'No movies found','js.addFirstDisc':'Add your first disc via the Add tab',
  'search.emptyTitle':'Search your collection','search.emptyDesc':'Type a title, director, actor or genre.','js.noResultsFor':'No results for "{0}".',
  'js.confirmBulkDelete':'Permanently delete {0} movie(s)?',
  'js.deleting':'Deleting...','js.deleted':'✓ {0} movie(s) deleted',
  'js.fetchingMetadata':'Fetching metadata for {0} movies…',
  'js.refreshErrors':'⚠ {0} error(s)',
  'js.refreshResult':'✓ {0} updated · {1} skipped',
  'js.confirmDelete':'Remove "{0}" from collection?',
  'js.fetching':'<span class="spinner"></span> Fetching...',
  'js.updated':'✓ Updated','js.noNewData':'— No new data','js.errorShort':'✕ Error',
  'js.syncing':'<span class="spinner"></span> Syncing...',
  'js.synced':'✓ Synchronized','js.noSourceData':'— No source data',
  'js.saved':'✓ Saved','js.saveError':'Error saving','js.saveBtn':'💾 Save',
  'js.unsavedChanges':'You have unsaved changes. Do you want to save?',
  'js.chooseCover':'Choose an image to upload first',
  'js.uploadingCover':'<span class="spinner"></span> Uploading cover...',
  'js.uploadFailed':'Upload failed','js.coverUploaded':'✓ Custom cover uploaded',
  'js.searchingMovie':'<span class="spinner"></span> Searching movie info...',
  'js.infoFound':'✓ Info found for "{0}"',
  'js.noInfoFound':'No movie info found. Fill in manually.',
  'js.barcodeRequired':'Barcode and title are required',
  'js.added':'✓ "{0}" added!',
  'js.processingFile':'<span class="spinner"></span> Processing "{0}"...',
  'js.previewTitle':'Preview — {0} ({1} rows)',
  'js.columnsRecognized':'{0} column(s) recognized',
  'js.columnsUnknown':'{0} unknown (will be ignored)',
  'js.showingRows':'Showing {0} of {1} rows',
  'js.importing':'<span class="spinner"></span> Importing...',
  'js.importProgress':'Importing: {0}/{1} (✓{2} ◆{3} ○{4} ✕{5})',
  'js.importCancelled':'⚠ Import manually cancelled.',
  'js.importBtn':'⬆ Import',
  'js.importAdded':'Added','js.importUpdated':'Updated','js.importSkipped':'Skipped','js.importErrors':'Errors',
  'js.cancellingImport':'⏳ Cancelling import...',
  'js.cancelFailed':'Cancel request failed','js.cancelSent':'⏳ Cancel request sent. Waiting...',
  'js.enrichTitle':'Fetching metadata via TMDb/OMDb... (0/{0})',
  'js.enrichProgress':'Fetching metadata... ({0}/{1})',
  'js.enrichDone':'Metadata fetch completed!',
  'js.enrichUpdated':'updated','js.enrichNotFound':'not found','js.enrichErrors':'errors',
  'js.logCount':'{0} log message(s)',
  'js.noLogs':'No logs found','js.logsLoadError':'Cannot load logs: {0}',
  'js.confirmClearLogs':'Permanently clear all log messages?',
  'js.waitingPasskey':'<span class="spinner"></span> Waiting for passkey...',
  'js.noChallenge':'No challenge received from server',
  'js.loginFailed':'Login failed','js.passkeyCancelled':'Passkey cancelled','js.loginBtn':'🔑 Log in',
  'js.loggedOut':'You have been logged out.',
  'js.passkeyRegistered':'✓ Passkey "{0}" registered!',
  'js.registrationFailed':'Registration failed','js.passkeyRegCancelled':'Passkey registration cancelled',
  'js.authActive':'ACTIVE','js.authOff':'OFF','js.logins':'logins',
  'js.confirmDeletePasskey':'Delete this passkey?',
  'js.authEnabled':'Authentication enabled','js.authDisabled':'Authentication disabled',
  'js.movies':'Movies','js.posters':'Posters','js.database':'Database','js.postersSize':'Posters size',
  'js.creatingBackup':'<span class="spinner"></span> Creating backup...',
  'js.backupCreated':'✓ Backup "{0}" created ({1} KB)',
  'js.noBackups':'No backups available',
  'js.restore':'↩ Restore','js.download':'⬇ Download',
  'js.downloadFailed':'Download failed',
  'js.tarGzOnly':'Only .tar.gz files are accepted',
  'js.uploading':'<span class="spinner"></span> Uploading...',
  'js.uploadDone':'✓ Backup uploaded',
  'js.confirmRestore':'Restore backup "{0}"? This will overwrite the current movies and posters!',
  'js.restoring':'<span class="spinner"></span> Restoring...',
  'js.restored':'✓ Backup "{0}" restored!',
  'js.confirmDeleteBackup':'Delete backup "{0}"?',
  'js.groupConflictTitle':'Groups not found',
  'js.groupConflictDesc':'The backup contains {0} movies. The following groups do not exist in your current database. Choose what to do for each:',
  'js.groupActionCreate':'Create group',
  'js.groupActionSkip':'Skip (don\'t assign movies)',
  'js.groupActionAssign':'Assign to: {0}',
  'js.groupConflictApply':'Restore',
  'js.cancel':'Cancel',
  'js.confirmReset1':'Delete ALL movies, posters and logs?',
  'js.confirmReset2':'Are you sure? This cannot be undone!',
  'js.resetComplete':'Database reset!',
  'js.unknownAction':'Unknown action','js.actionAdd':'Add movie','js.actionUpdate':'Update movie',
  'js.actionDelete':'Delete movie','js.actionBulkDelete':'Bulk delete',
  'js.noQueuedActions':'No actions in queue.',
  'js.confirmClearQueue':'Clear entire offline queue?','js.queueCleared':'Queue cleared',
  'js.offlineSyncError':'Offline: syncing is only possible when you\'re online.',
  'js.synchronizing':'<span class="spinner"></span> Synchronizing...',
  'js.syncComplete':'✓ Synchronization complete ({0} actions processed)',
  'js.syncPartial':'⚠ Partially successful: {0} processed, {1} remaining',
  'js.syncFailed':'No actions processed (backend may not be reachable yet)',
  'js.sourceSettingsSaved':'Source settings saved',
  'js.cameraError':'Camera error: {0}',
  'js.statsTotal':'Total: <span>{0}</span> discs | {1}',
  'js.filterCount':'{0} movies found',
  'js.queuedMessage':'Action queued (will sync when online).',
  'js.unknownMovie':'Unknown movie',
  'js.confirmDeleteCurrent':'Delete "{0}"?',
  'js.confirmEditOther':'This movie belongs to {0}. Edit as admin anyway?',
  'js.queuedSave':'⏳ "{0}" is queued.',
  'js.inQueue':'⏳ Queued',
  'js.queuedEdit':'⏳ Change queued.',
  'js.queuedAdd':'⏳ "{0}" queued.',
  'js.queuedDelete':'⏳ {0} movie(s) queued for deletion',
  'js.mcpSettingsSaved':'MCP setting saved',
  'js.advancedSettingsSaved':'Advanced setting saved',
  'js.pageTitle':'DiscVault — Collection Manager',
  'js.offlineNoCache':'Offline and no local cache available. Open the app online once to store data locally.',
  'js.backendError':'Backend unreachable — showing cached data.',
  'js.offlineCache':'Offline — showing cached data.',
  'js.confirmDeleteUser':'Are you sure you want to delete user "{0}"? All movies of this user will also be deleted.',
  'js.confirmResetPasskey':'Reset passkey(s) of "{0}"? The user will need to register a new passkey.',
  'js.noGroups':'No groups created.',
  'js.confirmDeleteGroup':'Delete group "{0}"? Movies will be unlinked but not deleted.',
  'edit.groups':'Groups','edit.noGroup':'No group',
  'bulk.assignGroup':'📁 Manage Groups','bulk.selectGroups':'Groups for selected movies:',
  'bulk.applyGroup':'✓ Save','bulk.cancelGroup':'Cancel',
  'bulk.selectAtLeastOne':'Select at least one group','bulk.assigning':'Assigning...',
  'bulk.assignDone':'✓ {0} movie(s) assigned to group(s)',
  'pwa.title':'Install DiscVault','pwa.subtitle':'Add to your home screen for the best experience','pwa.install':'Install',
  'pwa.iosHint':'Tap <strong style="color:var(--accent)">⬆ Share</strong> then <strong style="color:var(--accent)">Add to Home Screen</strong>',
  'pwa.close':'Close',
  // nav new
  'nav.lists':'Lists',
  // add submenu
  'toevoegen.scan':'Scan Barcode','toevoegen.manual':'Manual','toevoegen.import':'Import',
  // lists panel
  'lists.menuWatchlist':'Watchlist','lists.menuWatchHistory':'Watch History',
  'lists.menuCustomLists':'Custom Lists','lists.menuTags':'Tags',
  'lists.watchlistEmpty':'Your watchlist is empty.<br>Add movies via the detail screen or via Select in the collection.',
  'lists.watchHistoryEmpty':'You haven\'t marked any movies as watched yet.',
  // general
  'general.comingSoon':'Coming soon','general.loading':'Loading...','general.close':'Close',
  // watched popup
  'watched.yesterday':'Yesterday','watched.today':'Today',
  'watched.confirmDate':'Confirm date','watched.historyLabel':'History',
  // modal watchlist/watched buttons
  'modal.watchlist':'🔖 Watchlist','modal.inWatchlist':'🔖 In watchlist',
  'modal.watched':'👁 Watched','modal.watchedOn':'👁 Watched {0}',
  // bulk watchlist
  'bulk.watchlist':'🔖 Watchlist',
  // JS watchlist / dates
  'js.bulkWatchlistAdded':'✓ {0} movie(s) added to watchlist',
  'js.onWatchlist':'On watchlist',
  'js.watchedOn':'Watched {0}',
  'js.watchedOnDate':'Watched on {0}',
  'js.deleteTitle':'Delete',
  // months for date formatting
  'months.short':'Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec',
  // groups (my groups)
  'settings.myGroupsTitle':'📁 My Groups',
  'settings.myGroupsDesc':'Create your own group and invite others to share a collection.',
  'settings.myGroupName':'Group name',
  'settings.myGroupCreate':'+ Create',
  'settings.groupCreateBtn':'+ Create group',
  // invitations
  'invite.title':'🔔 Invitations',
  // logs
  'logs.autoRefresh':'Auto-refresh',
  // JS groups
  'js.noMyGroups':'You have no groups yet.',
  'js.myGroupsLoadError':'Error loading groups.',
  'js.myGroupsOwned':'Managed by you',
  'js.myGroupsMember':'Member of',
  'js.groupStats':'{0} members · {1} movies',
  'js.groupStatsByOwner':'by {0} · {1} movies',
  'js.manageMembersTitle':'Members & invitations',
  'js.leaveGroup':'Leave',
  'js.myGroupCreated':'Group "{0}" created!',
  'js.confirmDeleteMyGroup':'Delete group "{0}"? This also removes all group links.',
  'js.confirmLeaveGroup':'Leave group "{0}"?',
  'js.membersOf':'👥 Members of "{0}"',
  'js.noMembers':'No members.',
  'js.selfIndicator':' (you)',
  'js.inviteUser':'Invite user',
  'js.usernamePlaceholder':'username',
  'js.inviteBtn':'📨 Invite',
  'js.close':'Close',
  'js.groupStatsFull':'{0} members · {1} movies · by {2}',
  'js.manageMembersBtn':'Manage members',
  'js.addMemberBtn':'+ Add',
  'js.inviteSent':'✓ Invitation sent to {0}',
  'js.noInvites':'No pending invitations.',
  'js.inviteFrom':'Invitation from {0}',
  'js.inviteAccept':'✓ Accept',
  'js.inviteDecline':'✕ Decline',
  'js.inviteLoadError':'Error loading.',
  'js.groupsLoadError':'Error loading groups',
  'js.passkeyCount':'{0} passkey(s)',
},
fr: {
  // nav
  'nav.collection':'Collection','nav.import':'Import','nav.search':'Rechercher','nav.add':'Ajouter','nav.logs':'Logs','nav.settings':'Paramètres',
  'nav.lists':'Listes',
  // general
  'general.back':'Retour',
  // header
  'header.loading':'Chargement...','header.offline':'Mode hors ligne','header.queue':'File : {0}','header.logout':'↩ Déconnexion','header.menuOpen':'Ouvrir le menu',
  // scan
  'scan.title':'Scanner un code-barres','scan.placeholder':'Démarrer la caméra pour scanner','scan.startCamera':'▶ Démarrer la caméra','scan.stop':'⏹ Arrêter',
  'scan.manualLabel':'Ou saisir le code-barres manuellement','scan.barcodePlaceholder':'Ex. 5051892198103','scan.search':'Rechercher',
  'scan.movieInfo':'Informations du film','scan.format':'Format','scan.location':'Emplacement (optionnel)','scan.locationPlaceholder':'Ex. Étagère B rangée 3',
  'scan.hdr':'Dolby Vision / HDR','scan.hdrPlaceholder':'Ex. Dolby Vision, HDR10+, HDR10',
  'scan.audioTracks':'Pistes audio','scan.audioPlaceholder':'Ex. Dolby Atmos, DTS-HD MA 5.1',
  'scan.subtitles':'Sous-titres','scan.subtitlesPlaceholder':'Ex. NL, EN, DE',
  'scan.save':'💾 Enregistrer dans la collection','scan.noResult':'Scannez un code-barres ou saisissez-le manuellement',
  // collection
  'collection.searchPlaceholder':'Rechercher par titre, réalisateur, genre...','collection.filterAll':'Tout',
  'collection.allCollections':'Toutes les collections','collection.myMovies':'Mes films',
  'collection.selectMode':'☑ Sélectionner','collection.refresh':'↻ Actualiser',
  // bulk
  'bulk.count':'{0} sélectionné(s)','bulk.selectAll':'Tout sélectionner','bulk.deselect':'Désélectionner',
  'bulk.refresh':'↺ Mettre à jour via TMDb/OMDb','bulk.delete':'🗑 Supprimer','bulk.cancel':'✕ Annuler',
  'bulk.noSelection':'Pas de sélection',
  'bulk.watchlist':'🔖 Watchlist',
  'bulk.assignGroup':'📁 Gérer les groupes','bulk.selectGroups':'Groupes pour les films sélectionnés :',
  'bulk.applyGroup':'✓ Enregistrer','bulk.cancelGroup':'Annuler',
  'bulk.selectAtLeastOne':'Sélectionnez au moins un groupe','bulk.assigning':'Attribution...',
  'bulk.assignDone':'✓ {0} film(s) attribué(s) au(x) groupe(s)',
  // progress
  'progress.busy':'En cours...','progress.close':'Fermer',
  // add
  'add.title':'Ajout manuel rapide',
  'add.description':'Utilisez ceci si le scan ne fonctionne pas : saisissez le code-barres et le titre, ou utilisez le remplissage automatique.',
  'add.barcodeLabel':'Code-barres / ID personnalisé *','add.barcodePlaceholder':'Code-barres ou ID unique',
  'add.titleLabel':'Titre *','add.titlePlaceholder':'Ex. The Dark Knight','add.autofill':'🔍 Remplissage auto',
  'add.year':'Année','add.format':'Format','add.director':'Réalisateur','add.directorPlaceholder':'Ex. Christopher Nolan',
  'add.genre':'Genre','add.genrePlaceholder':'Ex. Action, Drame',
  'add.runtime':'Durée','add.runtimePlaceholder':'Ex. 152 min',
  'add.rating':'Note IMDb','add.ratingPlaceholder':'Ex. 9.0',
  'add.hdr':'Dolby Vision / HDR','add.hdrPlaceholder':'Ex. Dolby Vision, HDR10+',
  'add.audioTracks':'Pistes audio','add.audioPlaceholder':'Ex. Dolby Atmos, DTS:X, TrueHD 7.1',
  'add.subtitles':'Sous-titres','add.subtitlesPlaceholder':'Ex. Néerlandais, Anglais, Allemand',
  'add.location':'Emplacement','add.locationPlaceholder':'Ex. Étagère A rangée 2',
  'add.notes':'Notes','add.notesPlaceholder':'Steelbook, édition limitée, ...',
  'add.posterUrl':'URL de l\'affiche','add.submit':'💾 Ajouter à la collection','add.clear':'✕ Effacer',
  // import
  'import.title':'Importer la collection',
  'import.description':'Importez des films depuis une exportation CSV ou XML de CLZ Movies, iCollect, Blu-ray.com ou une feuille de calcul personnalisée. Les noms de colonnes sont reconnus automatiquement.',
  'import.dropHere':'Déposez un fichier ici','import.clickBrowse':'ou cliquez pour parcourir · CSV, TSV ou XML',
  'import.preview':'Aperçu','import.duplicates':'En cas de doublons :','import.skip':'Ignorer','import.overwrite':'Écraser',
  'import.enrich':'Remplissage automatique via TMDb','import.downloadCovers':'Télécharger les couvertures',
  'import.import':'⬆ Importer','import.cancel':'⏹ Annuler l\'import','import.otherFile':'← Autre fichier',
  'import.result':'Résultat de l\'import','import.viewCollection':'📀 Voir la collection','import.importAnother':'Importer un autre fichier',
  'import.supportedColumns':'📋 Colonnes prises en charge',
  'import.csvHelp':'virgule, point-virgule, tabulation ou barre verticale — détecté automatiquement.',
  'import.xmlHelp':'chaque élément répété est traité comme une ligne de film ; les balises imbriquées sont fusionnées.',
  // logs
  'logs.allLevels':'Tous les niveaux','logs.errors':'🔴 Erreurs','logs.warnings':'🟡 Avertissements','logs.success':'🟢 Succès','logs.info':'🔵 Info',
  'logs.allCategories':'Toutes les catégories','logs.catImport':'⬆ Import','logs.catRefresh':'↺ Actualiser','logs.catLookup':'🔍 Recherche',
  'logs.catAdd':'＋ Ajouter','logs.catDelete':'🗑 Supprimer','logs.catGeneral':'⚙ Général',
  'logs.refresh':'↻ Actualiser','logs.clear':'🗑 Effacer les logs',
  'logs.thTimestamp':'Horodatage','logs.thLevel':'Niveau','logs.thCategory':'Catégorie','logs.thBackends':'Backends','logs.thMessage':'Message',
  'logs.loading':'Chargement...',
  'logs.autoRefresh':'Actualisation auto',
  // settings
  'settings.menuGeneral':'Général','settings.menuSecurity':'Sécurité','settings.menuBackup':'Sauvegarde','settings.menuLogs':'Logs','settings.menuAdvanced':'Avancé','settings.menuNotifications':'Notifications',
  'settings.notifTitle':'🔔 Notifications push','settings.notifDesc':'Recevez des notifications sur cet appareil pour les imports terminés et les invitations.','settings.pushEnable':'🔔 Activer','settings.pushDisable':'🔕 Désactiver','settings.pushTest':'📣 Envoyer test','settings.pushActive':'Notifications actives sur cet appareil.','settings.pushInactive':'Notifications désactivées sur cet appareil.','settings.pushBlocked':'Notifications bloquées — modifiez dans les paramètres du navigateur.','settings.pushUnsupported':'Les notifications push ne sont pas prises en charge par ce navigateur.',
  'settings.menuProfile':'Profil',
  'settings.profileTitle':'👤 Profil utilisateur',
  'settings.profilePhoto':'Photo de profil','settings.profilePhotoDesc':'Cliquez pour télécharger une photo (max 2 Mo)','settings.profilePhotoRemove':'Supprimer',
  'settings.profileUsername':'Nom d\'utilisateur','settings.profileFirstName':'Prénom','settings.profileLastName':'Nom de famille',
  'settings.profileSave':'💾 Enregistrer','settings.profileSaved':'✓ Profil enregistré',
  'settings.profileUsernameRequired':'Saisissez un nom d\'utilisateur','settings.profileUsernameTaken':'Ce nom d\'utilisateur est déjà utilisé',
  'settings.profilePhotoUpdated':'✓ Photo mise à jour','settings.profilePhotoRemoved':'✓ Photo supprimée','settings.profilePhotoTooLarge':'Photo trop grande (max 2 Mo)',
  'settings.profilePasskeys':'🔑 Gérer les clés d\'accès','settings.profileLogout':'↩ Déconnexion',
  'settings.dbInfo':'Informations base de données','settings.version':'Version :','settings.versionLoading':'chargement...','settings.loading':'Chargement...',
  'settings.backup':'Sauvegarde & Restauration','settings.createBackup':'💾 Créer une sauvegarde','settings.uploadBackup':'📤 Téléverser',
  'settings.metadataSources':'Sources de métadonnées',
  'settings.omdbDesc':'OMDb — informations sur les films via IMDb ID ou titre',
  'settings.tmdbDesc':'TMDb — informations sur les films via titre + année',
  'settings.blurayDesc':'Scraper Blu-ray.com (expérimental)',
  'settings.blurayDeDesc':'Scraper bluray-disc.de (expérimental)',
  'settings.sourceHelp':'OMDb et TMDb nécessitent une clé API (voir .env). Les scrapers tentent de récupérer HDR/Audio/Sous-titres sans API officielle.',
  'settings.offlineQueue':'📶 File d\'attente hors ligne',
  'settings.offlineQueueDesc':'Les actions effectuées hors ligne sont temporairement stockées ici et synchronisées plus tard.',
  'settings.syncNow':'↻ Synchroniser maintenant','settings.clearQueue':'🗑 Vider la file',
  'settings.language':'Langue','settings.languageDesc':'Choisissez la langue de l\'interface.',
  'settings.advanced':'Avancé','settings.openLogs':'Ouvrir les logs',
  'settings.debugEnabled':'Mode débogage (afficher les ID)',
  'settings.debugDesc':'Afficher l\'ID du film et de la personne dans les fenêtres de détail pour le débogage.',
  'settings.mcpEnabled':'Serveur MCP activé',
  'settings.mcpDesc':'Désactivez le serveur MCP si vous ne l\'utilisez pas. Cela bloque l\'accès API pour les clients MCP.',
  'settings.resetTitle':'⚠ Réinitialiser la base de données',
  'settings.resetDesc':'Supprime tous les films, affiches et logs. Faites d\'abord une sauvegarde ! Les paramètres d\'authentification et les clés d\'accès sont conservés.',
  'settings.resetButton':'🗑 Effacer la base de données',
  'settings.authTitle':'🔐 Authentification (Clé d\'accès)','settings.authActive':'Authentification active','settings.logout':'↩ Déconnexion',
  'settings.noPasskey':'Aucune clé d\'accès enregistrée. Enregistrez-en une pour activer l\'authentification.',
  'settings.username':'Nom d\'utilisateur','settings.passkeyName':'Nom de la clé d\'accès','settings.myPasskey':'Ma clé d\'accès',
  'settings.passkeyPlaceholder':'Ex. MacBook Touch ID','settings.registerPasskey':'🔑 Enregistrer une clé d\'accès',
  'settings.addPasskey':'Ajouter une clé d\'accès supplémentaire','settings.name':'Nom','settings.addPasskeyPlaceholder':'Ex. iPhone','settings.addPasskeyBtn':'🔑 Ajouter',
  'settings.usersTitle':'👥 Gestion des utilisateurs','settings.groupsTitle':'📁 Groupes','settings.groupName':'Nom du groupe',
  'settings.registrationEnabled':'Autoriser les nouveaux utilisateurs à s\'inscrire',
  'settings.registrationDesc':'Si désactivé, les nouveaux utilisateurs ne peuvent pas créer de compte.',
  'settings.inviteTitle':'✉️ Codes d\'invitation',
  'settings.inviteDesc':'Créez des codes temporaires pour permettre à de nouveaux utilisateurs de s\'inscrire.',
  'settings.inviteUsername':'Nom d\'utilisateur du nouvel utilisateur',
  'settings.inviteCreate':'+ Créer un code',
  'settings.inviteCodeLabel':'Code',
  'settings.inviteCopy':'📋 Copier',
  'settings.inviteExpires':'Expire le',
  'settings.inviteRevoke':'Révoquer',
  'settings.inviteUsed':'Utilisé',
  'settings.inviteActive':'Actif',
  'settings.inviteExpired':'Expiré',
  'settings.myGroupsTitle':'📁 Mes Groupes',
  'settings.myGroupsDesc':'Créez votre propre groupe et invitez d\'autres à partager une collection.',
  'settings.myGroupName':'Nom du groupe',
  'settings.myGroupCreate':'+ Créer',
  'settings.groupCreateBtn':'+ Créer un groupe',
  'js.registrationEnabled':'Inscription activée',
  'js.registrationDisabled':'Inscription désactivée',
  'js.inviteCreated':'Code d\'invitation créé !',
  'js.inviteCreateError':'Impossible de créer un code d\'invitation.',
  'js.inviteCopied':'Code copié !',
  'js.inviteRevoked':'Code d\'invitation révoqué.',
  'js.noInviteCodes':'Aucun code d\'invitation actif.',
  // login
  'login.prompt':'Connectez-vous avec votre clé d\'accès','login.button':'🔑 Se connecter','login.recovery':'🔐 Utiliser le code de récupération',
  'login.register':'✨ Créer un compte',
  'login.registerWithInvite':'✉️ S\'inscrire avec un code d\'invitation',
  'login.registerUsername':'Nom d\'utilisateur',
  'login.inviteCode':'Code d\'invitation',
  'login.inviteCodePlaceholder':'XXXX-XXXX-XXXX',
  'login.registerPasskeyName':'Nom de la clé d\'accès',
  'login.registerBtn':'🔑 S\'inscrire',
  'login.registerFillIn':'Saisissez un nom d\'utilisateur',
  'login.registerSuccess':'✓ Compte créé ! Vous êtes maintenant connecté.',
  'login.registrationDisabled':'L\'inscription a été désactivée par l\'administrateur.',
  'login.recoveryUsername':'Nom d\'utilisateur','login.recoveryCode':'Code de récupération','login.recoveryBtn':'🔓 Code de récupération',
  'login.recoveryFillIn':'Saisissez le nom d\'utilisateur et le code de récupération',
  'login.recoveryFailed':'Récupération échouée',
  'login.newRecoveryAlert':'Nouveau code de récupération : {0}\n\nConservez ce code en lieu sûr ! Vos clés d\'accès ont été supprimées, enregistrez-en une nouvelle dans Paramètres > Sécurité.',
  'recovery.title':'🔑 Code de récupération','recovery.desc':'Conservez ce code en lieu sûr. Vous pouvez l\'utiliser si vous perdez votre clé d\'accès.',
  'recovery.dismiss':'Compris, j\'ai sauvegardé le code',
  // modal
  'modal.plot':'Synopsis','modal.edit':'Modifier','modal.refreshMeta':'Actualiser les métadonnées',
  'modal.syncAll':'Synchroniser toutes les sources','modal.delete':'Supprimer',
  'modal.copyId':'Copier l\'ID',
  'modal.tabInfo':'Informations','modal.tabCast':'Distribution',
  'modal.noCast':'Aucune distribution trouvée. Actualisez les métadonnées pour les récupérer.',
  'modal.watchlist':'🔖 Watchlist','modal.inWatchlist':'🔖 Dans la watchlist',
  'modal.watched':'👁 Vu','modal.watchedOn':'👁 Vu le {0}',
  'js.idCopied':'ID copié','js.copyFailed':'Échec de la copie',
  // person
  'person.inCollection':'Dans votre collection','person.born':'Né(e)','person.died':'Décédé(e)',
  'person.birthPlace':'Lieu de naissance','person.knownFor':'Connu pour',
  'person.noBio':'Aucune biographie disponible.','person.as':'en tant que',
  // edit
  'edit.title':'Modifier le film','edit.titleLabel':'Titre *','edit.originalTitle':'Titre original','edit.sortTitle':'Titre de tri',
  'edit.year':'Année','edit.releaseDate':'Date de sortie','edit.director':'Réalisateur','edit.actors':'Acteurs',
  'edit.producer':'Producteur','edit.studios':'Studios','edit.genre':'Genre','edit.format':'Format',
  'edit.runtime':'Durée (min)','edit.hdr':'HDR','edit.screenRatio':'Format d\'image','edit.packaging':'Emballage',
  'edit.regions':'Régions','edit.audienceRating':'Classification','edit.imdbRating':'Note IMDb',
  'edit.audioTracks':'Pistes audio','edit.subtitles':'Sous-titres','edit.distributor':'Distributeur',
  'edit.country':'Pays','edit.language':'Langue','edit.boxSet':'Coffret','edit.edition':'Édition',
  'edit.editionYear':'Année d\'édition','edit.editionDate':'Date d\'édition',
  'edit.purchasePrice':'Prix d\'achat','edit.purchaseDate':'Date d\'achat','edit.location':'Emplacement',
  'edit.extras':'Bonus','edit.plot':'Synopsis','edit.notes':'Notes','edit.posterUrl':'URL de l\'affiche',
  'edit.coverUpload':'Couverture personnalisée','edit.uploadCover':'🖼 Téléverser la couverture',
  'edit.coverHelp':'L\'image sera automatiquement mise à l\'échelle et compressée.',
  'edit.imdbId':'ID IMDb','edit.imdbUrl':'URL IMDb','edit.save':'Enregistrer','edit.cancel':'Annuler',
  'edit.groups':'Groupes','edit.noGroup':'Aucun groupe',
  // detail labels
  'd.genre':'Genre','d.origTitle':'Titre original','d.country':'Pays','d.language':'Langue',
  'd.releaseDate':'Date de sortie','d.runtime':'Durée','d.min':' min','d.aspectRatio':'Format d\'image',
  'd.packaging':'Emballage','d.region':'Région','d.imdbRating':'Note IMDb','d.contentRating':'Classification',
  'd.distributor':'Distributeur','d.studio':'Studio','d.boxSet':'Coffret','d.edition':'Édition',
  'd.editionYear':'Année d\'édition','d.editionDate':'Date d\'édition','d.actors':'Acteurs','d.producers':'Producteurs',
  'd.audio':'Audio','d.subtitles':'Sous-titres','d.extras':'Bonus','d.purchasePrice':'Prix d\'achat',
  'd.purchaseDate':'Date d\'achat','d.location':'Emplacement','d.barcode':'Code-barres','d.added':'Ajouté','d.notes':'Notes',
  // JS messages
  'js.lookingUp':'<span class="spinner"></span> Recherche...',
  'js.backendError':'Erreur backend (HTTP {0}). Vérifiez les logs.',
  'js.error':'Erreur : {0}',
  'js.alreadyInCollection':'✓ Déjà dans la collection : "{0}"',
  'js.movieFound':'✓ Film trouvé dans la base de données',
  'js.movieNotFound':'Film introuvable pour le code-barres {0}. Ajoutez-le manuellement.',
  'js.unknown':'(inconnu)','js.connectionError':'Erreur de connexion : {0}',
  'js.alreadyInCollectionBtn':'✓ Déjà dans la collection',
  'js.saving':'<span class="spinner"></span> Enregistrement...',
  'js.savedToCollection':'✓ "{0}" enregistré dans la collection !',
  'js.saveFailed':'Échec de l\'enregistrement','js.saveToCollectionBtn':'💾 Enregistrer dans la collection',
  'js.moviesSelected':'{0} film(s) sélectionné(s)',
  'js.noMoviesFound':'Aucun film trouvé','js.addFirstDisc':'Ajoutez votre premier disque via l\'onglet Ajouter',
  'search.emptyTitle':'Rechercher dans votre collection','search.emptyDesc':'Tapez un titre, réalisateur, acteur ou genre.','js.noResultsFor':'Aucun résultat pour "{0}".',
  'js.confirmBulkDelete':'Supprimer définitivement {0} film(s) ?',
  'js.deleting':'Suppression...','js.deleted':'✓ {0} film(s) supprimé(s)',
  'js.fetchingMetadata':'Récupération des métadonnées pour {0} films…',
  'js.refreshErrors':'⚠ {0} erreur(s)',
  'js.refreshResult':'✓ {0} mis à jour · {1} ignoré(s)',
  'js.confirmDelete':'Supprimer "{0}" de la collection ?',
  'js.fetching':'<span class="spinner"></span> Récupération...',
  'js.updated':'✓ Mis à jour','js.noNewData':'— Aucune nouvelle donnée','js.errorShort':'✕ Erreur',
  'js.syncing':'<span class="spinner"></span> Synchronisation...',
  'js.synced':'✓ Synchronisé','js.noSourceData':'— Aucune donnée source',
  'js.saved':'✓ Enregistré','js.saveError':'Erreur lors de l\'enregistrement','js.saveBtn':'💾 Enregistrer',
  'js.unsavedChanges':'Vous avez des modifications non enregistrées. Voulez-vous enregistrer ?',
  'js.chooseCover':'Choisissez d\'abord une image à téléverser',
  'js.uploadingCover':'<span class="spinner"></span> Téléversement de la couverture...',
  'js.uploadFailed':'Échec du téléversement','js.coverUploaded':'✓ Couverture personnalisée téléversée',
  'js.searchingMovie':'<span class="spinner"></span> Recherche d\'informations sur le film...',
  'js.infoFound':'✓ Informations trouvées pour "{0}"',
  'js.noInfoFound':'Aucune information sur le film. Remplissez manuellement.',
  'js.barcodeRequired':'Le code-barres et le titre sont obligatoires',
  'js.added':'✓ "{0}" ajouté !',
  'js.processingFile':'<span class="spinner"></span> Traitement de "{0}"...',
  'js.previewTitle':'Aperçu — {0} ({1} lignes)',
  'js.columnsRecognized':'{0} colonne(s) reconnue(s)',
  'js.columnsUnknown':'{0} inconnue(s) (seront ignorées)',
  'js.showingRows':'Affichage de {0} sur {1} lignes',
  'js.importing':'<span class="spinner"></span> Import en cours...',
  'js.importProgress':'Import : {0}/{1} (✓{2} ◆{3} ○{4} ✕{5})',
  'js.importCancelled':'⚠ Import annulé manuellement.',
  'js.importBtn':'⬆ Importer',
  'js.importAdded':'Ajouté','js.importUpdated':'Mis à jour','js.importSkipped':'Ignoré','js.importErrors':'Erreurs',
  'js.cancellingImport':'⏳ Annulation de l\'import...',
  'js.cancelFailed':'Échec de la demande d\'annulation','js.cancelSent':'⏳ Demande d\'annulation envoyée. En attente...',
  'js.enrichTitle':'Récupération des métadonnées via TMDb/OMDb... (0/{0})',
  'js.enrichProgress':'Récupération des métadonnées... ({0}/{1})',
  'js.enrichDone':'Récupération des métadonnées terminée !',
  'js.enrichUpdated':'mis à jour','js.enrichNotFound':'introuvable','js.enrichErrors':'erreurs',
  'js.logCount':'{0} message(s) de log',
  'js.noLogs':'Aucun log trouvé','js.logsLoadError':'Impossible de charger les logs : {0}',
  'js.confirmClearLogs':'Effacer définitivement tous les messages de log ?',
  'js.waitingPasskey':'<span class="spinner"></span> En attente de la clé d\'accès...',
  'js.noChallenge':'Aucun défi reçu du serveur',
  'js.loginFailed':'Échec de connexion','js.passkeyCancelled':'Clé d\'accès annulée','js.loginBtn':'🔑 Se connecter',
  'js.loggedOut':'Vous avez été déconnecté.',
  'js.passkeyRegistered':'✓ Clé d\'accès "{0}" enregistrée !',
  'js.registrationFailed':'Échec de l\'inscription','js.passkeyRegCancelled':'Inscription de la clé d\'accès annulée',
  'js.authActive':'ACTIF','js.authOff':'DÉSACTIVÉ','js.logins':'connexions',
  'js.confirmDeletePasskey':'Supprimer cette clé d\'accès ?',
  'js.authEnabled':'Authentification activée','js.authDisabled':'Authentification désactivée',
  'js.movies':'Films','js.posters':'Affiches','js.database':'Base de données','js.postersSize':'Taille des affiches',
  'js.creatingBackup':'<span class="spinner"></span> Création de la sauvegarde...',
  'js.backupCreated':'✓ Sauvegarde "{0}" créée ({1} Ko)',
  'js.noBackups':'Aucune sauvegarde disponible',
  'js.restore':'↩ Restaurer','js.download':'⬇ Télécharger',
  'js.downloadFailed':'Échec du téléchargement',
  'js.tarGzOnly':'Seuls les fichiers .tar.gz sont acceptés',
  'js.uploading':'<span class="spinner"></span> Téléversement...',
  'js.uploadDone':'✓ Sauvegarde téléversée',
  'js.confirmRestore':'Restaurer la sauvegarde "{0}" ? Cela écrasera les films et affiches actuels !',
  'js.restoring':'<span class="spinner"></span> Restauration...',
  'js.restored':'✓ Sauvegarde "{0}" restaurée !',
  'js.confirmDeleteBackup':'Supprimer la sauvegarde "{0}" ?',
  'js.groupConflictTitle':'Groupes introuvables',
  'js.groupConflictDesc':'La sauvegarde contient {0} films. Les groupes suivants n\'existent pas dans votre base de données. Choisissez ce que vous souhaitez faire :',
  'js.groupActionCreate':'Créer le groupe',
  'js.groupActionSkip':'Ignorer (ne pas attribuer les films)',
  'js.groupActionAssign':'Attribuer à : {0}',
  'js.groupConflictApply':'Restaurer',
  'js.cancel':'Annuler',
  'js.confirmReset1':'Supprimer TOUS les films, affiches et logs ?',
  'js.confirmReset2':'Êtes-vous sûr ? Cette action est irréversible !',
  'js.resetComplete':'Base de données réinitialisée !',
  'js.unknownAction':'Action inconnue','js.actionAdd':'Ajouter un film','js.actionUpdate':'Mettre à jour le film',
  'js.actionDelete':'Supprimer le film','js.actionBulkDelete':'Suppression en masse',
  'js.noQueuedActions':'Aucune action en file d\'attente.',
  'js.confirmClearQueue':'Vider toute la file d\'attente hors ligne ?','js.queueCleared':'File d\'attente vidée',
  'js.offlineSyncError':'Hors ligne : la synchronisation n\'est possible qu\'en ligne.',
  'js.synchronizing':'<span class="spinner"></span> Synchronisation...',
  'js.syncComplete':'✓ Synchronisation terminée ({0} actions traitées)',
  'js.syncPartial':'⚠ Partiellement réussi : {0} traité(s), {1} restant(s)',
  'js.syncFailed':'Aucune action traitée (le backend n\'est peut-être pas encore accessible)',
  'js.sourceSettingsSaved':'Paramètres sources enregistrés',
  'js.cameraError':'Erreur caméra : {0}',
  'js.statsTotal':'Total : <span>{0}</span> disques | {1}',
  'js.filterCount':'{0} film(s) trouvé(s)',
  'js.queuedMessage':'Action mise en file d\'attente (sera synchronisée en ligne).',
  'js.unknownMovie':'Film inconnu',
  'js.confirmDeleteCurrent':'Supprimer "{0}" ?',
  'js.confirmEditOther':'Ce film appartient à {0}. Modifier en tant qu\'administrateur quand même ?',
  'js.queuedSave':'⏳ "{0}" est en file d\'attente.',
  'js.inQueue':'⏳ En file d\'attente',
  'js.queuedEdit':'⏳ Modification en file d\'attente.',
  'js.queuedAdd':'⏳ "{0}" en file d\'attente.',
  'js.queuedDelete':'⏳ {0} film(s) en file d\'attente pour suppression',
  'js.mcpSettingsSaved':'Paramètre MCP enregistré',
  'js.advancedSettingsSaved':'Paramètre avancé enregistré',
  'js.pageTitle':'DiscVault — Gestionnaire de collection',
  'js.offlineNoCache':'Hors ligne et aucun cache local disponible. Ouvrez l\'application en ligne une fois pour stocker les données localement.',
  'js.backendError':'Backend inaccessible — affichage des données en cache.',
  'js.offlineCache':'Hors ligne — affichage des données en cache.',
  'js.confirmDeleteUser':'Êtes-vous sûr de vouloir supprimer l\'utilisateur "{0}" ? Tous ses films seront également supprimés.',
  'js.confirmResetPasskey':'Réinitialiser la ou les clé(s) d\'accès de "{0}" ? L\'utilisateur devra en enregistrer une nouvelle.',
  'js.noGroups':'Aucun groupe créé.',
  'js.confirmDeleteGroup':'Supprimer le groupe "{0}" ? Les films seront dissociés mais non supprimés.',
  // pwa
  'pwa.title':'Installer DiscVault','pwa.subtitle':'Ajoutez à votre écran d\'accueil pour la meilleure expérience','pwa.install':'Installer',
  'pwa.iosHint':'Appuyez sur <strong style="color:var(--accent)">⬆ Partager</strong> puis <strong style="color:var(--accent)">Sur l\'écran d\'accueil</strong>',
  'pwa.close':'Fermer',
  // general
  'general.comingSoon':'Bientôt disponible','general.loading':'Chargement...','general.close':'Fermer',
  // add/toevoegen submenu
  'toevoegen.scan':'Scanner un code-barres','toevoegen.manual':'Manuel','toevoegen.import':'Importer',
  // lists
  'lists.menuWatchlist':'Watchlist','lists.menuWatchHistory':'Historique de visionnage',
  'lists.menuCustomLists':'Listes personnalisées','lists.menuTags':'Étiquettes',
  'lists.watchlistEmpty':'Votre watchlist est vide.<br>Ajoutez des films via l\'écran de détail ou via Sélectionner dans la collection.',
  'lists.watchHistoryEmpty':'Vous n\'avez pas encore marqué de films comme vus.',
  // watched popup
  'watched.yesterday':'Hier','watched.today':'Aujourd\'hui',
  'watched.confirmDate':'Confirmer la date','watched.historyLabel':'Historique',
  // invitations
  'invite.title':'🔔 Invitations',
  // JS watchlist / dates
  'js.bulkWatchlistAdded':'✓ {0} film(s) ajouté(s) à la watchlist',
  'js.onWatchlist':'Dans la watchlist',
  'js.watchedOn':'Vu le {0}',
  'js.watchedOnDate':'Vu le {0}',
  'js.deleteTitle':'Supprimer',
  // months for date formatting
  'months.short':'jan,fév,mar,avr,mai,jun,jul,aoû,sep,oct,nov,déc',
  // JS groups
  'js.noMyGroups':'Vous n\'avez encore aucun groupe.',
  'js.myGroupsLoadError':'Erreur lors du chargement des groupes.',
  'js.myGroupsOwned':'Géré par vous',
  'js.myGroupsMember':'Membre de',
  'js.groupStats':'{0} membre(s) · {1} film(s)',
  'js.groupStatsByOwner':'par {0} · {1} film(s)',
  'js.manageMembersTitle':'Membres & invitations',
  'js.leaveGroup':'Quitter',
  'js.myGroupCreated':'Groupe "{0}" créé !',
  'js.confirmDeleteMyGroup':'Supprimer le groupe "{0}" ? Cela supprimera également tous les liens de groupe.',
  'js.confirmLeaveGroup':'Quitter le groupe "{0}" ?',
  'js.membersOf':'👥 Membres de "{0}"',
  'js.noMembers':'Aucun membre.',
  'js.selfIndicator':' (vous)',
  'js.inviteUser':'Inviter un utilisateur',
  'js.usernamePlaceholder':'nom d\'utilisateur',
  'js.inviteBtn':'📨 Inviter',
  'js.close':'Fermer',
  'js.groupStatsFull':'{0} membre(s) · {1} film(s) · par {2}',
  'js.manageMembersBtn':'Gérer les membres',
  'js.addMemberBtn':'+ Ajouter',
  'js.inviteSent':'✓ Invitation envoyée à {0}',
  'js.noInvites':'Aucune invitation en attente.',
  'js.inviteFrom':'Invitation de {0}',
  'js.inviteAccept':'✓ Accepter',
  'js.inviteDecline':'✕ Refuser',
  'js.inviteLoadError':'Erreur lors du chargement.',
  'js.groupsLoadError':'Erreur lors du chargement des groupes',
  'js.passkeyCount':'{0} clé(s) d\'accès',
  'nav.lists':'Listes',
},
de: {
  // nav
  'nav.collection':'Sammlung','nav.import':'Import','nav.search':'Suchen','nav.add':'Hinzufügen','nav.logs':'Logs','nav.settings':'Einstellungen','nav.lists':'Listen',
  // general
  'general.back':'Zurück',
  'header.loading':'Laden...','header.offline':'Offline-Modus','header.queue':'Warteschlange: {0}','header.logout':'↩ Abmelden','header.menuOpen':'Menü öffnen',
  'scan.title':'Barcode scannen','scan.placeholder':'Kamera starten zum Scannen','scan.startCamera':'▶ Kamera starten','scan.stop':'⏹ Stopp',
  'scan.manualLabel':'Oder Barcode manuell eingeben','scan.barcodePlaceholder':'z.B. 5051892198103','scan.search':'Suchen',
  'scan.movieInfo':'Filminformationen','scan.format':'Format','scan.location':'Standort (optional)','scan.locationPlaceholder':'z.B. Regal B Reihe 3',
  'scan.hdr':'Dolby Vision / HDR','scan.hdrPlaceholder':'z.B. Dolby Vision, HDR10+, HDR10',
  'scan.audioTracks':'Audiospuren','scan.audioPlaceholder':'z.B. Dolby Atmos, DTS-HD MA 5.1',
  'scan.subtitles':'Untertitelspuren','scan.subtitlesPlaceholder':'z.B. NL, EN, DE',
  'scan.save':'💾 In Sammlung speichern','scan.noResult':'Barcode scannen oder manuell eingeben',
  'collection.searchPlaceholder':'Nach Titel, Regisseur, Genre suchen...','collection.filterAll':'Alle',
  'collection.allCollections':'Alle Sammlungen','collection.myMovies':'Meine Filme',
  'collection.selectMode':'☑ Auswählen','collection.refresh':'↻ Aktualisieren',
  'bulk.count':'{0} ausgewählt','bulk.selectAll':'Alle auswählen','bulk.deselect':'Auswahl aufheben',
  'bulk.refresh':'↺ Über TMDb/OMDb aktualisieren','bulk.delete':'🗑 Löschen','bulk.cancel':'✕ Abbrechen',
  'bulk.noSelection':'Keine Auswahl',
  'bulk.watchlist':'🔖 Watchlist',
  'bulk.assignGroup':'📁 Gruppen verwalten','bulk.selectGroups':'Gruppen für ausgewählte Filme:',
  'bulk.applyGroup':'✓ Speichern','bulk.cancelGroup':'Abbrechen',
  'bulk.selectAtLeastOne':'Mindestens eine Gruppe auswählen','bulk.assigning':'Wird zugewiesen...',
  'bulk.assignDone':'✓ {0} Film(e) der/den Gruppe(n) zugewiesen',
  'progress.busy':'Wird verarbeitet...','progress.close':'Schließen',
  'add.title':'Schnell manuell hinzufügen',
  'add.description':'Nutze dies, wenn das Scannen nicht klappt: Barcode und Titel eingeben oder Auto-Ausfüllen verwenden.',
  'add.barcodeLabel':'Barcode / Eigene ID *','add.barcodePlaceholder':'Barcode oder eindeutige ID',
  'add.titleLabel':'Titel *','add.titlePlaceholder':'z.B. The Dark Knight','add.autofill':'🔍 Auto-Ausfüllen',
  'add.year':'Jahr','add.format':'Format','add.director':'Regisseur','add.directorPlaceholder':'z.B. Christopher Nolan',
  'add.genre':'Genre','add.genrePlaceholder':'z.B. Action, Drama',
  'add.runtime':'Laufzeit','add.runtimePlaceholder':'z.B. 152 min',
  'add.rating':'IMDb-Bewertung','add.ratingPlaceholder':'z.B. 9.0',
  'add.hdr':'Dolby Vision / HDR','add.hdrPlaceholder':'z.B. Dolby Vision, HDR10+',
  'add.audioTracks':'Audiospuren','add.audioPlaceholder':'z.B. Dolby Atmos, DTS:X, TrueHD 7.1',
  'add.subtitles':'Untertitelspuren','add.subtitlesPlaceholder':'z.B. Niederländisch, Englisch, Deutsch',
  'add.location':'Standort','add.locationPlaceholder':'z.B. Regal A Reihe 2',
  'add.notes':'Notizen','add.notesPlaceholder':'Steelbook, limitierte Edition, ...',
  'add.posterUrl':'Poster-URL','add.submit':'💾 Zur Sammlung hinzufügen','add.clear':'✕ Leeren',
  'import.title':'Sammlung importieren',
  'import.description':'Filme aus einem CSV- oder XML-Export von CLZ Movies, iCollect, Blu-ray.com oder einer eigenen Tabelle importieren. Spaltennamen werden automatisch erkannt.',
  'import.dropHere':'Datei hierher ziehen','import.clickBrowse':'oder klicken zum Durchsuchen · CSV, TSV oder XML',
  'import.preview':'Vorschau','import.duplicates':'Bei Duplikaten:','import.skip':'Überspringen','import.overwrite':'Überschreiben',
  'import.enrich':'Auto-Ausfüllen via TMDb','import.downloadCovers':'Cover herunterladen',
  'import.import':'⬆ Importieren','import.cancel':'⏹ Import abbrechen','import.otherFile':'← Andere Datei',
  'import.result':'Import-Ergebnis','import.viewCollection':'📀 Sammlung anzeigen','import.importAnother':'Weitere Datei importieren',
  'import.supportedColumns':'📋 Unterstützte Spaltennamen',
  'import.csvHelp':'Komma, Semikolon, Tab oder Pipe als Trennzeichen — automatisch erkannt.',
  'import.xmlHelp':'Jedes wiederkehrende Element wird als Filmzeile behandelt; verschachtelte Tags werden zusammengeführt.',
  'logs.allLevels':'Alle Ebenen','logs.errors':'🔴 Fehler','logs.warnings':'🟡 Warnungen','logs.success':'🟢 Erfolg','logs.info':'🔵 Info',
  'logs.allCategories':'Alle Kategorien','logs.catImport':'⬆ Import','logs.catRefresh':'↺ Aktualisieren','logs.catLookup':'🔍 Suche',
  'logs.catAdd':'＋ Hinzufügen','logs.catDelete':'🗑 Löschen','logs.catGeneral':'⚙ Allgemein',
  'logs.refresh':'↻ Aktualisieren','logs.clear':'🗑 Logs löschen',
  'logs.thTimestamp':'Zeitstempel','logs.thLevel':'Ebene','logs.thCategory':'Kategorie','logs.thBackends':'Backends','logs.thMessage':'Nachricht',
  'logs.loading':'Laden...','logs.autoRefresh':'Auto-Aktualisierung',
  'settings.menuGeneral':'Allgemein','settings.menuSecurity':'Sicherheit','settings.menuBackup':'Backup','settings.menuLogs':'Logs','settings.menuAdvanced':'Erweitert','settings.menuNotifications':'Benachrichtigungen',
  'settings.notifTitle':'🔔 Push-Benachrichtigungen','settings.notifDesc':'Erhalte Benachrichtigungen auf diesem Gerät für abgeschlossene Importe und Einladungen.','settings.pushEnable':'🔔 Aktivieren','settings.pushDisable':'🔕 Deaktivieren','settings.pushTest':'📣 Test senden','settings.pushActive':'Benachrichtigungen aktiv auf diesem Gerät.','settings.pushInactive':'Benachrichtigungen auf diesem Gerät deaktiviert.','settings.pushBlocked':'Benachrichtigungen blockiert — in den Browsereinstellungen ändern.','settings.pushUnsupported':'Push-Benachrichtigungen werden von diesem Browser nicht unterstützt.',
  'settings.menuProfile':'Profil',
  'settings.profileTitle':'👤 Benutzerprofil',
  'settings.profilePhoto':'Profilfoto','settings.profilePhotoDesc':'Klicken zum Hochladen (max. 2 MB)','settings.profilePhotoRemove':'Entfernen',
  'settings.profileUsername':'Benutzername','settings.profileFirstName':'Vorname','settings.profileLastName':'Nachname',
  'settings.profileSave':'💾 Speichern','settings.profileSaved':'✓ Profil gespeichert',
  'settings.profileUsernameRequired':'Benutzernamen eingeben','settings.profileUsernameTaken':'Dieser Benutzername ist bereits vergeben',
  'settings.profilePhotoUpdated':'✓ Foto aktualisiert','settings.profilePhotoRemoved':'✓ Foto entfernt','settings.profilePhotoTooLarge':'Foto zu groß (max. 2 MB)',
  'settings.profilePasskeys':'🔑 Passkeys verwalten','settings.profileLogout':'↩ Abmelden',
  'settings.dbInfo':'Datenbankinformationen','settings.version':'Version:','settings.versionLoading':'wird geladen...','settings.loading':'Laden...',
  'settings.backup':'Backup & Wiederherstellung','settings.createBackup':'💾 Backup erstellen','settings.uploadBackup':'📤 Backup hochladen',
  'settings.metadataSources':'Metadatenquellen',
  'settings.omdbDesc':'OMDb — Filminfos über IMDb-ID oder Titel',
  'settings.tmdbDesc':'TMDb — Filminfos über Titel + Jahr',
  'settings.blurayDesc':'Blu-ray.com-Scraper (experimentell)',
  'settings.blurayDeDesc':'bluray-disc.de-Scraper (experimentell)',
  'settings.sourceHelp':'OMDb und TMDb benötigen einen API-Schlüssel (siehe .env). Die Scraper rufen HDR/Audio/Untertitel ohne offizielle API ab.',
  'settings.offlineQueue':'📶 Offline-Warteschlange',
  'settings.offlineQueueDesc':'Offline durchgeführte Aktionen werden hier vorübergehend gespeichert und später synchronisiert.',
  'settings.syncNow':'↻ Jetzt synchronisieren','settings.clearQueue':'🗑 Warteschlange leeren',
  'settings.language':'Sprache','settings.languageDesc':'Sprache der Benutzeroberfläche wählen.',
  'settings.advanced':'Erweitert','settings.openLogs':'Logs öffnen',
  'settings.debugEnabled':'Debug-Modus (IDs anzeigen)',
  'settings.debugDesc':'Film-ID und Personen-ID in Detailansichten zur Fehlerbehebung anzeigen.',
  'settings.mcpEnabled':'MCP-Server aktiviert',
  'settings.mcpDesc':'Deaktiviere den MCP-Server, wenn du ihn nicht verwendest. Dadurch wird der API-Zugriff für MCP-Clients gesperrt.',
  'settings.resetTitle':'⚠ Datenbank zurücksetzen',
  'settings.resetDesc':'Löscht alle Filme, Poster und Logs. Erstelle zuerst ein Backup! Authentifizierungseinstellungen und Passkeys bleiben erhalten.',
  'settings.resetButton':'🗑 Datenbank löschen',
  'settings.authTitle':'🔐 Authentifizierung (Passkey)','settings.authActive':'Authentifizierung aktiv','settings.logout':'↩ Abmelden',
  'settings.noPasskey':'Noch kein Passkey registriert. Registriere einen, um die Authentifizierung zu aktivieren.',
  'settings.username':'Benutzername','settings.passkeyName':'Passkey-Name','settings.myPasskey':'Mein Passkey',
  'settings.passkeyPlaceholder':'z.B. MacBook Touch ID','settings.registerPasskey':'🔑 Passkey registrieren',
  'settings.addPasskey':'Weiteren Passkey hinzufügen','settings.name':'Name','settings.addPasskeyPlaceholder':'z.B. iPhone','settings.addPasskeyBtn':'🔑 Hinzufügen',
  'settings.usersTitle':'👥 Benutzerverwaltung','settings.groupsTitle':'📁 Gruppen','settings.groupName':'Gruppenname',
  'settings.registrationEnabled':'Neue Benutzer dürfen sich registrieren',
  'settings.registrationDesc':'Wenn deaktiviert, können neue Benutzer kein Konto erstellen.',
  'settings.inviteTitle':'✉️ Einladungscodes',
  'settings.inviteDesc':'Erstelle temporäre Codes, mit denen neue Benutzer ein Konto registrieren können.',
  'settings.inviteUsername':'Benutzername des neuen Benutzers',
  'settings.inviteCreate':'+ Code erstellen',
  'settings.inviteCodeLabel':'Code',
  'settings.inviteCopy':'📋 Kopieren',
  'settings.inviteExpires':'Läuft ab',
  'settings.inviteRevoke':'Widerrufen',
  'settings.inviteUsed':'Verwendet',
  'settings.inviteActive':'Aktiv',
  'settings.inviteExpired':'Abgelaufen',
  'settings.myGroupsTitle':'📁 Meine Gruppen',
  'settings.myGroupsDesc':'Erstelle deine eigene Gruppe und lade andere ein, eine Sammlung zu teilen.',
  'settings.myGroupName':'Gruppenname',
  'settings.myGroupCreate':'+ Erstellen',
  'settings.groupCreateBtn':'+ Gruppe erstellen',
  'js.registrationEnabled':'Registrierung aktiviert',
  'js.registrationDisabled':'Registrierung deaktiviert',
  'js.inviteCreated':'Einladungscode erstellt!',
  'js.inviteCreateError':'Einladungscode konnte nicht erstellt werden.',
  'js.inviteCopied':'Code kopiert!',
  'js.inviteRevoked':'Einladungscode widerrufen.',
  'js.noInviteCodes':'Keine aktiven Einladungscodes.',
  'login.prompt':'Mit Passkey anmelden','login.button':'🔑 Anmelden','login.recovery':'🔐 Wiederherstellungscode verwenden',
  'login.register':'✨ Konto erstellen',
  'login.registerWithInvite':'✉️ Mit Einladungscode registrieren',
  'login.registerUsername':'Benutzername',
  'login.inviteCode':'Einladungscode',
  'login.inviteCodePlaceholder':'XXXX-XXXX-XXXX',
  'login.registerPasskeyName':'Passkey-Name',
  'login.registerBtn':'🔑 Registrieren',
  'login.registerFillIn':'Benutzernamen eingeben',
  'login.registerSuccess':'✓ Konto erstellt! Du bist jetzt angemeldet.',
  'login.registrationDisabled':'Die Registrierung wurde vom Administrator deaktiviert.',
  'login.recoveryUsername':'Benutzername','login.recoveryCode':'Wiederherstellungscode','login.recoveryBtn':'🔓 Mit Wiederherstellungscode anmelden',
  'login.recoveryFillIn':'Benutzername und Wiederherstellungscode eingeben',
  'login.recoveryFailed':'Wiederherstellung fehlgeschlagen',
  'login.newRecoveryAlert':'Neuer Wiederherstellungscode: {0}\n\nBewahre diesen Code sicher auf! Deine Passkeys wurden entfernt. Registriere einen neuen Passkey unter Einstellungen > Sicherheit.',
  'recovery.title':'🔑 Wiederherstellungscode','recovery.desc':'Bewahre diesen Code sicher auf. Du kannst ihn verwenden, wenn du deinen Passkey verlierst.',
  'recovery.dismiss':'Verstanden, ich habe den Code gespeichert',
  'modal.plot':'Handlung','modal.edit':'Bearbeiten','modal.refreshMeta':'Metadaten aktualisieren',
  'modal.syncAll':'Alle Quellen synchronisieren','modal.delete':'Löschen',
  'modal.copyId':'ID kopieren',
  'modal.tabInfo':'Basisinfos','modal.tabCast':'Besetzung & Crew',
  'modal.noCast':'Keine Besetzung/Crew gefunden. Metadaten aktualisieren, um diese abzurufen.',
  'modal.watchlist':'🔖 Watchlist','modal.inWatchlist':'🔖 In Watchlist',
  'modal.watched':'👁 Gesehen','modal.watchedOn':'👁 Gesehen {0}',
  'js.idCopied':'ID kopiert','js.copyFailed':'Kopieren fehlgeschlagen',
  'person.inCollection':'In deiner Sammlung','person.born':'Geboren','person.died':'Gestorben',
  'person.birthPlace':'Geburtsort','person.knownFor':'Bekannt für',
  'person.noBio':'Keine Biografie verfügbar.','person.as':'als',
  'edit.title':'Film bearbeiten','edit.titleLabel':'Titel *','edit.originalTitle':'Originaltitel','edit.sortTitle':'Sortiertitel',
  'edit.year':'Jahr','edit.releaseDate':'Erscheinungsdatum','edit.director':'Regisseur','edit.actors':'Schauspieler',
  'edit.producer':'Produzent','edit.studios':'Studios','edit.genre':'Genre','edit.format':'Format',
  'edit.runtime':'Laufzeit (Min.)','edit.hdr':'HDR','edit.screenRatio':'Bildformat','edit.packaging':'Verpackung',
  'edit.regions':'Regionen','edit.audienceRating':'Altersfreigabe','edit.imdbRating':'IMDb-Bewertung',
  'edit.audioTracks':'Audiospuren','edit.subtitles':'Untertitel','edit.distributor':'Verleih',
  'edit.country':'Land','edit.language':'Sprache','edit.boxSet':'Box-Set','edit.edition':'Edition',
  'edit.editionYear':'Editionsjahr','edit.editionDate':'Editionsdatum',
  'edit.purchasePrice':'Kaufpreis','edit.purchaseDate':'Kaufdatum','edit.location':'Standort',
  'edit.extras':'Extras','edit.plot':'Handlung','edit.notes':'Notizen','edit.posterUrl':'Poster-URL',
  'edit.coverUpload':'Eigenes Cover hochladen','edit.uploadCover':'🖼 Cover hochladen',
  'edit.coverHelp':'Das Bild wird automatisch skaliert und komprimiert.',
  'edit.imdbId':'IMDb-ID','edit.imdbUrl':'IMDb-URL','edit.save':'Speichern','edit.cancel':'Abbrechen',
  'edit.groups':'Gruppen','edit.noGroup':'Keine Gruppe',
  'd.genre':'Genre','d.origTitle':'Originaltitel','d.country':'Land','d.language':'Sprache',
  'd.releaseDate':'Erscheinungsdatum','d.runtime':'Laufzeit','d.min':' Min.','d.aspectRatio':'Bildformat',
  'd.packaging':'Verpackung','d.region':'Region','d.imdbRating':'IMDb-Bewertung','d.contentRating':'Altersfreigabe',
  'd.distributor':'Verleih','d.studio':'Studio','d.boxSet':'Box-Set','d.edition':'Edition',
  'd.editionYear':'Editionsjahr','d.editionDate':'Editionsdatum','d.actors':'Schauspieler','d.producers':'Produzenten',
  'd.audio':'Audio','d.subtitles':'Untertitel','d.extras':'Extras','d.purchasePrice':'Kaufpreis',
  'd.purchaseDate':'Kaufdatum','d.location':'Standort','d.barcode':'Barcode','d.added':'Hinzugefügt','d.notes':'Notizen',
  'js.lookingUp':'<span class="spinner"></span> Wird gesucht...',
  'js.backendError':'Backend-Fehler (HTTP {0}). Bitte Logs prüfen.',
  'js.error':'Fehler: {0}',
  'js.alreadyInCollection':'✓ Bereits in der Sammlung: "{0}"',
  'js.movieFound':'✓ Film in der Datenbank gefunden',
  'js.movieNotFound':'Film für Barcode {0} nicht gefunden. Bitte manuell hinzufügen.',
  'js.unknown':'(unbekannt)','js.connectionError':'Verbindungsfehler: {0}',
  'js.alreadyInCollectionBtn':'✓ Bereits in der Sammlung',
  'js.saving':'<span class="spinner"></span> Wird gespeichert...',
  'js.savedToCollection':'✓ "{0}" in der Sammlung gespeichert!',
  'js.saveFailed':'Speichern fehlgeschlagen','js.saveToCollectionBtn':'💾 In Sammlung speichern',
  'js.moviesSelected':'{0} Film(e) ausgewählt',
  'js.noMoviesFound':'Keine Filme gefunden','js.addFirstDisc':'Ersten Datenträger über die Registerkarte Hinzufügen hinzufügen',
  'search.emptyTitle':'Sammlung durchsuchen','search.emptyDesc':'Titel, Regisseur, Schauspieler oder Genre eingeben.','js.noResultsFor':'Keine Ergebnisse für "{0}".',
  'js.confirmBulkDelete':'{0} Film(e) endgültig löschen?',
  'js.deleting':'Wird gelöscht...','js.deleted':'✓ {0} Film(e) gelöscht',
  'js.fetchingMetadata':'Metadaten für {0} Filme werden abgerufen…',
  'js.refreshErrors':'⚠ {0} Fehler',
  'js.refreshResult':'✓ {0} aktualisiert · {1} übersprungen',
  'js.confirmDelete':'"{0}" aus der Sammlung entfernen?',
  'js.fetching':'<span class="spinner"></span> Wird abgerufen...',
  'js.updated':'✓ Aktualisiert','js.noNewData':'— Keine neuen Daten','js.errorShort':'✕ Fehler',
  'js.syncing':'<span class="spinner"></span> Wird synchronisiert...',
  'js.synced':'✓ Synchronisiert','js.noSourceData':'— Keine Quelldaten',
  'js.saved':'✓ Gespeichert','js.saveError':'Fehler beim Speichern','js.saveBtn':'💾 Speichern',
  'js.unsavedChanges':'Du hast ungespeicherte Änderungen. Möchtest du speichern?',
  'js.chooseCover':'Zuerst ein Bild zum Hochladen auswählen',
  'js.uploadingCover':'<span class="spinner"></span> Cover wird hochgeladen...',
  'js.uploadFailed':'Hochladen fehlgeschlagen','js.coverUploaded':'✓ Eigenes Cover hochgeladen',
  'js.searchingMovie':'<span class="spinner"></span> Filminfos werden gesucht...',
  'js.infoFound':'✓ Infos für "{0}" gefunden',
  'js.noInfoFound':'Keine Filminfos gefunden. Bitte manuell ausfüllen.',
  'js.barcodeRequired':'Barcode und Titel sind erforderlich',
  'js.added':'✓ "{0}" hinzugefügt!',
  'js.processingFile':'<span class="spinner"></span> "{0}" wird verarbeitet...',
  'js.previewTitle':'Vorschau — {0} ({1} Zeilen)',
  'js.columnsRecognized':'{0} Spalte(n) erkannt',
  'js.columnsUnknown':'{0} unbekannt (werden ignoriert)',
  'js.showingRows':'{0} von {1} Zeilen werden angezeigt',
  'js.importing':'<span class="spinner"></span> Wird importiert...',
  'js.importProgress':'Import: {0}/{1} (✓{2} ◆{3} ○{4} ✕{5})',
  'js.importCancelled':'⚠ Import manuell abgebrochen.',
  'js.importBtn':'⬆ Importieren',
  'js.importAdded':'Hinzugefügt','js.importUpdated':'Aktualisiert','js.importSkipped':'Übersprungen','js.importErrors':'Fehler',
  'js.cancellingImport':'⏳ Import wird abgebrochen...',
  'js.cancelFailed':'Abbruchanforderung fehlgeschlagen','js.cancelSent':'⏳ Abbruchanforderung gesendet. Warten...',
  'js.enrichTitle':'Metadaten über TMDb/OMDb werden abgerufen... (0/{0})',
  'js.enrichProgress':'Metadaten werden abgerufen... ({0}/{1})',
  'js.enrichDone':'Metadatenabruf abgeschlossen!',
  'js.enrichUpdated':'aktualisiert','js.enrichNotFound':'nicht gefunden','js.enrichErrors':'Fehler',
  'js.logCount':'{0} Lognachricht(en)',
  'js.noLogs':'Keine Logs gefunden','js.logsLoadError':'Logs können nicht geladen werden: {0}',
  'js.confirmClearLogs':'Alle Lognachrichten endgültig löschen?',
  'js.waitingPasskey':'<span class="spinner"></span> Auf Passkey warten...',
  'js.noChallenge':'Keine Challenge vom Server erhalten',
  'js.loginFailed':'Anmeldung fehlgeschlagen','js.passkeyCancelled':'Passkey abgebrochen','js.loginBtn':'🔑 Anmelden',
  'js.loggedOut':'Du wurdest abgemeldet.',
  'js.passkeyRegistered':'✓ Passkey "{0}" registriert!',
  'js.registrationFailed':'Registrierung fehlgeschlagen','js.passkeyRegCancelled':'Passkey-Registrierung abgebrochen',
  'js.authActive':'AKTIV','js.authOff':'AUS','js.logins':'Anmeldungen',
  'js.confirmDeletePasskey':'Diesen Passkey löschen?',
  'js.authEnabled':'Authentifizierung aktiviert','js.authDisabled':'Authentifizierung deaktiviert',
  'js.movies':'Filme','js.posters':'Poster','js.database':'Datenbank','js.postersSize':'Poster-Größe',
  'js.creatingBackup':'<span class="spinner"></span> Backup wird erstellt...',
  'js.backupCreated':'✓ Backup "{0}" erstellt ({1} KB)',
  'js.noBackups':'Keine Backups verfügbar',
  'js.restore':'↩ Wiederherstellen','js.download':'⬇ Herunterladen',
  'js.downloadFailed':'Herunterladen fehlgeschlagen',
  'js.tarGzOnly':'Nur .tar.gz-Dateien werden akzeptiert',
  'js.uploading':'<span class="spinner"></span> Wird hochgeladen...',
  'js.uploadDone':'✓ Backup hochgeladen',
  'js.confirmRestore':'Backup "{0}" wiederherstellen? Dies überschreibt die aktuellen Filme und Poster!',
  'js.restoring':'<span class="spinner"></span> Wird wiederhergestellt...',
  'js.restored':'✓ Backup "{0}" wiederhergestellt!',
  'js.confirmDeleteBackup':'Backup "{0}" löschen?',
  'js.groupConflictTitle':'Gruppen nicht gefunden',
  'js.groupConflictDesc':'Das Backup enthält {0} Filme. Die folgenden Gruppen existieren nicht in deiner aktuellen Datenbank. Wähle für jede aus:',
  'js.groupActionCreate':'Gruppe erstellen',
  'js.groupActionSkip':'Überspringen (Filme nicht zuweisen)',
  'js.groupActionAssign':'Zuweisen zu: {0}',
  'js.groupConflictApply':'Wiederherstellen',
  'js.cancel':'Abbrechen',
  'js.confirmReset1':'ALLE Filme, Poster und Logs löschen?',
  'js.confirmReset2':'Bist du sicher? Dies kann nicht rückgängig gemacht werden!',
  'js.resetComplete':'Datenbank zurückgesetzt!',
  'js.unknownAction':'Unbekannte Aktion','js.actionAdd':'Film hinzufügen','js.actionUpdate':'Film aktualisieren',
  'js.actionDelete':'Film löschen','js.actionBulkDelete':'Massen-Löschen',
  'js.noQueuedActions':'Keine Aktionen in der Warteschlange.',
  'js.confirmClearQueue':'Gesamte Offline-Warteschlange leeren?','js.queueCleared':'Warteschlange geleert',
  'js.offlineSyncError':'Offline: Synchronisierung ist erst möglich, wenn du online bist.',
  'js.synchronizing':'<span class="spinner"></span> Wird synchronisiert...',
  'js.syncComplete':'✓ Synchronisierung abgeschlossen ({0} Aktionen verarbeitet)',
  'js.syncPartial':'⚠ Teilweise erfolgreich: {0} verarbeitet, {1} verbleibend',
  'js.syncFailed':'Keine Aktionen verarbeitet (Backend möglicherweise noch nicht erreichbar)',
  'js.sourceSettingsSaved':'Quelleinstellungen gespeichert',
  'js.cameraError':'Kamerafehler: {0}',
  'js.statsTotal':'Gesamt: <span>{0}</span> Datenträger | {1}',
  'js.filterCount':'{0} Filme gefunden',
  'js.queuedMessage':'Aktion in die Warteschlange gestellt (wird synchronisiert, wenn online).',
  'js.unknownMovie':'Unbekannter Film',
  'js.confirmDeleteCurrent':'"{0}" löschen?',
  'js.confirmEditOther':'Dieser Film gehört {0}. Trotzdem als Administrator bearbeiten?',
  'js.queuedSave':'⏳ "{0}" steht in der Warteschlange.',
  'js.inQueue':'⏳ In Warteschlange',
  'js.queuedEdit':'⏳ Änderung in der Warteschlange.',
  'js.queuedAdd':'⏳ "{0}" in der Warteschlange.',
  'js.queuedDelete':'⏳ {0} Film(e) zum Löschen in der Warteschlange',
  'js.mcpSettingsSaved':'MCP-Einstellung gespeichert',
  'js.advancedSettingsSaved':'Erweiterte Einstellung gespeichert',
  'js.pageTitle':'DiscVault — Sammlungsverwaltung',
  'js.offlineNoCache':'Offline und kein lokaler Cache verfügbar. App einmal online öffnen, um Daten lokal zu speichern.',
  'js.backendError':'Backend nicht erreichbar — gecachte Daten werden angezeigt.',
  'js.offlineCache':'Offline — gecachte Daten werden angezeigt.',
  'js.confirmDeleteUser':'Benutzer "{0}" wirklich löschen? Alle Filme dieses Benutzers werden ebenfalls gelöscht.',
  'js.confirmResetPasskey':'Passkey(s) von "{0}" zurücksetzen? Der Benutzer muss dann einen neuen Passkey registrieren.',
  'js.noGroups':'Keine Gruppen erstellt.',
  'js.confirmDeleteGroup':'Gruppe "{0}" löschen? Filme werden getrennt, aber nicht gelöscht.',
  'pwa.title':'DiscVault installieren','pwa.subtitle':'Zum Startbildschirm hinzufügen für das beste Erlebnis','pwa.install':'Installieren',
  'pwa.iosHint':'Tippe auf <strong style="color:var(--accent)">⬆ Teilen</strong> und dann auf <strong style="color:var(--accent)">Zum Home-Bildschirm</strong>',
  'pwa.close':'Schließen',
  'toevoegen.scan':'Barcode scannen','toevoegen.manual':'Manuell','toevoegen.import':'Importieren',
  'lists.menuWatchlist':'Watchlist','lists.menuWatchHistory':'Wiedergabeverlauf',
  'lists.menuCustomLists':'Eigene Listen','lists.menuTags':'Tags',
  'lists.watchlistEmpty':'Deine Watchlist ist leer.<br>Füge Filme über die Detailansicht oder über Auswählen in der Sammlung hinzu.',
  'lists.watchHistoryEmpty':'Du hast noch keine Filme als gesehen markiert.',
  'general.comingSoon':'Demnächst verfügbar','general.loading':'Laden...','general.close':'Schließen',
  'watched.yesterday':'Gestern','watched.today':'Heute',
  'watched.confirmDate':'Datum bestätigen','watched.historyLabel':'Verlauf',
  'invite.title':'🔔 Einladungen',
  'js.bulkWatchlistAdded':'✓ {0} Film(e) zur Watchlist hinzugefügt',
  'js.onWatchlist':'Auf der Watchlist',
  'js.watchedOn':'Gesehen {0}',
  'js.watchedOnDate':'Gesehen am {0}',
  'js.deleteTitle':'Löschen',
  'months.short':'Jan,Feb,Mär,Apr,Mai,Jun,Jul,Aug,Sep,Okt,Nov,Dez',
  'js.noMyGroups':'Du hast noch keine Gruppen.',
  'js.myGroupsLoadError':'Fehler beim Laden der Gruppen.',
  'js.myGroupsOwned':'Von dir verwaltet',
  'js.myGroupsMember':'Mitglied von',
  'js.groupStats':'{0} Mitglied(er) · {1} Film(e)',
  'js.groupStatsByOwner':'von {0} · {1} Film(e)',
  'js.manageMembersTitle':'Mitglieder & Einladungen',
  'js.leaveGroup':'Verlassen',
  'js.myGroupCreated':'Gruppe "{0}" erstellt!',
  'js.confirmDeleteMyGroup':'Gruppe "{0}" löschen? Dadurch werden auch alle Gruppenverknüpfungen entfernt.',
  'js.confirmLeaveGroup':'Gruppe "{0}" verlassen?',
  'js.membersOf':'👥 Mitglieder von "{0}"',
  'js.noMembers':'Keine Mitglieder.',
  'js.selfIndicator':' (du)',
  'js.inviteUser':'Benutzer einladen',
  'js.usernamePlaceholder':'Benutzername',
  'js.inviteBtn':'📨 Einladen',
  'js.close':'Schließen',
  'js.groupStatsFull':'{0} Mitglied(er) · {1} Film(e) · von {2}',
  'js.manageMembersBtn':'Mitglieder verwalten',
  'js.addMemberBtn':'+ Hinzufügen',
  'js.inviteSent':'✓ Einladung an {0} gesendet',
  'js.noInvites':'Keine ausstehenden Einladungen.',
  'js.inviteFrom':'Einladung von {0}',
  'js.inviteAccept':'✓ Annehmen',
  'js.inviteDecline':'✕ Ablehnen',
  'js.inviteLoadError':'Fehler beim Laden.',
  'js.groupsLoadError':'Fehler beim Laden der Gruppen',
  'js.passkeyCount':'{0} Passkey(s)',
},
es: {
  // nav
  'nav.collection':'Colección','nav.import':'Importar','nav.search':'Buscar','nav.add':'Añadir','nav.logs':'Registros','nav.settings':'Ajustes','nav.lists':'Listas',
  // general
  'general.back':'Volver',
  'header.loading':'Cargando...','header.offline':'Modo sin conexión','header.queue':'Cola: {0}','header.logout':'↩ Cerrar sesión','header.menuOpen':'Abrir menú',
  'scan.title':'Escanear código de barras','scan.placeholder':'Iniciar cámara para escanear','scan.startCamera':'▶ Iniciar cámara','scan.stop':'⏹ Detener',
  'scan.manualLabel':'O introducir código de barras manualmente','scan.barcodePlaceholder':'Ej. 5051892198103','scan.search':'Buscar',
  'scan.movieInfo':'Información de la película','scan.format':'Formato','scan.location':'Ubicación (opcional)','scan.locationPlaceholder':'Ej. Estante B fila 3',
  'scan.hdr':'Dolby Vision / HDR','scan.hdrPlaceholder':'Ej. Dolby Vision, HDR10+, HDR10',
  'scan.audioTracks':'Pistas de audio','scan.audioPlaceholder':'Ej. Dolby Atmos, DTS-HD MA 5.1',
  'scan.subtitles':'Pistas de subtítulos','scan.subtitlesPlaceholder':'Ej. NL, EN, DE',
  'scan.save':'💾 Guardar en colección','scan.noResult':'Escanea un código de barras o introdúcelo manualmente',
  'collection.searchPlaceholder':'Buscar por título, director, género...','collection.filterAll':'Todo',
  'collection.allCollections':'Todas las colecciones','collection.myMovies':'Mis películas',
  'collection.selectMode':'☑ Seleccionar','collection.refresh':'↻ Actualizar',
  'bulk.count':'{0} seleccionado(s)','bulk.selectAll':'Seleccionar todo','bulk.deselect':'Deseleccionar',
  'bulk.refresh':'↺ Actualizar via TMDb/OMDb','bulk.delete':'🗑 Eliminar','bulk.cancel':'✕ Cancelar',
  'bulk.noSelection':'Sin selección',
  'bulk.watchlist':'🔖 Watchlist',
  'bulk.assignGroup':'📁 Gestionar grupos','bulk.selectGroups':'Grupos para las películas seleccionadas:',
  'bulk.applyGroup':'✓ Guardar','bulk.cancelGroup':'Cancelar',
  'bulk.selectAtLeastOne':'Selecciona al menos un grupo','bulk.assigning':'Asignando...',
  'bulk.assignDone':'✓ {0} película(s) asignada(s) al/los grupo(s)',
  'progress.busy':'Procesando...','progress.close':'Cerrar',
  'add.title':'Añadir manualmente (rápido)',
  'add.description':'Usa esto si el escaneo no funciona: introduce el código de barras y el título, o usa el relleno automático.',
  'add.barcodeLabel':'Código de barras / ID personalizado *','add.barcodePlaceholder':'Código de barras o ID único',
  'add.titleLabel':'Título *','add.titlePlaceholder':'Ej. The Dark Knight','add.autofill':'🔍 Relleno automático',
  'add.year':'Año','add.format':'Formato','add.director':'Director','add.directorPlaceholder':'Ej. Christopher Nolan',
  'add.genre':'Género','add.genrePlaceholder':'Ej. Acción, Drama',
  'add.runtime':'Duración','add.runtimePlaceholder':'Ej. 152 min',
  'add.rating':'Puntuación IMDb','add.ratingPlaceholder':'Ej. 9.0',
  'add.hdr':'Dolby Vision / HDR','add.hdrPlaceholder':'Ej. Dolby Vision, HDR10+',
  'add.audioTracks':'Pistas de audio','add.audioPlaceholder':'Ej. Dolby Atmos, DTS:X, TrueHD 7.1',
  'add.subtitles':'Pistas de subtítulos','add.subtitlesPlaceholder':'Ej. Holandés, Inglés, Alemán',
  'add.location':'Ubicación','add.locationPlaceholder':'Ej. Estante A fila 2',
  'add.notes':'Notas','add.notesPlaceholder':'Steelbook, edición limitada, ...',
  'add.posterUrl':'URL del póster','add.submit':'💾 Añadir a la colección','add.clear':'✕ Limpiar',
  'import.title':'Importar colección',
  'import.description':'Importa películas desde una exportación CSV o XML de CLZ Movies, iCollect, Blu-ray.com o una hoja de cálculo propia. Los nombres de columna se reconocen automáticamente.',
  'import.dropHere':'Arrastra un archivo aquí','import.clickBrowse':'o haz clic para explorar · CSV, TSV o XML',
  'import.preview':'Vista previa','import.duplicates':'Con duplicados:','import.skip':'Omitir','import.overwrite':'Sobrescribir',
  'import.enrich':'Relleno automático via TMDb','import.downloadCovers':'Descargar portadas',
  'import.import':'⬆ Importar','import.cancel':'⏹ Cancelar importación','import.otherFile':'← Otro archivo',
  'import.result':'Resultado de la importación','import.viewCollection':'📀 Ver colección','import.importAnother':'Importar otro archivo',
  'import.supportedColumns':'📋 Nombres de columna admitidos',
  'import.csvHelp':'coma, punto y coma, tabulación o barra vertical — detectado automáticamente.',
  'import.xmlHelp':'cada elemento repetido se trata como una fila de película; las etiquetas anidadas se combinan.',
  'logs.allLevels':'Todos los niveles','logs.errors':'🔴 Errores','logs.warnings':'🟡 Advertencias','logs.success':'🟢 Éxito','logs.info':'🔵 Info',
  'logs.allCategories':'Todas las categorías','logs.catImport':'⬆ Importar','logs.catRefresh':'↺ Actualizar','logs.catLookup':'🔍 Búsqueda',
  'logs.catAdd':'＋ Añadir','logs.catDelete':'🗑 Eliminar','logs.catGeneral':'⚙ General',
  'logs.refresh':'↻ Actualizar','logs.clear':'🗑 Borrar registros',
  'logs.thTimestamp':'Marca de tiempo','logs.thLevel':'Nivel','logs.thCategory':'Categoría','logs.thBackends':'Backends','logs.thMessage':'Mensaje',
  'logs.loading':'Cargando...','logs.autoRefresh':'Actualización automática',
  'settings.menuGeneral':'General','settings.menuSecurity':'Seguridad','settings.menuBackup':'Copia de seguridad','settings.menuLogs':'Registros','settings.menuAdvanced':'Avanzado','settings.menuNotifications':'Notificaciones',
  'settings.notifTitle':'🔔 Notificaciones push','settings.notifDesc':'Recibe notificaciones en este dispositivo para importaciones completadas e invitaciones.','settings.pushEnable':'🔔 Activar','settings.pushDisable':'🔕 Desactivar','settings.pushTest':'📣 Enviar prueba','settings.pushActive':'Notificaciones activas en este dispositivo.','settings.pushInactive':'Notificaciones desactivadas en este dispositivo.','settings.pushBlocked':'Notificaciones bloqueadas — cámbialo en la configuración del navegador.','settings.pushUnsupported':'Las notificaciones push no son compatibles con este navegador.',
  'settings.menuProfile':'Perfil',
  'settings.profileTitle':'👤 Perfil de usuario',
  'settings.profilePhoto':'Foto de perfil','settings.profilePhotoDesc':'Haz clic para subir una foto (máx. 2 MB)','settings.profilePhotoRemove':'Eliminar',
  'settings.profileUsername':'Nombre de usuario','settings.profileFirstName':'Nombre','settings.profileLastName':'Apellidos',
  'settings.profileSave':'💾 Guardar','settings.profileSaved':'✓ Perfil guardado',
  'settings.profileUsernameRequired':'Introduce un nombre de usuario','settings.profileUsernameTaken':'Este nombre de usuario ya está en uso',
  'settings.profilePhotoUpdated':'✓ Foto actualizada','settings.profilePhotoRemoved':'✓ Foto eliminada','settings.profilePhotoTooLarge':'Foto demasiado grande (máx. 2 MB)',
  'settings.profilePasskeys':'🔑 Gestionar passkeys','settings.profileLogout':'↩ Cerrar sesión',
  'settings.dbInfo':'Información de la base de datos','settings.version':'Versión:','settings.versionLoading':'cargando...','settings.loading':'Cargando...',
  'settings.backup':'Copia de seguridad y restauración','settings.createBackup':'💾 Crear copia de seguridad','settings.uploadBackup':'📤 Subir copia de seguridad',
  'settings.metadataSources':'Fuentes de metadatos',
  'settings.omdbDesc':'OMDb — información de películas via ID de IMDb o título',
  'settings.tmdbDesc':'TMDb — información de películas via título + año',
  'settings.blurayDesc':'Scraper Blu-ray.com (experimental)',
  'settings.blurayDeDesc':'Scraper bluray-disc.de (experimental)',
  'settings.sourceHelp':'OMDb y TMDb requieren una clave API (ver .env). Los scrapers intentan obtener HDR/Audio/Subtítulos sin API oficial.',
  'settings.offlineQueue':'📶 Cola sin conexión',
  'settings.offlineQueueDesc':'Las acciones realizadas sin conexión se almacenan temporalmente aquí y se sincronizan más tarde.',
  'settings.syncNow':'↻ Sincronizar ahora','settings.clearQueue':'🗑 Vaciar cola',
  'settings.language':'Idioma','settings.languageDesc':'Elige el idioma de la interfaz.',
  'settings.advanced':'Avanzado','settings.openLogs':'Abrir registros',
  'settings.debugEnabled':'Modo depuración (mostrar IDs)',
  'settings.debugDesc':'Mostrar ID de película e ID de persona en vistas de detalle para depuración.',
  'settings.mcpEnabled':'Servidor MCP activado',
  'settings.mcpDesc':'Desactiva el servidor MCP si no lo usas. Esto bloquea el acceso a la API para clientes MCP.',
  'settings.resetTitle':'⚠ Restablecer base de datos',
  'settings.resetDesc':'Elimina todas las películas, pósteres y registros. ¡Crea primero una copia de seguridad! La configuración de autenticación y los passkeys se conservan.',
  'settings.resetButton':'🗑 Borrar base de datos',
  'settings.authTitle':'🔐 Autenticación (Passkey)','settings.authActive':'Autenticación activa','settings.logout':'↩ Cerrar sesión',
  'settings.noPasskey':'Aún no hay ningún passkey registrado. Registra uno para activar la autenticación.',
  'settings.username':'Nombre de usuario','settings.passkeyName':'Nombre del passkey','settings.myPasskey':'Mi passkey',
  'settings.passkeyPlaceholder':'Ej. MacBook Touch ID','settings.registerPasskey':'🔑 Registrar passkey',
  'settings.addPasskey':'Añadir passkey adicional','settings.name':'Nombre','settings.addPasskeyPlaceholder':'Ej. iPhone','settings.addPasskeyBtn':'🔑 Añadir',
  'settings.usersTitle':'👥 Gestión de usuarios','settings.groupsTitle':'📁 Grupos','settings.groupName':'Nombre del grupo',
  'settings.registrationEnabled':'Permitir el registro de nuevos usuarios',
  'settings.registrationDesc':'Si está desactivado, los nuevos usuarios no pueden crear una cuenta.',
  'settings.inviteTitle':'✉️ Códigos de invitación',
  'settings.inviteDesc':'Crea códigos temporales para que nuevos usuarios puedan registrar una cuenta.',
  'settings.inviteUsername':'Nombre de usuario del nuevo usuario',
  'settings.inviteCreate':'+ Crear código',
  'settings.inviteCodeLabel':'Código',
  'settings.inviteCopy':'📋 Copiar',
  'settings.inviteExpires':'Expira',
  'settings.inviteRevoke':'Revocar',
  'settings.inviteUsed':'Usado',
  'settings.inviteActive':'Activo',
  'settings.inviteExpired':'Expirado',
  'settings.myGroupsTitle':'📁 Mis grupos',
  'settings.myGroupsDesc':'Crea tu propio grupo e invita a otros a compartir una colección.',
  'settings.myGroupName':'Nombre del grupo',
  'settings.myGroupCreate':'+ Crear',
  'settings.groupCreateBtn':'+ Crear grupo',
  'js.registrationEnabled':'Registro activado',
  'js.registrationDisabled':'Registro desactivado',
  'js.inviteCreated':'¡Código de invitación creado!',
  'js.inviteCreateError':'No se pudo crear el código de invitación.',
  'js.inviteCopied':'¡Código copiado!',
  'js.inviteRevoked':'Código de invitación revocado.',
  'js.noInviteCodes':'No hay códigos de invitación activos.',
  'login.prompt':'Inicia sesión con tu passkey','login.button':'🔑 Iniciar sesión','login.recovery':'🔐 Usar código de recuperación',
  'login.register':'✨ Crear cuenta',
  'login.registerWithInvite':'✉️ Registrarse con código de invitación',
  'login.registerUsername':'Nombre de usuario',
  'login.inviteCode':'Código de invitación',
  'login.inviteCodePlaceholder':'XXXX-XXXX-XXXX',
  'login.registerPasskeyName':'Nombre del passkey',
  'login.registerBtn':'🔑 Registrarse',
  'login.registerFillIn':'Introduce un nombre de usuario',
  'login.registerSuccess':'✓ ¡Cuenta creada! Ahora has iniciado sesión.',
  'login.registrationDisabled':'El registro ha sido desactivado por el administrador.',
  'login.recoveryUsername':'Nombre de usuario','login.recoveryCode':'Código de recuperación','login.recoveryBtn':'🔓 Iniciar sesión con código de recuperación',
  'login.recoveryFillIn':'Introduce el nombre de usuario y el código de recuperación',
  'login.recoveryFailed':'Recuperación fallida',
  'login.newRecoveryAlert':'Nuevo código de recuperación: {0}\n\n¡Guarda este código de forma segura! Tus passkeys han sido eliminados, registra uno nuevo en Ajustes > Seguridad.',
  'recovery.title':'🔑 Código de recuperación','recovery.desc':'Guarda este código de forma segura. Puedes usarlo si pierdes tu passkey.',
  'recovery.dismiss':'Entendido, he guardado el código',
  'modal.plot':'Argumento','modal.edit':'Editar','modal.refreshMeta':'Actualizar metadatos',
  'modal.syncAll':'Sincronizar todas las fuentes','modal.delete':'Eliminar',
  'modal.copyId':'Copiar ID',
  'modal.tabInfo':'Información básica','modal.tabCast':'Reparto y equipo',
  'modal.noCast':'No se encontró reparto ni equipo. Actualiza los metadatos para obtenerlos.',
  'modal.watchlist':'🔖 Watchlist','modal.inWatchlist':'🔖 En watchlist',
  'modal.watched':'👁 Visto','modal.watchedOn':'👁 Visto {0}',
  'js.idCopied':'ID copiado','js.copyFailed':'Error al copiar',
  'person.inCollection':'En tu colección','person.born':'Nacido/a','person.died':'Fallecido/a',
  'person.birthPlace':'Lugar de nacimiento','person.knownFor':'Conocido/a por',
  'person.noBio':'No hay biografía disponible.','person.as':'como',
  'edit.title':'Editar película','edit.titleLabel':'Título *','edit.originalTitle':'Título original','edit.sortTitle':'Título de ordenación',
  'edit.year':'Año','edit.releaseDate':'Fecha de estreno','edit.director':'Director','edit.actors':'Actores',
  'edit.producer':'Productor','edit.studios':'Estudios','edit.genre':'Género','edit.format':'Formato',
  'edit.runtime':'Duración (min)','edit.hdr':'HDR','edit.screenRatio':'Relación de aspecto','edit.packaging':'Embalaje',
  'edit.regions':'Regiones','edit.audienceRating':'Clasificación','edit.imdbRating':'Puntuación IMDb',
  'edit.audioTracks':'Pistas de audio','edit.subtitles':'Subtítulos','edit.distributor':'Distribuidor',
  'edit.country':'País','edit.language':'Idioma','edit.boxSet':'Box set','edit.edition':'Edición',
  'edit.editionYear':'Año de edición','edit.editionDate':'Fecha de edición',
  'edit.purchasePrice':'Precio de compra','edit.purchaseDate':'Fecha de compra','edit.location':'Ubicación',
  'edit.extras':'Extras','edit.plot':'Argumento','edit.notes':'Notas','edit.posterUrl':'URL del póster',
  'edit.coverUpload':'Subir portada personalizada','edit.uploadCover':'🖼 Subir portada',
  'edit.coverHelp':'La imagen se escalará y comprimirá automáticamente.',
  'edit.imdbId':'ID de IMDb','edit.imdbUrl':'URL de IMDb','edit.save':'Guardar','edit.cancel':'Cancelar',
  'edit.groups':'Grupos','edit.noGroup':'Sin grupo',
  'd.genre':'Género','d.origTitle':'Título original','d.country':'País','d.language':'Idioma',
  'd.releaseDate':'Fecha de estreno','d.runtime':'Duración','d.min':' min','d.aspectRatio':'Relación de aspecto',
  'd.packaging':'Embalaje','d.region':'Región','d.imdbRating':'Puntuación IMDb','d.contentRating':'Clasificación',
  'd.distributor':'Distribuidor','d.studio':'Estudio','d.boxSet':'Box set','d.edition':'Edición',
  'd.editionYear':'Año de edición','d.editionDate':'Fecha de edición','d.actors':'Actores','d.producers':'Productores',
  'd.audio':'Audio','d.subtitles':'Subtítulos','d.extras':'Extras','d.purchasePrice':'Precio de compra',
  'd.purchaseDate':'Fecha de compra','d.location':'Ubicación','d.barcode':'Código de barras','d.added':'Añadido','d.notes':'Notas',
  'js.lookingUp':'<span class="spinner"></span> Buscando...',
  'js.backendError':'Error de backend (HTTP {0}). Consulta los registros.',
  'js.error':'Error: {0}',
  'js.alreadyInCollection':'✓ Ya en la colección: "{0}"',
  'js.movieFound':'✓ Película encontrada en la base de datos',
  'js.movieNotFound':'Película no encontrada para el código {0}. Añade manualmente.',
  'js.unknown':'(desconocido)','js.connectionError':'Error de conexión: {0}',
  'js.alreadyInCollectionBtn':'✓ Ya en la colección',
  'js.saving':'<span class="spinner"></span> Guardando...',
  'js.savedToCollection':'✓ "{0}" guardado en la colección!',
  'js.saveFailed':'Error al guardar','js.saveToCollectionBtn':'💾 Guardar en colección',
  'js.moviesSelected':'{0} película(s) seleccionada(s)',
  'js.noMoviesFound':'No se encontraron películas','js.addFirstDisc':'Añade tu primer disco desde la pestaña Añadir',
  'search.emptyTitle':'Busca en tu colección','search.emptyDesc':'Escribe un título, director, actor o género.','js.noResultsFor':'Sin resultados para "{0}".',
  'js.confirmBulkDelete':'¿Eliminar definitivamente {0} película(s)?',
  'js.deleting':'Eliminando...','js.deleted':'✓ {0} película(s) eliminada(s)',
  'js.fetchingMetadata':'Obteniendo metadatos para {0} películas…',
  'js.refreshErrors':'⚠ {0} error(es)',
  'js.refreshResult':'✓ {0} actualizada(s) · {1} omitida(s)',
  'js.confirmDelete':'¿Eliminar "{0}" de la colección?',
  'js.fetching':'<span class="spinner"></span> Obteniendo...',
  'js.updated':'✓ Actualizado','js.noNewData':'— Sin nuevos datos','js.errorShort':'✕ Error',
  'js.syncing':'<span class="spinner"></span> Sincronizando...',
  'js.synced':'✓ Sincronizado','js.noSourceData':'— Sin datos de origen',
  'js.saved':'✓ Guardado','js.saveError':'Error al guardar','js.saveBtn':'💾 Guardar',
  'js.unsavedChanges':'Tienes cambios sin guardar. ¿Deseas guardar?',
  'js.chooseCover':'Primero elige una imagen para subir',
  'js.uploadingCover':'<span class="spinner"></span> Subiendo portada...',
  'js.uploadFailed':'Error al subir','js.coverUploaded':'✓ Portada personalizada subida',
  'js.searchingMovie':'<span class="spinner"></span> Buscando información de la película...',
  'js.infoFound':'✓ Información encontrada para "{0}"',
  'js.noInfoFound':'No se encontró información. Rellena manualmente.',
  'js.barcodeRequired':'El código de barras y el título son obligatorios',
  'js.added':'✓ "{0}" añadido!',
  'js.processingFile':'<span class="spinner"></span> Procesando "{0}"...',
  'js.previewTitle':'Vista previa — {0} ({1} filas)',
  'js.columnsRecognized':'{0} columna(s) reconocida(s)',
  'js.columnsUnknown':'{0} desconocida(s) (se ignorarán)',
  'js.showingRows':'Mostrando {0} de {1} filas',
  'js.importing':'<span class="spinner"></span> Importando...',
  'js.importProgress':'Importando: {0}/{1} (✓{2} ◆{3} ○{4} ✕{5})',
  'js.importCancelled':'⚠ Importación cancelada manualmente.',
  'js.importBtn':'⬆ Importar',
  'js.importAdded':'Añadido','js.importUpdated':'Actualizado','js.importSkipped':'Omitido','js.importErrors':'Errores',
  'js.cancellingImport':'⏳ Cancelando importación...',
  'js.cancelFailed':'Error al cancelar','js.cancelSent':'⏳ Solicitud de cancelación enviada. Esperando...',
  'js.enrichTitle':'Obteniendo metadatos via TMDb/OMDb... (0/{0})',
  'js.enrichProgress':'Obteniendo metadatos... ({0}/{1})',
  'js.enrichDone':'¡Obtención de metadatos completada!',
  'js.enrichUpdated':'actualizado','js.enrichNotFound':'no encontrado','js.enrichErrors':'errores',
  'js.logCount':'{0} mensaje(s) de registro',
  'js.noLogs':'No se encontraron registros','js.logsLoadError':'No se pueden cargar los registros: {0}',
  'js.confirmClearLogs':'¿Borrar definitivamente todos los mensajes de registro?',
  'js.waitingPasskey':'<span class="spinner"></span> Esperando passkey...',
  'js.noChallenge':'No se recibió ningún desafío del servidor',
  'js.loginFailed':'Inicio de sesión fallido','js.passkeyCancelled':'Passkey cancelado','js.loginBtn':'🔑 Iniciar sesión',
  'js.loggedOut':'Has cerrado sesión.',
  'js.passkeyRegistered':'✓ Passkey "{0}" registrado!',
  'js.registrationFailed':'Registro fallido','js.passkeyRegCancelled':'Registro de passkey cancelado',
  'js.authActive':'ACTIVO','js.authOff':'DESACTIVADO','js.logins':'inicios de sesión',
  'js.confirmDeletePasskey':'¿Eliminar este passkey?',
  'js.authEnabled':'Autenticación activada','js.authDisabled':'Autenticación desactivada',
  'js.movies':'Películas','js.posters':'Pósteres','js.database':'Base de datos','js.postersSize':'Tamaño de pósteres',
  'js.creatingBackup':'<span class="spinner"></span> Creando copia de seguridad...',
  'js.backupCreated':'✓ Copia de seguridad "{0}" creada ({1} KB)',
  'js.noBackups':'No hay copias de seguridad disponibles',
  'js.restore':'↩ Restaurar','js.download':'⬇ Descargar',
  'js.downloadFailed':'Error al descargar',
  'js.tarGzOnly':'Solo se aceptan archivos .tar.gz',
  'js.uploading':'<span class="spinner"></span> Subiendo...',
  'js.uploadDone':'✓ Copia de seguridad subida',
  'js.confirmRestore':'¿Restaurar la copia de seguridad "{0}"? ¡Esto sobrescribirá las películas y pósteres actuales!',
  'js.restoring':'<span class="spinner"></span> Restaurando...',
  'js.restored':'✓ Copia de seguridad "{0}" restaurada!',
  'js.confirmDeleteBackup':'¿Eliminar la copia de seguridad "{0}"?',
  'js.groupConflictTitle':'Grupos no encontrados',
  'js.groupConflictDesc':'La copia de seguridad contiene {0} películas. Los siguientes grupos no existen en tu base de datos. Elige qué hacer con cada uno:',
  'js.groupActionCreate':'Crear grupo',
  'js.groupActionSkip':'Omitir (no asignar películas)',
  'js.groupActionAssign':'Asignar a: {0}',
  'js.groupConflictApply':'Restaurar',
  'js.cancel':'Cancelar',
  'js.confirmReset1':'¿Eliminar TODAS las películas, pósteres y registros?',
  'js.confirmReset2':'¿Estás seguro? ¡Esta acción no se puede deshacer!',
  'js.resetComplete':'¡Base de datos restablecida!',
  'js.unknownAction':'Acción desconocida','js.actionAdd':'Añadir película','js.actionUpdate':'Actualizar película',
  'js.actionDelete':'Eliminar película','js.actionBulkDelete':'Eliminación masiva',
  'js.noQueuedActions':'No hay acciones en cola.',
  'js.confirmClearQueue':'¿Vaciar toda la cola sin conexión?','js.queueCleared':'Cola vaciada',
  'js.offlineSyncError':'Sin conexión: la sincronización solo es posible estando en línea.',
  'js.synchronizing':'<span class="spinner"></span> Sincronizando...',
  'js.syncComplete':'✓ Sincronización completada ({0} acciones procesadas)',
  'js.syncPartial':'⚠ Parcialmente correcto: {0} procesada(s), {1} restante(s)',
  'js.syncFailed':'No se procesaron acciones (puede que el backend aún no esté disponible)',
  'js.sourceSettingsSaved':'Configuración de fuentes guardada',
  'js.cameraError':'Error de cámara: {0}',
  'js.statsTotal':'Total: <span>{0}</span> discos | {1}',
  'js.filterCount':'{0} películas encontradas',
  'js.queuedMessage':'Acción en cola (se sincronizará cuando haya conexión).',
  'js.unknownMovie':'Película desconocida',
  'js.confirmDeleteCurrent':'¿Eliminar "{0}"?',
  'js.confirmEditOther':'Esta película pertenece a {0}. ¿Editar como administrador de todas formas?',
  'js.queuedSave':'⏳ "{0}" está en cola.',
  'js.inQueue':'⏳ En cola',
  'js.queuedEdit':'⏳ Cambio en cola.',
  'js.queuedAdd':'⏳ "{0}" en cola.',
  'js.queuedDelete':'⏳ {0} película(s) en cola para eliminar',
  'js.mcpSettingsSaved':'Configuración MCP guardada',
  'js.advancedSettingsSaved':'Configuración avanzada guardada',
  'js.pageTitle':'DiscVault — Gestor de colección',
  'js.offlineNoCache':'Sin conexión y sin caché local disponible. Abre la aplicación en línea una vez para almacenar datos localmente.',
  'js.backendError':'Backend no disponible — mostrando datos en caché.',
  'js.offlineCache':'Sin conexión — mostrando datos en caché.',
  'js.confirmDeleteUser':'¿Seguro que deseas eliminar al usuario "{0}"? Todas sus películas también se eliminarán.',
  'js.confirmResetPasskey':'¿Restablecer el/los passkey(s) de "{0}"? El usuario tendrá que registrar uno nuevo.',
  'js.noGroups':'No se han creado grupos.',
  'js.confirmDeleteGroup':'¿Eliminar el grupo "{0}"? Las películas se desvincularán pero no se eliminarán.',
  'pwa.title':'Instalar DiscVault','pwa.subtitle':'Añade a tu pantalla de inicio para la mejor experiencia','pwa.install':'Instalar',
  'pwa.iosHint':'Toca <strong style="color:var(--accent)">⬆ Compartir</strong> y luego <strong style="color:var(--accent)">Añadir a pantalla de inicio</strong>',
  'pwa.close':'Cerrar',
  'toevoegen.scan':'Escanear código','toevoegen.manual':'Manual','toevoegen.import':'Importar',
  'lists.menuWatchlist':'Watchlist','lists.menuWatchHistory':'Historial de visionado',
  'lists.menuCustomLists':'Listas personalizadas','lists.menuTags':'Etiquetas',
  'lists.watchlistEmpty':'Tu watchlist está vacía.<br>Añade películas desde la pantalla de detalles o mediante Seleccionar en la colección.',
  'lists.watchHistoryEmpty':'Aún no has marcado ninguna película como vista.',
  'general.comingSoon':'Próximamente','general.loading':'Cargando...','general.close':'Cerrar',
  'watched.yesterday':'Ayer','watched.today':'Hoy',
  'watched.confirmDate':'Confirmar fecha','watched.historyLabel':'Historial',
  'invite.title':'🔔 Invitaciones',
  'js.bulkWatchlistAdded':'✓ {0} película(s) añadida(s) a la watchlist',
  'js.onWatchlist':'En watchlist',
  'js.watchedOn':'Visto {0}',
  'js.watchedOnDate':'Visto el {0}',
  'js.deleteTitle':'Eliminar',
  'months.short':'ene,feb,mar,abr,may,jun,jul,ago,sep,oct,nov,dic',
  'js.noMyGroups':'Aún no tienes grupos.',
  'js.myGroupsLoadError':'Error al cargar grupos.',
  'js.myGroupsOwned':'Gestionado por ti',
  'js.myGroupsMember':'Miembro de',
  'js.groupStats':'{0} miembro(s) · {1} película(s)',
  'js.groupStatsByOwner':'por {0} · {1} película(s)',
  'js.manageMembersTitle':'Miembros e invitaciones',
  'js.leaveGroup':'Salir',
  'js.myGroupCreated':'¡Grupo "{0}" creado!',
  'js.confirmDeleteMyGroup':'¿Eliminar el grupo "{0}"? Esto también eliminará todos los vínculos del grupo.',
  'js.confirmLeaveGroup':'¿Salir del grupo "{0}"?',
  'js.membersOf':'👥 Miembros de "{0}"',
  'js.noMembers':'No hay miembros.',
  'js.selfIndicator':' (tú)',
  'js.inviteUser':'Invitar usuario',
  'js.usernamePlaceholder':'nombre de usuario',
  'js.inviteBtn':'📨 Invitar',
  'js.close':'Cerrar',
  'js.groupStatsFull':'{0} miembro(s) · {1} película(s) · por {2}',
  'js.manageMembersBtn':'Gestionar miembros',
  'js.addMemberBtn':'+ Añadir',
  'js.inviteSent':'✓ Invitación enviada a {0}',
  'js.noInvites':'No hay invitaciones pendientes.',
  'js.inviteFrom':'Invitación de {0}',
  'js.inviteAccept':'✓ Aceptar',
  'js.inviteDecline':'✕ Rechazar',
  'js.inviteLoadError':'Error al cargar.',
  'js.groupsLoadError':'Error al cargar grupos',
  'js.passkeyCount':'{0} passkey(s)',
}
};


let currentLang = localStorage.getItem('dv_lang') || 'nl';
let _detailReturnTab = 'collection';
let _personReturnPanel = 'movie-detail';

function t(key, ...args) {
  let s = (LANGS[currentLang] && LANGS[currentLang][key]) || (LANGS.nl[key]) || key;
  args.forEach((a, i) => { s = s.replaceAll('{' + i + '}', a); });
  return s;
}

function applyLanguage() {
  document.documentElement.lang = currentLang;
  document.title = t('js.pageTitle');
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => {
    el.setAttribute('aria-label', t(el.dataset.i18nAria));
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
}

function setLanguage(lang) {
  currentLang = lang;
  localStorage.setItem('dv_lang', lang);
  applyLanguage();
}

const API = '/api';
let currentBarcode = '';
let currentMovieData = {};
let currentMovieId = null;
let currentPersonId = null;
let debugModeEnabled = false;
let allMovies = [];
let activeFormat = '';
let scannerRunning = false;
const WRITE_QUEUE_KEY = 'dv_write_queue';
let appVersionLabel = 'dev';
let isBeta = false;

async function loadAppVersion() {
  try {
    const r = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' });
    if (r.ok) {
      const d = await r.json();
      if (d && d.version) appVersionLabel = String(d.version);
    }
  } catch(e) {}

  isBeta = appVersionLabel.startsWith('beta');
  if (isBeta) applyBetaBranding();

  const loginEl = document.getElementById('loginVersion');
  const settingsEl = document.getElementById('settingsVersion');
  if (loginEl) loginEl.textContent = appVersionLabel;
  if (settingsEl) settingsEl.textContent = appVersionLabel;
}

function applyBetaBranding() {
  // Swap favicon and apple-touch-icon to beta variants
  const iconSwaps = {
    '/favicon-32.png': '/favicon-32-beta.png',
    '/favicon-192.png': '/favicon-192-beta.png',
    '/apple-touch-icon.png': '/apple-touch-icon-beta.png',
  };
  document.querySelectorAll('link[rel="icon"], link[rel="apple-touch-icon"]').forEach(link => {
    const href = link.getAttribute('href');
    if (href && iconSwaps[href]) link.setAttribute('href', iconSwaps[href]);
  });
  // Swap manifest to beta version
  const manifestLink = document.querySelector('link[rel="manifest"]');
  if (manifestLink) manifestLink.setAttribute('href', '/manifest-beta.json');
  // Update page title
  document.title = 'DiscVault Beta — Collectie Beheer';
}

function setCachedData(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch(e) {}
}

function getCachedData(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch(e) {
    return fallback;
  }
}

function updateConnectionState() {
  document.body.classList.toggle('is-offline', !navigator.onLine);
}

function readWriteQueue() {
  return getCachedData(WRITE_QUEUE_KEY, []);
}

function writeWriteQueue(queue) {
  setCachedData(WRITE_QUEUE_KEY, queue);
}

function updateQueueIndicator() {
  const q = readWriteQueue();
  const count = q.length;
  document.body.classList.toggle('has-queue', count > 0);
  const pill = document.getElementById('queuePill');
  if (pill) pill.textContent = t('header.queue', count);
}

function isQueueableMutation(pathname, method) {
  if (!['POST', 'PUT', 'DELETE'].includes(method)) return false;
  if (!pathname.startsWith('/api/movies')) return false;
  if (pathname.includes('/bulk-refresh')) return false;
  if (pathname.endsWith('/refresh')) return false;
  return true;
}

function serializeBodyForQueue(body) {
  if (body == null) return null;
  if (typeof body === 'string') return body;
  if (body instanceof URLSearchParams) return body.toString();
  return null;
}

function buildQueuedResponse(pathname, method, bodyText) {
  let parsed = {};
  try { parsed = bodyText ? JSON.parse(bodyText) : {}; } catch(e) {}

  const payload = {
    queued: true,
    offline: true,
    message: t('js.queuedMessage')
  };

  if (pathname === '/api/movies' && method === 'POST') {
    payload.movie = { title: parsed.title || t('js.unknownMovie') };
  }

  if (pathname.endsWith('/bulk-delete') && method === 'POST') {
    payload.deleted = Array.isArray(parsed.ids) ? parsed.ids.length : 0;
  }

  return new Response(JSON.stringify(payload), {
    status: 202,
    headers: { 'Content-Type': 'application/json' }
  });
}

function queueMutation(url, method, headers, bodyText) {
  const queue = readWriteQueue();
  queue.push({
    id: crypto.randomUUID(),
    url,
    method,
    headers: headers || {},
    body: bodyText,
    created_at: Date.now()
  });
  writeWriteQueue(queue);
  updateQueueIndicator();
}

async function flushQueuedMutations() {
  if (!navigator.onLine) return;
  const queue = readWriteQueue();
  if (!queue.length) return;

  const remaining = [];
  for (const item of queue) {
    try {
      const r = await _origFetch(item.url, {
        method: item.method,
        headers: { ...(item.headers || {}), ...authHeaders() },
        body: item.body || undefined
      });
      if (!r.ok) {
        remaining.push(item);
      }
    } catch(e) {
      remaining.push(item);
    }
  }

  writeWriteQueue(remaining);
  updateQueueIndicator();

  if (queue.length !== remaining.length) {
    await loadCollection();
    await loadStats();
  }

  loadQueueSettings();
}

function isMobileNav() {
  return window.matchMedia('(max-width: 900px)').matches;
}

function closeMobileMenu() {
  document.body.classList.remove('nav-open');
  const btn = document.getElementById('menuToggle');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

function toggleMobileMenu() {
  if (!isMobileNav()) return;
  const open = document.body.classList.toggle('nav-open');
  const btn = document.getElementById('menuToggle');
  if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function updateScrollState() {
  document.body.classList.toggle('is-scrolled', window.scrollY > 6);
}

function lockBodyScroll() {
  if (document.body.classList.contains('modal-open')) return;
  bodyScrollLockY = window.scrollY || window.pageYOffset || 0;
  document.body.style.top = `-${bodyScrollLockY}px`;
  document.body.classList.add('modal-open');
}

function unlockBodyScroll() {
  if (!document.body.classList.contains('modal-open')) return;
  document.body.classList.remove('modal-open');
  document.body.style.top = '';
  window.scrollTo(0, bodyScrollLockY);
}

function syncOverlayScrollLock() {
  // No-op: detail pages are panels now, no scroll lock needed
}

function applyDebugVisibility() {
  const movieRow = document.getElementById('modalMovieDebugRow');
  const personRow = document.getElementById('personDebugRow');
  if (movieRow) movieRow.style.display = debugModeEnabled ? 'flex' : 'none';
  if (personRow) personRow.style.display = debugModeEnabled ? 'flex' : 'none';
  filterMovies();
  filterSearchMovies();
}

function validateCriticalUiElements() {
  const requiredIds = [
    'modalTitle',
    'modalDirector',
    'modalMovieIdLabel',
    'modalMovieIdStatus',
    'personName',
    'personIdLabel',
    'personIdStatus',
    'panel-movie-detail',
    'panel-person-detail'
  ];
  const missing = requiredIds.filter(id => !document.getElementById(id));
  if (missing.length) {
    console.warn('[DiscVault] Missing critical UI elements:', missing.join(', '));
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  reorganizeToevoegenPanels();
  validateCriticalUiElements();
  applyDebugVisibility();
  document.getElementById('languageSelect').value = currentLang;
  applyLanguage();
  await loadAppVersion();
  await loadDebugSettings();
  const ok = await checkAuth();
  if (!ok) return;
  updateQueueIndicator();
  flushQueuedMutations();
  await loadStats();
  await loadCollection();
  loadInviteNotifications();
  _handleRoute();
}

function _renderHeaderStats() {
  const el = document.getElementById('headerStats');
  if (!el) return;
  const own = allMovies.filter(m => m.owner_id === currentUserId);
  const total = own.length;
  const counts = {};
  for (const m of own) { if (m.format) counts[m.format] = (counts[m.format] || 0) + 1; }
  const formats = Object.entries(counts).map(([f,c]) => `<span>${c}</span> ${f}`).join(' &nbsp;·&nbsp; ');
  el.innerHTML = t('js.statsTotal', total, formats || '');
}

async function loadStats(retries = 2) {
  try {
    const r = await fetch(`${API}/stats`);
    if (!r.ok) {
      if (retries > 0 && r.status >= 500) {
        await new Promise(ok => setTimeout(ok, 1500));
        return loadStats(retries - 1);
      }
      throw new Error(`HTTP ${r.status}`);
    }
    const d = await r.json();
    setCachedData('dv_stats_cache', d);
    _renderHeaderStats();
  } catch(e) {
    _renderHeaderStats();
  }
}

async function _loadGroupFilter() {
  const sel = document.getElementById('groupFilter');
  if (!sel) return;
  try {
    const groups = await fetch(`${API}/groups`).then(r => r.json());
    const prev = sel.value;
    sel.innerHTML = `<option value="">${t('collection.allCollections')}</option>`;
    if (authEnabled && currentUserId) {
      sel.insertAdjacentHTML('beforeend', `<option value="_mine">${t('collection.myMovies')}</option>`);
    }
    for (const g of groups) {
      sel.insertAdjacentHTML('beforeend', `<option value="${g.id}">${g.name}</option>`);
    }
    // Restore previous selection if still valid
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
    sel.style.display = groups.length || (authEnabled && currentUserId) ? '' : 'none';
  } catch(e) {
    sel.style.display = 'none';
  }
}

async function loadCollection(retries = 2) {
  // Load group filter options
  _loadGroupFilter();
  try {
    const r = await fetch(`${API}/movies`);
    if (!r.ok) {
      // Retry on server errors (502/503/504 = backend starting up)
      if (retries > 0 && r.status >= 500) {
        await new Promise(ok => setTimeout(ok, 1500));
        return loadCollection(retries - 1);
      }
      throw new Error(`HTTP ${r.status}`);
    }
    allMovies = await r.json();
    setCachedData('dv_movies_cache', allMovies);
    filterMovies();
    filterSearchMovies();
  } catch(e) {
    allMovies = getCachedData('dv_movies_cache', []);
    if (allMovies.length) {
      filterMovies();
      filterSearchMovies();
      const msg = navigator.onLine
        ? t('js.backendError')
        : t('js.offlineCache');
      document.getElementById('moviesGrid').insertAdjacentHTML(
        'afterbegin',
        `<div style="grid-column:1/-1; margin-bottom:12px; color:#ffd89a; border:1px solid rgba(240,144,64,.4); background:rgba(240,144,64,.08); border-radius:8px; padding:10px 12px; font-size:0.82rem; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">${msg} <button class="btn btn-secondary" onclick="loadCollection()" style="padding:4px 12px; font-size:0.78rem;">↻ ${t('collection.refresh')}</button></div>`
      );
    } else {
      document.getElementById('moviesGrid').innerHTML = `<div style="color: var(--danger); padding: 20px;">${t('js.offlineNoCache')}</div>`;
    }
  }
}

// ── Tab switching ─────────────────────────────────────────────────────────────
let currentToevoegenSub = 'scan';

function reorganizeToevoegenPanels() {
  // Move existing panel-scan content into addSubScan
  const scanDest = document.getElementById('addSubScan');
  const scanSrc  = document.getElementById('panel-scan');
  if (scanDest && scanSrc && scanDest.children.length === 0) {
    while (scanSrc.firstChild) scanDest.appendChild(scanSrc.firstChild);
    scanSrc.remove();
  }
  // Move existing panel-add content into addSubManual
  const manualDest = document.getElementById('addSubManual');
  const manualSrc  = document.getElementById('panel-add');
  if (manualDest && manualSrc && manualDest.children.length === 0) {
    while (manualSrc.firstChild) manualDest.appendChild(manualSrc.firstChild);
    manualSrc.remove();
  }
  // Move existing panel-import content into addSubImport
  const importDest = document.getElementById('addSubImport');
  const importSrc  = document.getElementById('panel-import');
  if (importDest && importSrc && importDest.children.length === 0) {
    while (importSrc.firstChild) importDest.appendChild(importSrc.firstChild);
    importSrc.remove();
  }
}

function switchToevoegen(sub) {
  currentToevoegenSub = sub;
  document.querySelectorAll('[data-toevoegen-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-toevoegen-sub') === sub);
  });
  const map = { scan: 'addSubScan', manual: 'addSubManual', import: 'addSubImport' };
  document.querySelectorAll('#panel-toevoegen .settings-sub-section').forEach(s => s.classList.remove('active'));
  const el = document.getElementById(map[sub]);
  if (el) el.classList.add('active');
}

function switchTab(name) {
  const activeTabName = (name === 'logs') ? 'settings'
    : (name === 'scan' || name === 'import') ? 'toevoegen'
    : name;
  const panelName = (name === 'scan' || name === 'import') ? 'toevoegen' : name;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const selected = document.querySelector(`.tab[data-tab="${activeTabName}"]`);
  if (selected) selected.classList.add('active');
  const panel = document.getElementById(`panel-${panelName}`);
  if (panel) panel.classList.add('active');
  if (isMobileNav()) closeMobileMenu();
  if (panelName === 'collection') loadCollection();
  if (panelName === 'lists') loadWatchlist();
  if (name === 'logs') loadLogs();
  if (name === 'settings') loadSettings();
  if (name === 'import') switchToevoegen('import');
  if (name === 'search') {
    filterSearchMovies();
    const input = document.getElementById('searchPageInput');
    if (input) {
      setTimeout(() => input.focus(), 0);
    }
  }
  _pushRoute(_tabPath(name));
}

// ── Barcode Scanner ───────────────────────────────────────────────────────────
function startScanner() {
  const container = document.getElementById('scanner-container');
  document.getElementById('scannerPlaceholder').style.display = 'none';
  document.getElementById('btnStartScan').style.display = 'none';
  document.getElementById('btnStopScan').style.display = 'inline-flex';

  Quagga.init({
    inputStream: {
      name: 'Live',
      type: 'LiveStream',
      target: container,
      constraints: { facingMode: 'environment' }
    },
    decoder: {
      readers: ['ean_reader', 'upc_reader', 'upc_e_reader', 'ean_8_reader']
    },
    locate: true
  }, function(err) {
    if (err) {
      showStatus('scanStatus', t('js.cameraError', err.message), 'error');
      stopScanner();
      return;
    }
    Quagga.start();
    scannerRunning = true;

    // Add scan line animation
    const overlay = document.createElement('div');
    overlay.className = 'scanner-overlay';
    overlay.innerHTML = '<div class="scanner-frame"><div class="scan-line"></div></div>';
    container.appendChild(overlay);
  });

  let lastCode = '';
  let lastTime = 0;
  Quagga.onDetected(result => {
    const code = result.codeResult.code;
    const now = Date.now();
    if (code === lastCode && now - lastTime < 3000) return;
    lastCode = code;
    lastTime = now;
    stopScanner();
    document.getElementById('manualBarcode').value = code;
    doLookup(code);
  });
}

function stopScanner() {
  if (scannerRunning) {
    Quagga.stop();
    scannerRunning = false;
  }
  document.getElementById('btnStartScan').style.display = 'inline-flex';
  document.getElementById('btnStopScan').style.display = 'none';
  // Re-show placeholder
  const container = document.getElementById('scanner-container');
  const overlay = container.querySelector('.scanner-overlay');
  if (overlay) overlay.remove();
  document.getElementById('scannerPlaceholder').style.display = 'flex';
  document.getElementById('scannerPlaceholder').style.flexDirection = 'column';
  document.getElementById('scannerPlaceholder').style.alignItems = 'center';
}

function lookupBarcode() {
  const val = document.getElementById('manualBarcode').value.trim();
  if (!val) return;
  doLookup(val);
}

async function doLookup(barcode) {
  showStatus('scanStatus', t('js.lookingUp'), 'info');
  document.getElementById('movieResult').classList.remove('visible');
  document.getElementById('noResult').style.display = 'none';

  try {
    const r = await fetch(`${API}/lookup/${barcode}?stream=1`);
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const msg = JSON.parse(line);
        if (msg.type === 'step') {
          const icon = msg.status === 'searching' ? '<span class="spinner"></span>'
            : msg.status === 'hit' ? '✓' : msg.status === 'miss' ? '—' : '✕';
          showStatus('scanStatus', `${icon} ${msg.source}${msg.detail ? ': ' + msg.detail : ''}`, msg.status === 'hit' ? 'success' : 'info');
        } else if (msg.type === 'done') {
          finalData = msg;
        }
      }
    }

    if (!finalData) {
      showStatus('scanStatus', t('js.backendError', r.status), 'error');
      document.getElementById('noResult').style.display = 'block';
      return;
    }

    if (finalData.error) {
      showStatus('scanStatus', t('js.error', finalData.error), 'error');
      document.getElementById('noResult').style.display = 'block';
      return;
    }

    if (finalData.status === 'exists') {
      showStatus('scanStatus', t('js.alreadyInCollection', finalData.movie.title), 'success');
      displayMovieResult(finalData.movie, barcode, true);
    } else if (finalData.status === 'found') {
      showStatus('scanStatus', t('js.movieFound'), 'success');
      displayMovieResult(finalData.movie, barcode, false);
    } else {
      showStatus('scanStatus', t('js.movieNotFound', barcode), 'error');
      currentBarcode = barcode;
      currentMovieData = { title: finalData.raw_title || '', barcode };
      document.getElementById('movieResult').classList.add('visible');
      document.getElementById('resultTitle').textContent = finalData.raw_title || t('js.unknown');
      document.getElementById('resultPlot').textContent = '';
      document.getElementById('resultTags').innerHTML = '';
      const poster = document.getElementById('resultPoster');
      poster.innerHTML = '<div class="no-poster">🎬</div>';
    }
  } catch(e) {
    showStatus('scanStatus', t('js.connectionError', e.message), 'error');
    document.getElementById('noResult').style.display = 'block';
  }
}

// Returns the best available poster URL for a movie object
function posterSrc(m) {
  if (m.poster_file) {
    const raw = String(m.poster_file).trim();
    if (raw) {
      if (/^https?:\/\//i.test(raw)) return raw;
      const fileName = raw.split(/[/\\]/).pop();
      if (fileName) return `/api/posters/${encodeURIComponent(fileName)}`;
    }
  }
  if (m.poster && m.poster !== 'N/A') return m.poster;
  return null;
}

function displayMovieResult(movie, barcode, alreadyInCollection) {
  currentBarcode = barcode;
  currentMovieData = { ...movie, barcode };

  document.getElementById('resultTitle').textContent = movie.title || '—';
  document.getElementById('resultPlot').textContent = movie.plot || '';

  const tags = document.getElementById('resultTags');
  tags.innerHTML = '';
  if (movie.year) tags.innerHTML += `<span class="tag">${movie.year}</span>`;
  if (movie.director) tags.innerHTML += `<span class="tag">${movie.director}</span>`;
  if (movie.genre) movie.genre.split(',').forEach(g => { tags.innerHTML += `<span class="tag">${g.trim()}</span>`; });
  if (movie.rating) tags.innerHTML += `<span class="tag">⭐ ${movie.rating}</span>`;
  if (movie.runtime) tags.innerHTML += `<span class="tag">${movie.runtime}</span>`;

  const src = posterSrc(movie);
  const poster = document.getElementById('resultPoster');
  if (src) {
    poster.innerHTML = `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=\\'no-poster\\'>🎬</div>'">`;
  } else {
    poster.innerHTML = '<div class="no-poster">🎬</div>';
  }

  document.getElementById('movieResult').classList.add('visible');
  document.getElementById('noResult').style.display = 'none';
  document.getElementById('btnSave').style.display = alreadyInCollection ? 'none' : 'inline-flex';
  document.getElementById('resultHdr').value = movie.hdr || '';
  document.getElementById('resultAudioTracks').value = movie.audio_tracks || '';
  document.getElementById('resultSubtitles').value = movie.subtitles || '';

  if (alreadyInCollection) {
    document.getElementById('btnSave').textContent = t('js.alreadyInCollectionBtn');
  }
}

async function saveMovie() {
  const btn = document.getElementById('btnSave');
  btn.innerHTML = t('js.saving');
  btn.disabled = true;

  const payload = {
    ...currentMovieData,
    barcode: currentBarcode,
    format: document.getElementById('resultFormat').value,
    location: document.getElementById('resultLocation').value,
    hdr: document.getElementById('resultHdr').value,
    audio_tracks: document.getElementById('resultAudioTracks').value,
    subtitles: document.getElementById('resultSubtitles').value,
  };

  try {
    const r = await fetch(`${API}/movies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok) {
      if (d.queued) {
        showStatus('scanStatus', t('js.queuedSave', payload.title || t('js.unknownMovie')), 'info');
        btn.innerHTML = t('js.inQueue');
        btn.disabled = true;
      } else {
        showStatus('scanStatus', t('js.savedToCollection', d.movie.title), 'success');
        btn.style.display = 'none';
        loadStats();
      }
    } else {
      showStatus('scanStatus', d.error || t('js.saveFailed'), 'error');
      btn.innerHTML = t('js.saveToCollectionBtn');
      btn.disabled = false;
    }
  } catch(e) {
    showStatus('scanStatus', t('js.error', e.message), 'error');
    btn.innerHTML = t('js.saveToCollectionBtn');
    btn.disabled = false;
  }
}

// ── Collection ────────────────────────────────────────────────────────────────
// ── Selection mode ────────────────────────────────────────────────────────────
let selectMode = false;
let selectedIds = new Set();

function toggleSelectMode() {
  selectMode = !selectMode;
  selectedIds.clear();
  const btn = document.getElementById('btnSelectMode');
  const toolbar = document.getElementById('bulkToolbar');
  const grid = document.getElementById('moviesGrid');
  if (selectMode) {
    btn.classList.add('btn-primary');
    btn.classList.remove('btn-secondary');
    toolbar.classList.add('visible');
    grid.classList.add('select-mode');
  } else {
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-secondary');
    toolbar.classList.remove('visible');
    grid.classList.remove('select-mode');
  }
  updateBulkCount();
  renderGrid(getCurrentMovies());
}

function getCurrentMovies() {
  const searchInput = document.getElementById('searchInput');
  const q = searchInput ? searchInput.value.toLowerCase() : '';
  const groupFilter = document.getElementById('groupFilter');
  const activeGroup = groupFilter ? groupFilter.value : '';
  return allMovies.filter(m => {
    const matchesFormat = !activeFormat || m.format === activeFormat;
    const matchesGroup  = !activeGroup ||
      (activeGroup === '_mine' ? (m.owner_id === currentUserId) : (m.group_ids || []).includes(parseInt(activeGroup)));
    const matchesQuery  = !q ||
      (m.title || '').toLowerCase().includes(q) ||
      (m.original_title || '').toLowerCase().includes(q) ||
      (m.director || '').toLowerCase().includes(q) ||
      (m.actor || '').toLowerCase().includes(q) ||
      (m.genre || '').toLowerCase().includes(q) ||
      (m.box_set || '').toLowerCase().includes(q) ||
      (m.studios || '').toLowerCase().includes(q) ||
      (m.distributor || '').toLowerCase().includes(q);
    return matchesFormat && matchesGroup && matchesQuery;
  });
}

function canEditMovie(m) {
  return !authEnabled || !currentUserId || currentUserRole === 'admin' || m.owner_id === currentUserId;
}

function toggleCard(id) {
  const movie = allMovies.find(m => m.id === id);
  if (movie && !canEditMovie(movie)) return;
  if (selectedIds.has(id)) {
    selectedIds.delete(id);
  } else {
    selectedIds.add(id);
  }
  // Toggle .selected on the card element without full re-render
  const card = document.querySelector(`.movie-card[data-id="${id}"]`);
  if (card) card.classList.toggle('selected', selectedIds.has(id));
  updateBulkCount();
}

function selectAll() {
  getCurrentMovies().filter(canEditMovie).forEach(m => selectedIds.add(m.id));
  getCurrentMovies().forEach(m => {
    const card = document.querySelector(`.movie-card[data-id="${m.id}"]`);
    if (card) card.classList.toggle('selected', selectedIds.has(m.id));
  });
  updateBulkCount();
}

function deselectAll() {
  selectedIds.clear();
  document.querySelectorAll('.movie-card[data-id]').forEach(c => c.classList.remove('selected'));
  updateBulkCount();
}

function updateBulkCount() {
  const n = selectedIds.size;
  document.getElementById('bulkCount').textContent =
    n === 0 ? t('bulk.noSelection') : t('js.moviesSelected', n);
  document.getElementById('btnSelectAll').textContent =
    selectedIds.size === getCurrentMovies().length ? t('bulk.deselect') : t('bulk.selectAll');
}

function renderGrid(movies) {
  const grid = document.getElementById('moviesGrid');
  if (!movies.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <span class="big-icon">📀</span>
        <h3>${t('js.noMoviesFound')}</h3>
        <p>${t('js.addFirstDisc')}</p>
      </div>`;
    return;
  }

  grid.innerHTML = movies.map(m => {
    const src       = posterSrc(m);
    const imgHtml   = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    const isSelected = selectedIds.has(m.id);
    const ownable = canEditMovie(m);
    const clickHandler = selectMode
      ? (ownable ? `toggleCard(${m.id})` : `openMovieDetail(${m.id})`)
      : `openMovieDetail(${m.id})`;
    const safeTitle = (m.title || '').replace(/'/g, "\\'");
    const showDelete = !selectMode && debugModeEnabled && ownable;
    return `
    <div class="movie-card${isSelected ? ' selected' : ''}${selectMode && !ownable ? ' not-owned' : ''}" data-id="${m.id}" onclick="${clickHandler}">
      ${showDelete ? `<button class="movie-card-delete" onclick="event.stopPropagation(); quickDelete(${m.id}, '${safeTitle}')">✕</button>` : ''}
      ${m.on_watchlist ? `<div class="watchlist-dot" title="${t('js.onWatchlist')}"></div>` : ''}
      ${m.last_watched ? `<div class="watched-check" title="${t('js.watchedOn', m.last_watched.slice(0,10))}">✓</div>` : ''}
      <div class="movie-card-poster">
        ${imgHtml}
        <div class="movie-card-format">${m.format || '4K'}</div>
      </div>
      <div class="movie-card-info">
        <div class="movie-card-title">${m.title}</div>
        <div class="movie-card-year">${m.year || '—'}</div>
      </div>
    </div>`;
  }).join('');
}

function filterMovies() {
  const movies = getCurrentMovies();
  renderGrid(movies);
  _renderHeaderStats();
  const fc = document.getElementById('filterCount');
  if (fc) {
    fc.textContent = t('js.filterCount', movies.length);
    fc.style.display = '';
  }
}

function getSearchMovies() {
  const input = document.getElementById('searchPageInput');
  const q = input ? input.value.toLowerCase().trim() : '';
  if (!q) return [];
  return allMovies.filter(m =>
    (m.title || '').toLowerCase().includes(q) ||
    (m.original_title || '').toLowerCase().includes(q) ||
    (m.director || '').toLowerCase().includes(q) ||
    (m.actor || '').toLowerCase().includes(q) ||
    (m.genre || '').toLowerCase().includes(q) ||
    (m.box_set || '').toLowerCase().includes(q) ||
    (m.studios || '').toLowerCase().includes(q) ||
    (m.distributor || '').toLowerCase().includes(q)
  );
}

function renderSearchGrid(movies) {
  const grid = document.getElementById('searchMoviesGrid');
  if (!grid) return;
  const query = (document.getElementById('searchPageInput')?.value || '').trim();

  if (!query) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <span class="big-icon">🔎</span>
        <h3>${t('search.emptyTitle')}</h3>
        <p>${t('search.emptyDesc')}</p>
      </div>`;
    return;
  }

  if (!movies.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <span class="big-icon">📭</span>
        <h3>${t('js.noMoviesFound')}</h3>
        <p>${t('js.noResultsFor', query)}</p>
      </div>`;
    return;
  }

  grid.innerHTML = movies.map(m => {
    const src = posterSrc(m);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    const safeTitle = (m.title || '').replace(/'/g, "\\'");
    const showDelete = debugModeEnabled;
    return `
    <div class="movie-card" data-id="${m.id}" onclick="openMovieDetail(${m.id})">
      ${showDelete ? `<button class="movie-card-delete" onclick="event.stopPropagation(); quickDelete(${m.id}, '${safeTitle}')">✕</button>` : ''}
      <div class="movie-card-poster">
        ${imgHtml}
        <div class="movie-card-format">${m.format || '4K'}</div>
      </div>
      <div class="movie-card-info">
        <div class="movie-card-title">${m.title}</div>
        <div class="movie-card-year">${m.year || '—'}</div>
      </div>
    </div>`;
  }).join('');
}

function filterSearchMovies() {
  renderSearchGrid(getSearchMovies());
}

// ── Bulk actions ──────────────────────────────────────────────────────────────

async function showBulkGroupAssign() {
  const panel = document.getElementById('bulkGroupPanel');
  const container = document.getElementById('bulkGroupCheckboxes');
  panel.style.display = 'block';
  container.innerHTML = `<span style="color:var(--text-muted); font-size:0.82rem;">${t('general.loading')}</span>`;
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    if (!groups.length) {
      container.innerHTML = '<span style="color:var(--text-muted); font-size:0.82rem;">' + t('js.noGroups') + '</span>';
      return;
    }
    // Count how many selected movies are already in each group
    const selMovies = allMovies.filter(m => selectedIds.has(m.id));
    container.innerHTML = groups.map(g => {
      const inGroup = selMovies.filter(m => (m.group_ids || []).includes(g.id)).length;
      const badge = inGroup > 0
        ? `<span style="font-size:0.7rem; color:var(--accent); opacity:0.8;">(${inGroup}/${selMovies.length})</span>`
        : '';
      return `
      <label style="display:flex; align-items:center; gap:6px; padding:6px 12px; background:var(--surface2); border:1px solid ${inGroup === selMovies.length ? 'var(--accent)' : 'var(--border)'}; border-radius:6px; cursor:pointer; font-size:0.84rem; white-space:nowrap;">
        <input type="checkbox" class="bulk-group-cb" value="${g.id}" style="accent-color:var(--accent); width:15px; height:15px;"${inGroup === selMovies.length ? ' checked' : ''}>
        ${g.name} ${badge}
      </label>`;
    }).join('');
  } catch(e) {
    container.innerHTML = `<span style="color:var(--danger);">${t('js.groupsLoadError')}</span>`;
  }
}

async function bulkAssignGroups() {
  const ids = [...selectedIds];
  if (!ids.length) return;
  const allCbs = [...document.querySelectorAll('.bulk-group-cb')];
  const checkedIds = allCbs.filter(cb => cb.checked).map(cb => parseInt(cb.value));
  const uncheckedIds = allCbs.filter(cb => !cb.checked).map(cb => parseInt(cb.value));

  if (!checkedIds.length && !uncheckedIds.length) return;

  try {
    showStatus('bulkGroupStatus', '<span class="spinner"></span> ' + t('bulk.assigning'), 'info');

    // Add to checked groups
    if (checkedIds.length) {
      await fetch(`${API}/movies/bulk/groups`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: ids, group_ids: checkedIds })
      });
    }
    // Remove from unchecked groups
    if (uncheckedIds.length) {
      await fetch(`${API}/movies/bulk/groups`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: ids, group_ids: uncheckedIds })
      });
    }

    // Update local cache
    for (const m of allMovies) {
      if (selectedIds.has(m.id)) {
        let gids = new Set(m.group_ids || []);
        checkedIds.forEach(g => gids.add(g));
        uncheckedIds.forEach(g => gids.delete(g));
        m.group_ids = [...gids];
      }
    }
    showStatus('bulkGroupStatus', t('bulk.assignDone', ids.length), 'success');
    setTimeout(() => {
      document.getElementById('bulkGroupPanel').style.display = 'none';
      filterMovies();
    }, 1000);
  } catch(e) {
    showStatus('bulkGroupStatus', t('js.error', e.message), 'error');
  }
}

async function bulkDelete() {
  const ids = [...selectedIds];
  if (!ids.length) return;
  if (!confirm(t('js.confirmBulkDelete', ids.length))) return;

  showProgress(t('js.deleting'), ids.length);
  try {
    const r = await fetch(`${API}/movies/bulk-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids })
    });
    const d = await r.json();
    allMovies = allMovies.filter(m => !selectedIds.has(m.id));
    selectedIds.clear();
    setProgress(ids.length, ids.length);
    if (d.queued) {
      finishProgress(t('js.queuedDelete', ids.length));
    } else {
      finishProgress(t('js.deleted', d.deleted));
    }
    filterMovies();
    loadStats();
    updateBulkCount();
  } catch(e) {
    finishProgress(t('js.error', e.message), true);
  }
}

async function bulkRefresh() {
  const ids = [...selectedIds];
  if (!ids.length) return;

  showProgress(t('js.fetchingMetadata', ids.length), ids.length);

  // Process in chunks to avoid timeouts on very large collections.
  try {
    const chunkSize = 40;
    let updated = 0;
    let skipped = 0;
    let errors = 0;
    const errorDetails = [];

    for (let i = 0; i < ids.length; i += chunkSize) {
      const chunk = ids.slice(i, i + chunkSize);
      const r = await fetch(`${API}/movies/bulk-refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: chunk, fetch_posters: true })
      });
      const d = await parseApiJson(r);

      updated += Number(d.updated || 0);
      skipped += Number(d.skipped || 0);
      errors += Number(d.errors || 0);
      if (Array.isArray(d.error_details) && d.error_details.length) {
        errorDetails.push(...d.error_details);
      }
      setProgress(Math.min(i + chunk.length, ids.length), ids.length);
    }

    const errStr = errorDetails.length
      ? '\n' + t('js.refreshErrors', errors) + ':\n' + errorDetails.join('\n')
      : '';
    finishProgress(
      t('js.refreshResult', updated, skipped) + errStr
    );
    // Reload so new posters/data are reflected
    await loadCollection();
    filterMovies();
    loadStats();
  } catch(e) {
    finishProgress(t('js.error', e.message), true);
  }
}

// ── Progress overlay helpers ──────────────────────────────────────────────────

function showProgress(title, total) {
  document.getElementById('progressTitle').textContent = title;
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('progressLabel').textContent = `0 / ${total}`;
  document.getElementById('progressResult').style.display = 'none';
  document.getElementById('progressCloseBtn').style.display = 'none';
  document.getElementById('bulkProgressOverlay').classList.add('visible');
}

function setProgress(done, total) {
  const pct = total ? Math.round((done / total) * 100) : 100;
  document.getElementById('progressBar').style.width = pct + '%';
  document.getElementById('progressLabel').textContent = `${done} / ${total}`;
}

function finishProgress(message, isError = false) {
  const result = document.getElementById('progressResult');
  result.style.display = 'block';
  result.style.color = isError ? 'var(--danger)' : 'var(--success)';
  result.style.whiteSpace = 'pre-line';
  result.textContent = message;
  document.getElementById('progressCloseBtn').style.display = 'inline-flex';
  document.getElementById('progressBar').style.width = '100%';
}

function closeProgress() {
  document.getElementById('bulkProgressOverlay').classList.remove('visible');
}

function setFormatFilter(format, btn) {
  activeFormat = format;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterMovies();
}

async function quickDelete(id, title) {
  if (!confirm(t('js.confirmDelete', title))) return;
  await fetch(`${API}/movies/${id}`, { method: 'DELETE' });
  allMovies = allMovies.filter(m => m.id !== id);
  filterMovies();
  filterSearchMovies();
  loadStats();
}

// ── Movie Detail Modal ────────────────────────────────────────────────────────
async function openMovieDetail(id) {
  // Remember which panel/tab we're coming from (for back navigation)
  // Only update if we're NOT already in movie-detail or person-detail
  const currentActive = document.querySelector('.panel.active');
  const currentPanelId = currentActive ? currentActive.id : '';
  if (currentPanelId !== 'panel-movie-detail' && currentPanelId !== 'panel-person-detail') {
    // Determine return tab from active nav tab
    const activeTab = document.querySelector('.tab.active');
    _detailReturnTab = activeTab ? (activeTab.dataset.tab || 'collection') : 'collection';
  }
  // When opening from person-detail, set person return panel for the new movie's back btn
  if (currentPanelId === 'panel-person-detail') {
    _detailReturnTab = 'person-detail';
  }

  currentMovieId = id;
  const movie = allMovies.find(m => m.id === id);
  if (!movie) return;

  // Hide edit button if user doesn't own this movie (and isn't admin / auth disabled)
  const canEdit = canEditMovie(movie);
  const editBtn = document.getElementById('btnEditMovie');
  if (editBtn) editBtn.style.display = canEdit ? '' : 'none';

  document.getElementById('modalTitle').textContent = movie.title;
  document.getElementById('modalDirector').textContent = movie.director || '';
  const movieIdLabel = document.getElementById('modalMovieIdLabel');
  const movieIdStatus = document.getElementById('modalMovieIdStatus');
  if (movieIdLabel) movieIdLabel.textContent = `ID: ${movie.id}`;
  if (movieIdStatus) movieIdStatus.textContent = '';

  const tags = document.getElementById('modalTags');
  tags.innerHTML = '';
  if (movie.format)          tags.innerHTML += `<span class="tag format">${movie.format}</span>`;
  if (movie.year)            tags.innerHTML += `<span class="tag">${movie.year}</span>`;
  if (movie.audience_rating) tags.innerHTML += `<span class="tag">${movie.audience_rating}</span>`;
  if (movie.hdr)             tags.innerHTML += `<span class="tag" style="color:#7cf">${movie.hdr}</span>`;

  const src = posterSrc(movie);
  const poster = document.getElementById('modalPoster');
  poster.innerHTML = src
    ? `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
    : '<div class="no-img">🎬</div>';

  const bg   = document.getElementById('movieDetailBg');
  const hero = document.getElementById('detailHeroImg');
  const heroWrap = document.querySelector('.detail-hero-wrap');
  const backdropUrl = movie.backdrop || '';
  const blurSrc = backdropUrl || src || '';  // fallback: poster as blurred bg

  // Page background blur — use backdrop if available, else poster as ambience
  if (bg) {
    bg.classList.remove('loaded');
    if (blurSrc) {
      bg.style.backgroundImage = `url('${blurSrc}')`;
      const bgImg = new Image();
      bgImg.onload = () => bg.classList.add('loaded');
      bgImg.src = blurSrc;
    } else {
      bg.style.backgroundImage = '';
    }
  }

  // Hero banner — show backdrop image; collapse hero if no backdrop
  if (heroWrap) heroWrap.classList.toggle('no-backdrop', !backdropUrl);
  if (hero) {
    hero.classList.remove('loaded');
    if (backdropUrl) {
      hero.style.backgroundImage = `url('${backdropUrl}')`;
      const heroImg = new Image();
      heroImg.onload = () => hero.classList.add('loaded');
      heroImg.onerror = () => {
        // Backdrop failed to load — collapse hero
        hero.style.backgroundImage = '';
        if (heroWrap) heroWrap.classList.add('no-backdrop');
      };
      heroImg.src = backdropUrl;
    } else {
      hero.style.backgroundImage = '';
    }
  }

  const d = document.getElementById('modalDetails');
  const row = (label, val, full=false) =>
    val ? `<div class="detail-item${full?' full':''}"><label>${label}</label><span>${val}</span></div>` : '';
  const link = (label, href, text) =>
    href ? `<div class="detail-item"><label>${label}</label><span><a href="${href}" target="_blank" style="color:var(--accent)">${text} ↗</a></span></div>` : '';

  d.innerHTML = [
    row(t('d.genre'),          movie.genre, true),
    row(t('d.origTitle'),movie.original_title),
    row(t('d.country'),           movie.country),
    row(t('d.language'),           movie.language),
    row(t('d.releaseDate'),   movie.release_date),
    row(t('d.runtime'),      movie.runtime ? movie.runtime + t('d.min') : ''),
    row(t('d.aspectRatio'), movie.screen_ratios),
    row(t('d.packaging'),     movie.packaging),
    row(t('d.region'),          movie.regions),
    movie.rating ? `<div class="detail-item"><label>${t('d.imdbRating')}</label><span class="rating-stars">★ ${movie.rating}</span></div>` : '',
    row(t('d.contentRating'),     movie.audience_rating),
    row(t('d.distributor'),   movie.distributor),
    row(t('d.studio'),         movie.studios),
    row(t('d.boxSet'),        movie.box_set, true),
    row(t('d.edition'),         movie.edition),
    row(t('d.editionYear'),    movie.edition_release_year),
    row(t('d.editionDate'),   movie.edition_release_date),
    row(t('d.audio'),          movie.audio_tracks, true),
    row(t('d.subtitles'),    movie.subtitles, true),
    row(t('d.extras'),       movie.extras, true),
    row(t('d.purchasePrice'),   movie.purchase_price),
    row(t('d.purchaseDate'),   movie.purchase_date),
    row(t('d.location'),        movie.location),
    movie.barcode       ? `<div class="detail-item"><label>${t('d.barcode')}</label><span style="font-family:'DM Mono',monospace;font-size:0.8rem">${movie.barcode}</span></div>` : '',
    link('IMDb',          movie.imdb_url || (movie.imdb_id ? `https://www.imdb.com/title/${movie.imdb_id}` : ''), movie.imdb_id || 'Open'),
    row(t('d.added'),     movie.added_at ? movie.added_at.slice(0,10) : ''),
    row(t('d.notes'),       movie.notes, true),
  ].join('');

  document.getElementById('modalPlot').textContent = movie.plot || '';
  document.getElementById('modalPlotGroup').style.display = movie.plot ? 'block' : 'none';

  // Reset to info tab and scroll to top
  switchDetailTab('info');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  document.getElementById('castContent').style.display = 'none';
  document.getElementById('castLoading').style.display = 'block';
  document.getElementById('castContent').innerHTML = '';
  _castLoaded = false;

  // Update watchlist / watched state from allMovies cache
  const cachedMovie = allMovies.find(m => m.id === id);
  _modalMovieOnWatchlist = cachedMovie ? !!cachedMovie.on_watchlist : false;
  _modalMovieLastWatched  = cachedMovie ? (cachedMovie.last_watched || null) : null;
  _updateWatchlistBtn();
  _updateWatchedBtn();

  // Navigate to the movie-detail panel
  switchTabDirect('movie-detail');
  _pushRoute(`/movie/${id}`);
}

function closeMovieDetail() {
  // If in edit mode with unsaved changes, ask to save
  const editMode = document.getElementById('modalEditMode');
  if (editMode && editMode.style.display !== 'none' && _isEditDirty()) {
    if (confirm(t('js.unsavedChanges'))) { saveEdit(); return; }
  }
  _editDirty = false;
  // Clear backdrop/blur so they don't flash when re-opening
  const bg = document.getElementById('movieDetailBg');
  if (bg) { bg.classList.remove('loaded'); bg.style.backgroundImage = ''; }
  const hero = document.getElementById('detailHeroImg');
  if (hero) { hero.classList.remove('loaded'); hero.style.backgroundImage = ''; }
  // Reset to view mode when closing
  document.getElementById('modalViewMode').style.display = '';
  document.getElementById('modalEditMode').style.display = 'none';
  // Restore URL
  _replaceRoute(_tabPath(_detailReturnTab));
  switchTabDirect(_detailReturnTab);
}

// Alias kept for any legacy inline calls
function closeModalDirect() { closeMovieDetail(); }
function closeModal(e) {}

let _castLoaded = false;

function switchDetailTab(tab) {
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.modal-tab-content').forEach(c => c.classList.remove('active'));
  const tabBtn = document.querySelector(`.modal-tab[data-detail-tab="${tab}"]`);
  if (tabBtn) tabBtn.classList.add('active');
  const tabContent = document.getElementById(tab === 'info' ? 'detailTabInfo' : 'detailTabCast');
  if (tabContent) tabContent.classList.add('active');
  if (tab === 'cast' && !_castLoaded) {
    _castLoaded = true;
    loadMovieCast(currentMovieId);
  }
}

async function loadMovieCast(movieId) {
  const loading = document.getElementById('castLoading');
  const content = document.getElementById('castContent');
  loading.style.display = 'block';
  content.style.display = 'none';
  try {
    const r = await fetch(`${API}/movies/${movieId}/cast`);
    const cast = await r.json();
    if (!cast.length) {
      content.innerHTML = `<div style="text-align:center; padding:30px; color:var(--text-muted);">${t('modal.noCast')}</div>`;
      loading.style.display = 'none';
      content.style.display = 'block';
      return;
    }
    const actors = cast.filter(c => c.role === 'actor');
    const crew = cast.filter(c => c.role === 'crew');
    // Group crew by job
    const crewByJob = {};
    crew.forEach(c => {
      const job = c.job || 'Other';
      if (!crewByJob[job]) crewByJob[job] = [];
      crewByJob[job].push(c);
    });

    let html = '';
    if (actors.length) {
      html += `<div class="crew-section-title">${t('d.actors')} (${actors.length})</div>`;
      html += '<div class="cast-grid">';
      actors.forEach(a => {
        const photoSrc = a.photo_url || (a.photo_file ? `${API}/profiles/${a.photo_file}?v=${encodeURIComponent(a.photo_file)}` : '');
        const photo = a.photo_file
          ? `<img class="cast-photo" src="${photoSrc}" onerror="this.outerHTML='<div class=\\'cast-photo-placeholder\\'>👤</div>'">`
          : '<div class="cast-photo-placeholder">👤</div>';
        const charName = a.character ? `<div class="cast-role">${t('person.as')} ${escHtml(a.character)}</div>` : '';
        html += `<div class="cast-card" onclick="openPersonDetail(${a.person_id})">
          ${photo}
          <div class="cast-name">${escHtml(a.name)}</div>
          ${charName}
        </div>`;
      });
      html += '</div>';
    }
    for (const [job, members] of Object.entries(crewByJob)) {
      html += `<div class="crew-section-title">${escHtml(job)} (${members.length})</div>`;
      html += '<div class="cast-grid">';
      members.forEach(c => {
        const photoSrc = c.photo_url || (c.photo_file ? `${API}/profiles/${c.photo_file}?v=${encodeURIComponent(c.photo_file)}` : '');
        const photo = c.photo_file
          ? `<img class="cast-photo" src="${photoSrc}" onerror="this.outerHTML='<div class=\\'cast-photo-placeholder\\'>👤</div>'">`
          : '<div class="cast-photo-placeholder">👤</div>';
        html += `<div class="cast-card" onclick="openPersonDetail(${c.person_id})">
          ${photo}
          <div class="cast-name">${escHtml(c.name)}</div>
        </div>`;
      });
      html += '</div>';
    }
    content.innerHTML = html;
    loading.style.display = 'none';
    content.style.display = 'block';
  } catch(e) {
    content.innerHTML = `<div style="text-align:center; padding:30px; color:var(--danger);">${t('js.error', e.message)}</div>`;
    loading.style.display = 'none';
    content.style.display = 'block';
  }
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function openPersonDetail(personId) {
  // Remember which panel we came from for the back button
  const currentActive = document.querySelector('.panel.active');
  const currentPanelId = currentActive ? currentActive.id : '';
  _personReturnPanel = (currentPanelId === 'panel-movie-detail') ? 'movie-detail' : 'collection';

  currentPersonId = personId;
  // Navigate to person-detail panel
  switchTabDirect('person-detail');
  _pushRoute(`/person/${personId}`);
  document.getElementById('personName').textContent = '';
  document.getElementById('personMeta').innerHTML = '';
  const personIdLabel = document.getElementById('personIdLabel');
  const personIdStatus = document.getElementById('personIdStatus');
  if (personIdLabel) personIdLabel.textContent = `ID: ${personId}`;
  if (personIdStatus) personIdStatus.textContent = '';
  document.getElementById('personBio').textContent = '';
  document.getElementById('personPhoto').innerHTML = '<div class="person-photo-placeholder-large">👤</div>';
  document.getElementById('personFilmography').innerHTML = '<span class="spinner"></span>';
  // Scroll to top of panel
  window.scrollTo({ top: 0, behavior: 'smooth' });
  try {
    const r = await fetch(`${API}/people/${personId}`);
    const p = await r.json();
    document.getElementById('personName').textContent = p.name || '';
    // Photo
    if (p.photo_file) {
      const photoSrc = p.photo_url || `${API}/profiles/${p.photo_file}?v=${encodeURIComponent(p.photo_file)}`;
      document.getElementById('personPhoto').innerHTML =
        `<img class="person-photo-large" src="${photoSrc}" onerror="this.outerHTML='<div class=\\'person-photo-placeholder-large\\'>👤</div>'">`; 
    }
    // Meta info
    let meta = [];
    if (p.known_for) meta.push(`${t('person.knownFor')}: ${p.known_for}`);
    if (p.birthday) meta.push(`${t('person.born')}: ${p.birthday}`);
    if (p.deathday) meta.push(`${t('person.died')}: ${p.deathday}`);
    if (p.place_of_birth) meta.push(`${t('person.birthPlace')}: ${p.place_of_birth}`);
    document.getElementById('personMeta').innerHTML = meta.join(' · ');
    // Biography
    document.getElementById('personBio').textContent = p.biography || t('person.noBio');
    // Filmography from collection
    const movies = p.movies || [];
    if (!movies.length) {
      document.getElementById('personFilmography').innerHTML =
        `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noMoviesFound')}</div>`;
      return;
    }
    let filmHtml = '';
    movies.forEach(m => {
      const src = m.poster_file ? `${API}/posters/${m.poster_file}` : (m.poster || '');
      const posterImg = src
        ? `<img class="person-film-poster" src="${src}" onerror="this.style.display='none'">`
        : `<div class="person-film-poster" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--text-muted)">🎬</div>`;
      const role = m.character ? `<div class="person-film-year">${t('person.as')} ${escHtml(m.character)}</div>`
                 : m.job ? `<div class="person-film-year">${escHtml(m.job)}</div>` : '';
      filmHtml += `<div class="person-film-card" onclick="_detailReturnTab='person-detail';openMovieDetail(${m.id})">`;
      filmHtml += `${posterImg}<div class="person-film-title">${escHtml(m.title)}</div><div class="person-film-year">${m.year || ''} · ${m.format || ''}</div>${role}</div>`;
    });
    document.getElementById('personFilmography').innerHTML = filmHtml;
  } catch(e) {
    document.getElementById('personFilmography').innerHTML =
      `<div style="color:var(--danger)">${t('js.error', e.message)}</div>`;
  }
}

function closePersonDetail() {
  currentPersonId = null;
  switchTabDirect(_personReturnPanel);
  if (_personReturnPanel === 'movie-detail') {
    _replaceRoute(`/movie/${currentMovieId}`);
  } else {
    _replaceRoute(_tabPath(_personReturnPanel));
  }
}

// Alias kept for legacy inline calls
function closePersonOverlay() { closePersonDetail(); }

async function copyValueToClipboard(value, statusElId) {
  if (!value) return;
  const statusEl = document.getElementById(statusElId);
  try {
    await navigator.clipboard.writeText(String(value));
    if (statusEl) {
      statusEl.style.color = 'var(--success)';
      statusEl.textContent = t('js.idCopied');
      setTimeout(() => {
        if (statusEl.textContent === t('js.idCopied')) statusEl.textContent = '';
      }, 1500);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.style.color = 'var(--danger)';
      statusEl.textContent = t('js.copyFailed');
      setTimeout(() => {
        statusEl.style.color = 'var(--success)';
        if (statusEl.textContent === t('js.copyFailed')) statusEl.textContent = '';
      }, 1800);
    }
  }
}

function copyCurrentMovieId() {
  copyValueToClipboard(currentMovieId, 'modalMovieIdStatus');
}

function copyCurrentPersonId() {
  copyValueToClipboard(currentPersonId, 'personIdStatus');
}

async function deleteCurrentMovie() {
  const movie = allMovies.find(m => m.id === currentMovieId);
  if (!movie || !confirm(t('js.confirmDeleteCurrent', movie.title))) return;
  await fetch(`${API}/movies/${currentMovieId}`, { method: 'DELETE' });
  allMovies = allMovies.filter(m => m.id !== currentMovieId);
  closeModalDirect();
  filterMovies();
  loadStats();
}

async function refreshSingleMovie() {
  const btn = document.getElementById('btnRefreshSingle');
  const origText = btn.innerHTML;
  btn.innerHTML = t('js.fetching');
  btn.disabled = true;

  try {
    const r = await fetch(`${API}/movies/bulk-refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [currentMovieId], fetch_posters: true })
    });
    const d = await parseApiJson(r);

    if (d.updated > 0) {
      // Reload the movie from API to get fresh data
      const mr = await fetch(`${API}/movies/${currentMovieId}`);
      const fresh = await mr.json();
      const idx = allMovies.findIndex(m => m.id === currentMovieId);
      if (idx >= 0) allMovies[idx] = fresh;

      // Re-render the detail view
      openMovieDetail(currentMovieId);
      filterMovies();

      btn.innerHTML = t('js.updated');
      btn.style.color = 'var(--success)';
      btn.style.borderColor = 'rgba(64,192,128,.4)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '';
        btn.style.borderColor = '';
        btn.disabled = false;
      }, 2000);
    } else {
      btn.innerHTML = t('js.noNewData');
      btn.style.color = 'var(--accent)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '';
        btn.disabled = false;
      }, 2000);
    }
  } catch(e) {
    btn.innerHTML = t('js.errorShort');
    btn.style.color = 'var(--danger)';
    setTimeout(() => {
      btn.innerHTML = origText;
      btn.style.color = '';
      btn.disabled = false;
    }, 2000);
  }
}

async function syncSingleMovieAllBackends() {
  const btn = document.getElementById('btnSyncAllBackends');
  const origText = btn.innerHTML;
  btn.innerHTML = t('js.syncing');
  btn.disabled = true;

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}/sync-all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fetch_posters: true })
    });
    const d = await r.json();

    if (d.status === 'updated') {
      const mr = await fetch(`${API}/movies/${currentMovieId}`);
      const fresh = await mr.json();
      const idx = allMovies.findIndex(m => m.id === currentMovieId);
      if (idx >= 0) allMovies[idx] = fresh;

      openMovieDetail(currentMovieId);
      filterMovies();

      btn.innerHTML = t('js.synced');
      btn.style.color = 'var(--success)';
      btn.style.borderColor = 'rgba(64,192,128,.4)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '#86d5ff';
        btn.style.borderColor = 'rgba(134,213,255,.45)';
        btn.disabled = false;
      }, 2000);
    } else if (d.status === 'skipped') {
      btn.innerHTML = t('js.noSourceData');
      btn.style.color = 'var(--accent)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '#86d5ff';
        btn.style.borderColor = 'rgba(134,213,255,.45)';
        btn.disabled = false;
      }, 2000);
    } else {
      btn.innerHTML = t('js.errorShort');
      btn.style.color = 'var(--danger)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '#86d5ff';
        btn.style.borderColor = 'rgba(134,213,255,.45)';
        btn.disabled = false;
      }, 2000);
    }
  } catch(e) {
    btn.innerHTML = t('js.errorShort');
    btn.style.color = 'var(--danger)';
    setTimeout(() => {
      btn.innerHTML = origText;
      btn.style.color = '#86d5ff';
      btn.style.borderColor = 'rgba(134,213,255,.45)';
      btn.disabled = false;
    }, 2000);
  }
}

async function syncSingleMovieSource(source, buttonId) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;

  const origText = btn.innerHTML;
  const origColor = btn.style.color;
  const origBorder = btn.style.borderColor;

  btn.innerHTML = t('js.syncing');
  btn.disabled = true;

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}/sync-source`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, fetch_posters: true })
    });
    const d = await r.json();

    if (d.status === 'updated') {
      const mr = await fetch(`${API}/movies/${currentMovieId}`);
      const fresh = await mr.json();
      const idx = allMovies.findIndex(m => m.id === currentMovieId);
      if (idx >= 0) allMovies[idx] = fresh;
      openMovieDetail(currentMovieId);
      filterMovies();

      btn.innerHTML = t('js.synced');
      btn.style.color = 'var(--success)';
      btn.style.borderColor = 'rgba(64,192,128,.4)';
    } else if (d.status === 'skipped') {
      btn.innerHTML = t('js.noSourceData');
      btn.style.color = 'var(--accent)';
      btn.style.borderColor = 'rgba(232,197,71,.4)';
    } else {
      btn.innerHTML = t('js.errorShort');
      btn.style.color = 'var(--danger)';
      btn.style.borderColor = 'rgba(240,64,96,.4)';
    }
  } catch(e) {
    btn.innerHTML = t('js.errorShort');
    btn.style.color = 'var(--danger)';
    btn.style.borderColor = 'rgba(240,64,96,.4)';
  }

  setTimeout(() => {
    btn.innerHTML = origText;
    btn.style.color = origColor;
    btn.style.borderColor = origBorder;
    btn.disabled = false;
  }, 2000);
}

// ── Edit mode ─────────────────────────────────────────────────────────────────

// Mapping: edit field id suffix → movie object key
const EDIT_FIELDS = {
  Title:              'title',
  OriginalTitle:      'original_title',
  SortTitle:          'sort_title',
  Year:               'year',
  ReleaseDate:        'release_date',
  Director:           'director',
  Actor:              'actor',
  Producer:           'producer',
  Studios:            'studios',
  Genre:              'genre',
  Format:             'format',
  Runtime:            'runtime',
  Hdr:                'hdr',
  ScreenRatios:       'screen_ratios',
  Packaging:          'packaging',
  Regions:            'regions',
  AudienceRating:     'audience_rating',
  Rating:             'rating',
  AudioTracks:        'audio_tracks',
  Subtitles:          'subtitles',
  Distributor:        'distributor',
  Country:            'country',
  Language:           'language',
  BoxSet:             'box_set',
  Edition:            'edition',
  EditionReleaseYear: 'edition_release_year',
  EditionReleaseDate: 'edition_release_date',
  PurchasePrice:      'purchase_price',
  PurchaseDate:       'purchase_date',
  Location:           'location',
  Extras:             'extras',
  Plot:               'plot',
  Notes:              'notes',
  Poster:             'poster',
  ImdbId:             'imdb_id',
  ImdbUrl:            'imdb_url',
};

async function _populateGroupCheckboxes(currentGroupIds) {
  const container = document.getElementById('editGroupsContainer');
  if (!container) return;
  container.innerHTML = '';
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    if (!groups.length) {
      container.innerHTML = '<span style="color:var(--text-muted); font-size:0.82rem;">' + t('js.noGroups') + '</span>';
      return;
    }
    const ids = currentGroupIds || [];
    for (const g of groups) {
      const checked = ids.includes(g.id) ? 'checked' : '';
      container.insertAdjacentHTML('beforeend', `
        <label style="display:flex; align-items:center; gap:6px; padding:6px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; cursor:pointer; font-size:0.84rem; white-space:nowrap;">
          <input type="checkbox" class="edit-group-cb" value="${g.id}" ${checked} style="accent-color:var(--accent); width:15px; height:15px;">
          ${g.name}
        </label>
      `);
    }
  } catch(e) {}
}

function startEdit() {
  const movie = allMovies.find(m => m.id === currentMovieId);
  if (!movie) return;

  // Admin editing another user's movie → ask confirmation
  if (authEnabled && currentUserRole === 'admin' && movie.owner_id && movie.owner_id !== currentUserId) {
    const ownerName = movie.owner_name || ('user #' + movie.owner_id);
    if (!confirm(t('js.confirmEditOther', ownerName))) return;
  }

  // Populate all edit fields
  for (const [suffix, key] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el) {
      if (el.tagName === 'SELECT') {
        el.value = movie[key] || el.options[0].value;
      } else {
        el.value = movie[key] || '';
      }
    }
  }

  // Populate group checkboxes
  _populateGroupCheckboxes(movie.group_ids || []);

  document.getElementById('editStatus').className = 'status-msg';
  document.getElementById('modalViewMode').style.display = 'none';
  document.getElementById('modalEditMode').style.display = 'block';

  // Snapshot original values for dirty-checking
  _editSnapshot = {};
  for (const [suffix] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el) _editSnapshot[suffix] = el.value;
  }
  _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
  _editDirty = false;
}

let _editSnapshot = {};
let _editDirty = false;

function _isEditDirty() {
  if (_editDirty) return true;
  for (const [suffix] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el && el.value !== (_editSnapshot[suffix] ?? '')) return true;
  }
  const curGroups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
  if (curGroups !== (_editSnapshot._groups ?? '')) return true;
  return false;
}

function cancelEdit() {
  if (_isEditDirty()) {
    if (confirm(t('js.unsavedChanges'))) { saveEdit(); return; }
  }
  _editDirty = false;
  document.getElementById('modalEditMode').style.display = 'none';
  document.getElementById('modalViewMode').style.display = '';
}

async function saveEdit() {
  const btn = document.getElementById('btnSaveEdit');
  btn.innerHTML = t('js.saving');
  btn.disabled = true;

  const payload = {};
  for (const [suffix, key] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el) payload[key] = el.value;
  }

  // Collect selected group IDs from checkboxes
  const selectedGroupIds = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => parseInt(cb.value));

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok) {
      // Update group assignments
      await fetch(`${API}/movies/${currentMovieId}/groups`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_ids: selectedGroupIds })
      });
      if (d && typeof d === 'object') d.group_ids = selectedGroupIds;
      if (d.queued) {
        showStatus('editStatus', t('js.queuedEdit'), 'info');
      } else {
        // Update local cache
        const idx = allMovies.findIndex(m => m.id === currentMovieId);
        if (idx >= 0) allMovies[idx] = { ...allMovies[idx], ...d };

        showStatus('editStatus', t('js.saved'), 'success');
        // Stay in edit mode — update snapshot so fields are no longer dirty
        _editSnapshot = {};
        for (const [suffix] of Object.entries(EDIT_FIELDS)) {
          const el = document.getElementById('edit' + suffix);
          if (el) _editSnapshot[suffix] = el.value;
        }
        _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
        _editDirty = false;
        filterMovies();  // Update grid
      }
    } else {
      showStatus('editStatus', d.error || t('js.saveError'), 'error');
    }
  } catch(e) {
    showStatus('editStatus', t('js.error', e.message), 'error');
  }
  btn.innerHTML = t('js.saveBtn');
  btn.disabled = false;
}

async function uploadCustomCover() {
  const input = document.getElementById('editPosterUpload');
  const file = input && input.files ? input.files[0] : null;
  if (!file) {
    showStatus('editStatus', t('js.chooseCover'), 'error');
    return;
  }

  const fd = new FormData();
  fd.append('poster', file);

  showStatus('editStatus', t('js.uploadingCover'), 'info');

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}/poster`, {
      method: 'POST',
      body: fd
    });
    const d = await r.json();
    if (!r.ok) {
      showStatus('editStatus', d.error || t('js.uploadFailed'), 'error');
      return;
    }

    const updated = d.movie || null;
    if (updated) {
      const idx = allMovies.findIndex(m => m.id === currentMovieId);
      if (idx >= 0) allMovies[idx] = updated;
    }

    const current = allMovies.find(m => m.id === currentMovieId);
    if (current && current.poster_file) {
      document.getElementById('editPoster').value = current.poster || '';
      openMovieDetail(currentMovieId);
      filterMovies();
    }

    input.value = '';
    showStatus('editStatus', t('js.coverUploaded'), 'success');
  } catch(e) {
    showStatus('editStatus', t('js.error', e.message), 'error');
  }
}

// ── Manual Add ────────────────────────────────────────────────────────────────
async function autoFillFromTitle() {
  const title = document.getElementById('addTitle').value.trim();
  if (!title) return;
  showStatus('addStatus', t('js.searchingMovie'), 'info');

  try {
    const r = await fetch(`${API}/search_title?q=${encodeURIComponent(title)}`);
    const d = await r.json();
    if (d.status === 'found') {
      const m = d.movie;
      document.getElementById('addTitle').value = m.title || title;
      document.getElementById('addYear').value = m.year || '';
      document.getElementById('addDirector').value = m.director || '';
      document.getElementById('addGenre').value = m.genre || '';
      document.getElementById('addRuntime').value = m.runtime || '';
      document.getElementById('addRating').value = m.rating || '';
      document.getElementById('addHdr').value = m.hdr || '';
      document.getElementById('addAudioTracks').value = m.audio_tracks || '';
      document.getElementById('addSubtitles').value = m.subtitles || '';
      document.getElementById('addPoster').value = m.poster || '';
      showStatus('addStatus', t('js.infoFound', m.title), 'success');
    } else {
      showStatus('addStatus', t('js.noInfoFound'), 'error');
    }
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

async function submitManual() {
  const barcode = document.getElementById('addBarcode').value.trim();
  const title = document.getElementById('addTitle').value.trim();
  if (!barcode || !title) {
    showStatus('addStatus', t('js.barcodeRequired'), 'error');
    return;
  }

  const payload = {
    barcode, title,
    year: document.getElementById('addYear').value,
    director: document.getElementById('addDirector').value,
    genre: document.getElementById('addGenre').value,
    format: document.getElementById('addFormat').value,
    runtime: document.getElementById('addRuntime').value,
    rating: document.getElementById('addRating').value,
    hdr: document.getElementById('addHdr').value,
    audio_tracks: document.getElementById('addAudioTracks').value,
    subtitles: document.getElementById('addSubtitles').value,
    location: document.getElementById('addLocation').value,
    notes: document.getElementById('addNotes').value,
    poster: document.getElementById('addPoster').value,
  };

  try {
    const r = await fetch(`${API}/movies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok) {
      if (d.queued) {
        showStatus('addStatus', t('js.queuedAdd', title), 'info');
      } else {
        showStatus('addStatus', t('js.added', title), 'success');
      }
      clearManualForm();
      loadStats();
    } else {
      showStatus('addStatus', d.error || t('js.saveError'), 'error');
    }
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

function clearManualForm() {
  ['addBarcode','addTitle','addYear','addDirector','addGenre','addRuntime',
   'addRating','addHdr','addAudioTracks','addSubtitles','addLocation','addNotes','addPoster'].forEach(id => {
    document.getElementById(id).value = '';
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function showStatus(id, msg, type) {
  const el = document.getElementById(id);
  el.innerHTML = msg;
  el.className = `status-msg visible ${type}`;
}

async function parseApiJson(response) {
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (e) {
    data = null;
  }

  if (!response.ok) {
    let msg = (data && data.error) ? data.error : `HTTP ${response.status}`;
    if (!data && text) {
      const snippet = text.replace(/\s+/g, ' ').trim().slice(0, 180);
      if (snippet) msg += `: ${snippet}`;
    }
    throw new Error(msg);
  }

  if (data === null) {
    const snippet = (text || '').replace(/\s+/g, ' ').trim().slice(0, 180);
    throw new Error(`Ongeldige API-response (geen JSON): ${snippet || '(leeg)'}`);
  }

  return data;
}


// ── Import ────────────────────────────────────────────────────────────────────
let importFile = null;
let importPreviewData = null;
let activeImportId = null;
let importCancelRequested = false;

function handleDragOver(e) {
  e.preventDefault();
  document.getElementById('dropZone').style.borderColor = 'var(--accent)';
  document.getElementById('dropZone').style.background = 'rgba(232,197,71,0.05)';
}

function handleDragLeave(e) {
  document.getElementById('dropZone').style.borderColor = 'var(--border)';
  document.getElementById('dropZone').style.background = 'var(--surface2)';
}

function handleDrop(e) {
  e.preventDefault();
  handleDragLeave(e);
  const file = e.dataTransfer.files[0];
  if (file) processImportFile(file);
}

function handleFileSelect(e) {
  const file = e.target.files[0];
  if (file) processImportFile(file);
  e.target.value = '';
}

async function processImportFile(file) {
  importFile = file;
  showStatus('importStatus', t('js.processingFile', file.name), 'info');

  const fd = new FormData();
  fd.append('file', file);

  try {
    const r = await fetch(`${API}/import/preview`, { method: 'POST', body: fd });
    const contentType = r.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      showStatus('importStatus', t('js.backendError', r.status), 'error');
      return;
    }
    const d = await r.json();
    if (!r.ok) {
      showStatus('importStatus', t('js.error', d.error), 'error');
      return;
    }
    importPreviewData = d;
    showImportPreview(d, file.name);
  } catch(e) {
    showStatus('importStatus', t('js.connectionError', e.message), 'error');
  }
}

function showImportPreview(d, fname) {
  document.getElementById('importStep2').style.display = 'block';
  document.getElementById('previewTitle').textContent =
    t('js.previewTitle', fname, d.total_rows);

  // Column mapping summary
  const mapEl = document.getElementById('columnMapping');
  const mapped = d.mapped_columns || [];
  const unknown = d.unknown_columns || [];
  mapEl.innerHTML = `
    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:6px;">
      ${mapped.map(c => `<span class="tag" style="color:var(--success);border-color:rgba(64,192,128,.3);background:rgba(64,192,128,.07)">✓ ${c}</span>`).join('')}
      ${unknown.map(c => `<span class="tag" style="color:var(--text-muted)">~ ${c}</span>`).join('')}
    </div>
    <div style="font-size:0.75rem; color:var(--text-muted);">
      <span style="color:var(--success)">${t('js.columnsRecognized', mapped.length)}</span>
      ${unknown.length ? ` · <span>${t('js.columnsUnknown', unknown.length)}</span>` : ''}
    </div>`;

  // Preview table
  const preview = d.preview || [];
  const cols = d.detected_columns || [];
  const knownCols = cols.filter(c => d.mapped_columns.includes(c));
  const tableCols = knownCols.length > 0 ? knownCols : cols.slice(0, 8);

  const table = document.getElementById('previewTable');
  table.innerHTML = `
    <thead>
      <tr style="background:var(--surface2); border-bottom:1px solid var(--border);">
        ${tableCols.map(c => `<th style="padding:8px 12px; text-align:left; color:${d.mapped_columns.includes(c) ? 'var(--accent)' : 'var(--text-muted)'}; white-space:nowrap;">${c}</th>`).join('')}
      </tr>
    </thead>
    <tbody>
      ${preview.map((row, i) => `
        <tr style="border-bottom:1px solid var(--border); background:${i%2===0?'transparent':'rgba(255,255,255,.02)'}">
          ${tableCols.map(c => `<td style="padding:7px 12px; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text);" title="${(row[c]||'').replace(/"/g,'&quot;')}">${row[c] || '<span style=color:var(--border)>—</span>'}</td>`).join('')}
        </tr>`).join('')}
    </tbody>`;

  document.getElementById('previewCount').textContent =
    t('js.showingRows', preview.length, d.total_rows);

  document.getElementById('importStatus').className = 'status-msg';
  const cancelBtn = document.getElementById('btnCancelImport');
  cancelBtn.disabled = true;
}

async function doImport() {
  if (!importFile) return;
  const btn = document.getElementById('btnDoImport');
  const cancelBtn = document.getElementById('btnCancelImport');
  btn.innerHTML = t('js.importing');
  btn.disabled = true;
  cancelBtn.disabled = false;

  activeImportId = (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : (`imp_${Date.now()}_${Math.random().toString(16).slice(2)}`);
  importCancelRequested = false;

  // Show progress container
  const progressContainer = document.createElement('div');
  progressContainer.id = 'importProgressContainer';
  progressContainer.style.cssText = `
    margin-top: 16px;
    padding: 16px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
  `;
  progressContainer.innerHTML = `
    <div style="font-size:0.85rem; margin-bottom:8px; color:var(--text-muted);">
      <span id="importProgressText">${t('js.importProgress', 0, 0, 0, 0, 0, 0)}</span>
    </div>
    <div style="background:var(--surface); border-radius:4px; height:24px; overflow:hidden; position:relative;">
      <div id="importProgressBar" style="
        height:100%; width:0%;
        background:linear-gradient(90deg, var(--accent), var(--accent2));
        transition:width 0.3s ease;
        display:flex; align-items:center; justify-content:center;
        font-size:0.7rem; color:#000; font-weight:500;
      "></div>
    </div>
  `;
  document.getElementById('importStep2').appendChild(progressContainer);

  const fd = new FormData();
  fd.append('file', importFile);
  fd.append('mode', document.getElementById('importMode').value);
  fd.append('fetch_posters', 'false');  // posters worden in de enrich-stap opgehaald
  fd.append('enrich', document.getElementById('importEnrich').checked ? 'true' : 'false');
  fd.append('import_id', activeImportId);

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 1800000);

    const r = await fetch(`${API}/import`, { method: 'POST', body: fd, signal: controller.signal });
    clearTimeout(timeoutId);

    if (!r.ok) {
      showStatus('importStatus', t('js.backendError', r.status), 'error');
      btn.innerHTML = t('js.importBtn');
      btn.disabled = false;
      cancelBtn.disabled = true;
      progressContainer.remove();
      activeImportId = null;
      importCancelRequested = false;
      return;
    }

    // Parse streaming JSON responses
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResult = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // Keep incomplete line in buffer

      for (const line of lines) {
        if (!line) continue;
        try {
          const d = JSON.parse(line);
          if (d.type === 'progress') {
            const pct = d.percent || 0;
            document.getElementById('importProgressBar').style.width = pct + '%';
            document.getElementById('importProgressBar').textContent = pct + '%';
            document.getElementById('importProgressText').textContent = 
              t('js.importProgress', d.current, d.total, d.added, d.updated, d.skipped, d.errors);
          } else if (d.type === 'done') {
            finalResult = d;
          }
        } catch(e) {}
      }
    }

    // Handle final result
    if (!finalResult) {
      showStatus('importStatus', t('js.errorShort'), 'error');
      btn.innerHTML = t('js.importBtn');
      btn.disabled = false;
      cancelBtn.disabled = true;
      progressContainer.remove();
      activeImportId = null;
      importCancelRequested = false;
      return;
    }

    const d = finalResult;

    progressContainer.remove();
    cancelBtn.disabled = true;
    activeImportId = null;
    importCancelRequested = false;
    document.getElementById('importStep2').style.display = 'none';
    document.getElementById('importStep3').style.display = 'block';

    const resultEl = document.getElementById('importResult');
    const hasErrors = d.errors > 0;
    const isCancelled = !!d.cancelled;
    resultEl.innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:16px; margin-bottom:20px;">
        <div style="background:rgba(64,192,128,.08); border:1px solid rgba(64,192,128,.25); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:var(--success);">${d.added}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importAdded')}</div>
        </div>
        <div style="background:rgba(124,106,247,.08); border:1px solid rgba(124,106,247,.25); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:#a09af8;">${d.updated}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importUpdated')}</div>
        </div>
        <div style="background:var(--surface2); border:1px solid var(--border); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:var(--text-muted);">${d.skipped}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importSkipped')}</div>
        </div>
        ${hasErrors ? `<div style="background:rgba(240,64,96,.08); border:1px solid rgba(240,64,96,.25); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:var(--danger);">${d.errors}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importErrors')}</div>
        </div>` : ''}
      </div>
      ${isCancelled ? `<div style="margin-bottom:12px; font-size:0.84rem; color:var(--accent);">${t('js.importCancelled')}</div>` : ''}
      ${hasErrors && d.error_details?.length ? `
        <div style="background:rgba(240,64,96,.06); border:1px solid rgba(240,64,96,.2); border-radius:6px; padding:14px; font-size:0.78rem; font-family:'DM Mono',monospace; color:var(--danger);">
          ${d.error_details.map(e => `<div>${e}</div>`).join('')}
        </div>` : ''}`;

    // Clear stale file reference
    importFile = null;
    document.getElementById('fileInput').value = '';

    // Reload collection, then start per-movie enrichment
    await loadCollection();
    loadStats();

    if (!isCancelled && d.added > 0) {
      await postImportEnrich();
    } else {
      document.getElementById('btnViewCollection').style.display = 'inline-flex';
    }

  } catch(e) {
    cancelBtn.disabled = true;
    activeImportId = null;
    importCancelRequested = false;
    importFile = null;
    document.getElementById('fileInput').value = '';
    showStatus('importStatus', t('js.error', e.message), 'error');
    btn.innerHTML = t('js.importBtn');
    btn.disabled = false;
  }
}

async function cancelImportRun() {
  if (!activeImportId) return;
  const btn = document.getElementById('btnCancelImport');
  btn.disabled = true;
  importCancelRequested = true;
  showStatus('importStatus', t('js.cancellingImport'), 'info');
  try {
    const r = await fetch(`${API}/import/cancel/${encodeURIComponent(activeImportId)}`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      showStatus('importStatus', d.error || t('js.cancelFailed'), 'error');
      btn.disabled = false;
      importCancelRequested = false;
      return;
    }
    showStatus('importStatus', t('js.cancelSent'), 'info');
  } catch(e) {
    showStatus('importStatus', t('js.cancelFailed') + ': ' + e.message, 'error');
    btn.disabled = false;
    importCancelRequested = false;
  }
}

async function postImportEnrich() {
  const section  = document.getElementById('postImportEnrich');
  const bar      = document.getElementById('enrichBar');
  const counter  = document.getElementById('enrichCounter');
  const log      = document.getElementById('enrichLog');
  const title    = document.getElementById('enrichTitle');
  const resultEl = document.getElementById('enrichResult');

  section.style.display = 'block';
  log.innerHTML = '';

  // Get all movies (freshly imported)
  let movies = [];
  try {
    const r = await fetch(`${API}/movies`);
    movies = await r.json();
  } catch(e) { return; }

  const total = movies.length;
  let enriched = 0, skippedCount = 0, errorCount = 0;
  title.textContent = t('js.enrichTitle', total);

  for (let i = 0; i < movies.length; i++) {
    const m = movies[i];
    const pct = Math.round(((i + 1) / total) * 100);
    bar.style.width = pct + '%';
    counter.textContent = `${i + 1} / ${total}`;
    title.textContent = t('js.enrichProgress', i + 1, total);

    try {
      const r = await fetch(`${API}/movies/${m.id}/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fetch_posters: true })
      });
      const d = await r.json();

      if (d.status === 'updated') {
        enriched++;
        const flds = d.fields?.length ? d.fields.join(', ') : '';
        const posterIcon = d.has_poster ? ' 🖼' : '';
        log.innerHTML += `<div style="color:var(--success)">✓ ${d.title} — ${d.source}${posterIcon}</div>`;
      } else if (d.status === 'skipped') {
        skippedCount++;
        log.innerHTML += `<div style="color:var(--text-muted)">— ${m.title} (${t('js.enrichNotFound')})</div>`;
      } else if (d.status === 'error') {
        errorCount++;
        log.innerHTML += `<div style="color:var(--danger)">✕ ${m.title}: ${d.error}</div>`;
      }
    } catch(e) {
      errorCount++;
      log.innerHTML += `<div style="color:var(--danger)">✕ ${m.title}: ${e.message}</div>`;
    }

    // Auto-scroll log
    log.scrollTop = log.scrollHeight;
  }

  bar.style.width = '100%';
  title.textContent = t('js.enrichDone');

  resultEl.style.display = 'block';
  resultEl.innerHTML = `
    <div style="display:flex; gap:16px; flex-wrap:wrap;">
      <div style="font-size:0.88rem;"><span style="color:var(--success); font-weight:500;">${enriched}</span> ${t('js.enrichUpdated')}</div>
      <div style="font-size:0.88rem;"><span style="color:var(--text-muted); font-weight:500;">${skippedCount}</span> ${t('js.enrichNotFound')}</div>
      ${errorCount > 0 ? `<div style="font-size:0.88rem;"><span style="color:var(--danger); font-weight:500;">${errorCount}</span> ${t('js.enrichErrors')}</div>` : ''}
    </div>`;

  document.getElementById('btnViewCollection').style.display = 'inline-flex';

  // Reload collection to reflect new posters/data
  await loadCollection();
  loadStats();
}

function resetImport() {
  importFile = null;
  importPreviewData = null;
  activeImportId = null;
  importCancelRequested = false;
  document.getElementById('fileInput').value = '';
  document.getElementById('importStep2').style.display = 'none';
  document.getElementById('importStep3').style.display = 'none';
  document.getElementById('postImportEnrich').style.display = 'none';
  document.getElementById('enrichLog').innerHTML = '';
  document.getElementById('enrichResult').style.display = 'none';
  document.getElementById('enrichBar').style.width = '0%';
  document.getElementById('btnViewCollection').style.display = 'none';
  document.getElementById('btnDoImport').innerHTML = t('js.importBtn');
  document.getElementById('btnDoImport').disabled = false;
  document.getElementById('btnCancelImport').disabled = true;
  document.getElementById('importStatus').className = 'status-msg';
}

function switchTabDirect(name) {
  const activeTabName = (name === 'logs') ? 'settings'
    : (name === 'scan' || name === 'import') ? 'toevoegen'
    : name;
  const panelName = (name === 'scan' || name === 'import') ? 'toevoegen' : name;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const selected = document.querySelector(`.tab[data-tab="${activeTabName}"]`);
  if (selected) selected.classList.add('active');
  const panel = document.getElementById(`panel-${panelName}`);
  if (panel) panel.classList.add('active');
  if (isMobileNav()) closeMobileMenu();
  if (panelName === 'collection') loadCollection();
  if (panelName === 'lists') loadWatchlist();
  if (name === 'logs') loadLogs();
  if (name === 'settings') loadSettings();
  if (name === 'import') switchToevoegen('import');
  if (name === 'search') {
    filterSearchMovies();
    const input = document.getElementById('searchPageInput');
    if (input) {
      setTimeout(() => input.focus(), 0);
    }
  }
}

function openLogsFromSettings() {
  switchTab('settings');
  switchSettingsSubmenu('logs');
}

// ── Client-side Routing ───────────────────────────────────────────────────────
const _TAB_PATHS = { collection:'/', settings:'/settings', search:'/search', lists:'/lists', toevoegen:'/add' };
const _PATH_TABS = { '/':'collection', '/settings':'settings', '/search':'search', '/lists':'lists', '/add':'toevoegen' };

function _tabPath(tab) {
  const base = (tab === 'logs') ? 'settings' : (tab === 'scan' || tab === 'import') ? 'toevoegen' : tab;
  return _TAB_PATHS[base] || '/';
}
function _pushRoute(path) {
  if (window.location.pathname !== path) history.pushState({ path }, '', path);
}
function _replaceRoute(path) {
  history.replaceState({ path }, '', path);
}
function _handleRoute() {
  // Handle hash-based deep links (e.g. from push notification clicks).
  if (window.location.hash === '#invites') {
    history.replaceState(null, '', window.location.pathname);
    openInvitePanel();
    return;
  }
  const path = window.location.pathname;
  const personMatch = path.match(/^\/person\/(\d+)$/);
  if (personMatch) {
    openPersonDetail(parseInt(personMatch[1], 10));
    return;
  }
  const movieMatch = path.match(/^\/movie\/(\d+)$/);
  if (movieMatch) {
    const id = parseInt(movieMatch[1], 10);
    switchTabDirect('collection');
    const tryOpen = (tries) => {
      const found = allMovies.find(m => m.id === id);
      if (found) { openMovieDetail(id); }
      else if (tries > 0) { setTimeout(() => tryOpen(tries - 1), 300); }
      else { _replaceRoute('/'); }
    };
    tryOpen(15);
    return;
  }
  const tab = _PATH_TABS[path];
  if (tab) switchTabDirect(tab);
  else _replaceRoute('/');
}

window.addEventListener('popstate', (e) => {
  const path = (e.state && e.state.path) || window.location.pathname;
  const personMatch = path.match(/^\/person\/(\d+)$/);
  if (personMatch) { openPersonDetail(parseInt(personMatch[1], 10)); return; }
  const movieMatch = path.match(/^\/movie\/(\d+)$/);
  if (movieMatch) {
    openMovieDetail(parseInt(movieMatch[1], 10));
  } else {
    const tab = _PATH_TABS[path] || 'collection';
    switchTabDirect(tab);
  }
});

window.addEventListener('resize', () => {
  if (!isMobileNav()) closeMobileMenu();
});

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const active = document.querySelector('.panel.active');
    if (active && active.id === 'panel-person-detail') { closePersonDetail(); return; }
    if (active && active.id === 'panel-movie-detail') { closeMovieDetail(); return; }
    closeMobileMenu();
  }
});

window.addEventListener('scroll', updateScrollState, { passive: true });
updateScrollState();
window.addEventListener('online', () => {
  updateConnectionState();
  flushQueuedMutations();
});
window.addEventListener('offline', updateConnectionState);
updateConnectionState();
updateQueueIndicator();

// ── Logs ──────────────────────────────────────────────────────────────────────
let logAutoTimer = null;

async function loadLogs() {
  const level = document.getElementById('logLevelFilter').value;
  const cat   = document.getElementById('logCatFilter').value;
  const params = new URLSearchParams({ limit: '300' });
  if (level) params.set('level', level);
  if (cat)   params.set('category', cat);

  try {
    const r = await fetch(`${API}/logs?${params}`);
    const logs = await r.json();
    renderLogs(logs);
  } catch(e) {
    document.getElementById('logBody').innerHTML =
      `<tr><td colspan="5" style="text-align:center;padding:30px;color:var(--danger)">${t('js.logsLoadError', e.message)}</td></tr>`;
  }
}

function renderLogs(logs) {
  const body = document.getElementById('logBody');
  document.getElementById('logCount').textContent = t('js.logCount', logs.length);

  if (!logs.length) {
    body.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:40px; color:var(--text-muted);">${t('js.noLogs')}</td></tr>`;
    return;
  }

  const esc = (s) => String(s || '').replace(/</g, '&lt;');
  const allowedStatuses = new Set(['hit', 'miss', 'skipped', 'error', 'partial']);

  function renderBackendTrace(traceText) {
    if (!traceText) {
      return '<div class="log-backend-trace" style="color:var(--text-muted)">-</div>';
    }
    const parts = traceText.split(' | ').map(p => p.trim()).filter(Boolean);
    if (!parts.length) {
      return '<div class="log-backend-trace" style="color:var(--text-muted)">-</div>';
    }
    const chips = parts.map(part => {
      const m = part.match(/^([^:]+):\s*(hit|miss|skipped|error|partial)\b(.*)$/i);
      if (!m) {
        return `<span class="backend-chip skipped">${esc(part)}</span>`;
      }
      const backend = (m[1] || '').trim();
      const status = (m[2] || 'skipped').toLowerCase();
      const rest = (m[3] || '').trim();
      const cls = allowedStatuses.has(status) ? status : 'skipped';
      const label = `${backend}: ${status}${rest ? ' ' + rest : ''}`;
      return `<span class="backend-chip ${cls}">${esc(label)}</span>`;
    }).join('');
    return `<div class="log-backend-trace">${chips}</div>`;
  }

  body.innerHTML = logs.map(l => {
    const ts = (l.timestamp || '').replace('T', ' ').slice(0, 19);
    const rawDetail = String(l.detail || '');
    const marker = 'Backends:';
    const idx = rawDetail.indexOf(marker);
    const backendTrace = idx >= 0 ? rawDetail.slice(idx + marker.length).trim() : '';
    const detailWithoutBackend = idx >= 0 ? rawDetail.slice(0, idx).trim() : rawDetail;
    const backendHtml = renderBackendTrace(backendTrace);
    const detailHtml = detailWithoutBackend
      ? `<div class="log-detail">${esc(detailWithoutBackend)}</div>`
      : '';

    return `<tr>
      <td class="ts" data-label="${t('logs.thTimestamp')}">${ts}</td>
      <td data-label="${t('logs.thLevel')}"><span class="log-level ${l.level}">${l.level}</span></td>
      <td class="log-cat" data-label="${t('logs.thCategory')}">${l.category}</td>
      <td data-label="${t('logs.thBackends')}">${backendHtml}</td>
      <td data-label="${t('logs.thMessage')}">
        <div class="log-msg">${esc(l.message)}</div>
        ${detailHtml}
      </td>
    </tr>`;
  }).join('');
}

async function clearLogs() {
  if (!confirm(t('js.confirmClearLogs'))) return;
  await fetch(`${API}/logs`, { method: 'DELETE' });
  loadLogs();
}

// Auto-refresh timer
function startLogAutoRefresh() {
  stopLogAutoRefresh();
  logAutoTimer = setInterval(() => {
    const panel = document.getElementById('panel-settings');
    const logsSection = document.getElementById('settingsSubLogs');
    const cb    = document.getElementById('logAutoRefresh');
    if (panel.classList.contains('active') && logsSection && logsSection.classList.contains('active') && cb && cb.checked) {
      loadLogs();
    }
  }, 4000);
}

function stopLogAutoRefresh() {
  if (logAutoTimer) { clearInterval(logAutoTimer); logAutoTimer = null; }
}

startLogAutoRefresh();

// ── Auth & Settings ───────────────────────────────────────────────────────────
let authToken = localStorage.getItem('dv_token') || '';
let authEnabled = false;
let currentUserId = null;
let currentUserRole = null;

function authHeaders() {
  return authToken ? { 'Authorization': 'Bearer ' + authToken } : {};
}

function updateLogoutButton() {
  const buttonIds = ['btnLogoutSettings', 'btnLogoutProfile'];
  const visible = !!authToken || authEnabled;
  for (const id of buttonIds) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.style.display = visible ? 'inline-flex' : 'none';
  }
}

async function logoutApp() {
  authToken = '';
  currentUserId = null;
  currentUserRole = null;
  localStorage.removeItem('dv_token');
  updateLogoutButton();

  try {
    const sr = await _origFetch(`${API}/auth/status`);
    const status = await sr.json();
    authEnabled = !!status.auth_enabled;
    updateLogoutButton();
    if (status.auth_enabled) {
      showLoginOverlay();
      showStatus('loginStatus', t('js.loggedOut'), 'info');
    } else {
      hideLoginOverlay();
    }
  } catch(e) {
    showLoginOverlay();
  }
}

// Patch global fetch to inject auth header
const _origFetch = window.fetch;
window.fetch = async function(url, opts = {}) {
  const reqUrl = typeof url === 'string' ? url : (url?.url || '');
  const fullUrl = reqUrl ? new URL(reqUrl, window.location.origin) : null;
  const isApiRoute = !!(fullUrl && fullUrl.pathname.startsWith('/api/'));
  const isPublicAuth = !!(fullUrl && (
    fullUrl.pathname.startsWith('/api/auth/login') ||
    fullUrl.pathname.startsWith('/api/auth/register') ||
    fullUrl.pathname === '/api/auth/recovery'
  ));
  const method = (opts.method || 'GET').toUpperCase();

  if (isApiRoute && !isPublicAuth) {
    opts.headers = { ...(opts.headers || {}), ...authHeaders() };
  }

  const canQueue = !!(fullUrl && isQueueableMutation(fullUrl.pathname, method));
  const bodyText = serializeBodyForQueue(opts.body);

  if (canQueue && !navigator.onLine) {
    if (bodyText === null && opts.body != null) {
      return new Response(JSON.stringify({
        error: 'Offline queue ondersteunt dit requesttype niet.'
      }), { status: 503, headers: { 'Content-Type': 'application/json' } });
    }
    queueMutation(fullUrl.pathname + fullUrl.search, method, opts.headers, bodyText);
    return buildQueuedResponse(fullUrl.pathname, method, bodyText);
  }

  try {
    const r = await _origFetch(url, opts);

    if (r.status === 401 && !isPublicAuth) {
      const d = await r.clone().json().catch(() => ({}));
      if (d.auth_required) {
        authToken = '';
        localStorage.removeItem('dv_token');
        updateLogoutButton();
        showLoginOverlay();
      }
    }

    if (canQueue && r.status === 503) {
      if (bodyText === null && opts.body != null) return r;
      queueMutation(fullUrl.pathname + fullUrl.search, method, opts.headers, bodyText);
      return buildQueuedResponse(fullUrl.pathname, method, bodyText);
    }

    return r;
  } catch(e) {
    if (canQueue) {
      if (bodyText === null && opts.body != null) throw e;
      queueMutation(fullUrl.pathname + fullUrl.search, method, opts.headers, bodyText);
      return buildQueuedResponse(fullUrl.pathname, method, bodyText);
    }
    throw e;
  }
};

async function checkAuth() {
  updateLogoutButton();
  try {
    const r = await _origFetch(`${API}/auth/status`);
    const d = await r.json();
    authEnabled = !!d.auth_enabled;
    updateLogoutButton();
    if (d.auth_enabled && !authToken) {
      currentUserId = null;
      currentUserRole = null;
      updateLogoutButton();
      showLoginOverlay(d);
      return false;
    }
    if (d.auth_enabled && authToken) {
      // Verify token still valid
      const t = await _origFetch(`${API}/health`, { headers: authHeaders() });
      if (t.status === 401) {
        authToken = '';
        currentUserId = null;
        currentUserRole = null;
        localStorage.removeItem('dv_token');
        updateLogoutButton();
        showLoginOverlay(d);
        return false;
      }
      // Fetch current user info for ownership checks
      try {
        const mr = await _origFetch(`${API}/auth/me`, { headers: authHeaders() });
        const me = await mr.json();
        if (me.authenticated) {
          currentUserId = me.id;
          currentUserRole = me.role;
        }
      } catch(e) {}
    }
    if (!d.auth_enabled) {
      currentUserId = null;
      currentUserRole = null;
    }
    updateLogoutButton();
    return true;
  } catch(e) { return true; }
}

function showLoginOverlay(statusData) {
  document.getElementById('loginOverlay').style.display = 'flex';
  const regBtn = document.getElementById('btnRegisterToggle');
  if (regBtn) {
    const hasAuth = !!(statusData && statusData.auth_enabled);
    const regEnabled = !!(statusData && statusData.registration_enabled);
    const hasUsers = !!(statusData && statusData.has_users);
    const inviteMode = hasAuth && hasUsers && !regEnabled;
    // Show button when auth is not yet set up, registration is open, or invite codes are available
    regBtn.style.display = (!hasAuth || !hasUsers || regEnabled || inviteMode) ? '' : 'none';
    const i18nKey = inviteMode ? 'login.registerWithInvite' : 'login.register';
    regBtn.setAttribute('data-i18n', i18nKey);
    regBtn.innerHTML = t(i18nKey);
  }
  // Show invite code field only when registration is disabled (invite required)
  const inviteRow = document.getElementById('loginInviteCodeRow');
  if (inviteRow) {
    const inviteMode = !!(statusData && statusData.auth_enabled && statusData.has_users && !statusData.registration_enabled);
    inviteRow.style.display = inviteMode ? 'block' : 'none';
  }
  // Hide the register form when overlay opens
  const regForm = document.getElementById('loginRegisterForm');
  if (regForm) regForm.style.display = 'none';
}

function hideLoginOverlay() {
  document.getElementById('loginOverlay').style.display = 'none';
}

function base64urlToBuffer(base64url) {
  let s = base64url.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const raw = atob(s);
  const buf = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
  return buf.buffer;
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  bytes.forEach(b => binary += String.fromCharCode(b));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function loginPasskey() {
  const btn = document.getElementById('btnLogin');
  btn.innerHTML = t('js.waitingPasskey');
  btn.disabled = true;
  try {
    const optResp = await _origFetch(`${API}/auth/login/options`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const data = await optResp.json();
    if (data.error) { showStatus('loginStatus', data.error, 'error'); btn.innerHTML = t('js.loginBtn'); btn.disabled = false; return; }
    const options = data.options;
    if (!options || !options.challenge) { showStatus('loginStatus', t('js.noChallenge'), 'error'); btn.innerHTML = t('js.loginBtn'); btn.disabled = false; return; }

    options.challenge = base64urlToBuffer(options.challenge);
    if (options.allowCredentials) {
      options.allowCredentials = options.allowCredentials.map(c => ({
        ...c, id: base64urlToBuffer(c.id)
      }));
    }

    const assertion = await navigator.credentials.get({ publicKey: options });

    const credential = {
      id: assertion.id,
      rawId: bufferToBase64url(assertion.rawId),
      response: {
        authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
        clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
        signature: bufferToBase64url(assertion.response.signature),
        userHandle: assertion.response.userHandle ? bufferToBase64url(assertion.response.userHandle) : null,
      },
      type: assertion.type,
      authenticatorAttachment: assertion.authenticatorAttachment,
    };

    const verResp = await _origFetch(`${API}/auth/login/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential })
    });
    const verData = await verResp.json();
    if (verData.token) {
      authToken = verData.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      hideLoginOverlay();
      init();
    } else {
      showStatus('loginStatus', verData.error || t('js.loginFailed'), 'error');
    }
  } catch(e) {
    if (e.name === 'NotAllowedError') {
      showStatus('loginStatus', t('js.passkeyCancelled'), 'error');
    } else {
      showStatus('loginStatus', t('js.error', e.message), 'error');
    }
  }
  btn.innerHTML = t('js.loginBtn');
  btn.disabled = false;
}

async function registerPasskey() {
  const username = document.getElementById('registerUsername').value.trim() || 'admin';
  const credName = document.getElementById('registerCredName').value.trim() || 'Passkey';
  await _doRegisterPasskey(username, credName);
}

async function registerAdditionalPasskey() {
  const credName = document.getElementById('addCredName').value.trim() || 'Passkey';
  // Use the current user's username from the JWT token
  let username = 'admin';
  try {
    const me = await fetch(`${API}/auth/me`).then(r => r.json());
    if (me.username) username = me.username;
  } catch(e) {}
  await _doRegisterPasskey(username, credName);
}

async function _doRegisterPasskey(username, credName) {
  try {
    showStatus('authStatus', t('js.waitingPasskey'), 'info');

    const optResp = await _origFetch(`${API}/auth/register/options`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ username })
    });
    const { user_id, options } = await optResp.json();

    options.challenge = base64urlToBuffer(options.challenge);
    options.user.id = base64urlToBuffer(options.user.id);
    if (options.excludeCredentials) {
      options.excludeCredentials = options.excludeCredentials.map(c => ({
        ...c, id: base64urlToBuffer(c.id)
      }));
    }

    const attestation = await navigator.credentials.create({ publicKey: options });

    const credential = {
      id: attestation.id,
      rawId: bufferToBase64url(attestation.rawId),
      response: {
        attestationObject: bufferToBase64url(attestation.response.attestationObject),
        clientDataJSON: bufferToBase64url(attestation.response.clientDataJSON),
      },
      type: attestation.type,
      authenticatorAttachment: attestation.authenticatorAttachment,
    };

    const verResp = await _origFetch(`${API}/auth/register/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ user_id, username, credential_name: credName, credential })
    });
    const verData = await verResp.json();
    if (verData.token) {
      authToken = verData.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      showStatus('authStatus', t('js.passkeyRegistered', credName), 'success');
      // Show recovery code if provided
      if (verData.recovery_code) {
        document.getElementById('recoveryCodeDisplay').textContent = verData.recovery_code;
        document.getElementById('recoveryCodeCard').style.display = 'block';
      }
      loadAuthSettings();
    } else {
      showStatus('authStatus', verData.error || t('js.registrationFailed'), 'error');
    }
  } catch(e) {
    if (e.name === 'NotAllowedError') {
      showStatus('authStatus', t('js.passkeyRegCancelled'), 'error');
    } else {
      showStatus('authStatus', t('js.error', e.message), 'error');
    }
  }
}

// ---------------------------------------------------------------------------
// Profile management
// ---------------------------------------------------------------------------

async function loadProfile() {
  try {
    const r = await fetch(`${API}/auth/me`);
    const me = await r.json();
    if (!me.authenticated) return;

    document.getElementById('profileUsername').value = me.username || '';
    document.getElementById('profileFirstName').value = me.first_name || '';
    document.getElementById('profileLastName').value = me.last_name || '';

    const img = document.getElementById('profileAvatarImg');
    const placeholder = document.getElementById('profileAvatarPlaceholder');
    const removeBtn = document.getElementById('profileAvatarRemoveBtn');
    if (me.avatar_url) {
      img.src = me.avatar_url;
      img.style.display = 'block';
      placeholder.style.display = 'none';
      removeBtn.style.display = 'inline-flex';
    } else {
      img.style.display = 'none';
      img.src = '';
      placeholder.style.display = '';
      removeBtn.style.display = 'none';
    }
    document.getElementById('profileUsernameError').style.display = 'none';
    document.getElementById('profileStatus').textContent = '';
  } catch(e) {}
}

// ── API key management ────────────────────────────────────────────────────────

async function loadApiKeys() {
  const card = document.getElementById('apiKeysCard');
  const list = document.getElementById('apiKeysList');
  if (!card || !currentUserId) return;
  card.style.display = '';
  try {
    const r = await fetch(`${API}/user/api-keys`);
    if (!r.ok) { card.style.display = 'none'; return; }
    const keys = await r.json();
    if (!keys.length) {
      list.innerHTML = `<div style="font-size:0.82rem; color:var(--text-muted);">Nog geen API-sleutels aangemaakt.</div>`;
      return;
    }
    list.innerHTML = keys.map(k => `
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:8px; margin-bottom:8px;">
        <div>
          <div style="font-size:0.85rem; font-weight:500;">${k.label || '<span style="color:var(--text-muted);">Naamloos</span>'}</div>
          <div style="font-size:0.72rem; color:var(--text-muted);">Aangemaakt: ${(k.created_at||'').slice(0,10)}</div>
        </div>
        <button class="btn btn-danger" onclick="revokeApiKey(${k.id})" style="padding:5px 10px; font-size:0.74rem;">🗑 Intrekken</button>
      </div>
    `).join('');
  } catch(e) { card.style.display = 'none'; }
}

async function generateApiKey() {
  const label = (document.getElementById('apiKeyLabel').value || '').trim();
  const statusEl = document.getElementById('apiKeyStatus');
  const revealEl = document.getElementById('apiKeyReveal');
  const revealVal = document.getElementById('apiKeyRevealValue');
  try {
    const r = await fetch(`${API}/user/api-keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    const data = await r.json();
    document.getElementById('apiKeyLabel').value = '';
    revealVal.textContent = data.key;
    revealEl.style.display = '';
    revealEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    await loadApiKeys();
    if (statusEl) statusEl.innerHTML = '';
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${e.message}</span>`;
  }
}

function copyApiKey() {
  const val = document.getElementById('apiKeyRevealValue').textContent;
  navigator.clipboard.writeText(val).catch(() => {});
}

async function revokeApiKey(id) {
  const statusEl = document.getElementById('apiKeyStatus');
  try {
    const r = await fetch(`${API}/user/api-keys/${id}`, { method: 'DELETE' });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    await loadApiKeys();
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${e.message}</span>`;
  }
}

async function loadMcpLogs() {
  const card = document.getElementById('mcpLogsCard');
  const list = document.getElementById('mcpLogsList');
  if (!card || !currentUserId) return;
  try {
    const r = await fetch(`${API}/user/mcp-logs?limit=50`);
    if (!r.ok) { card.style.display = 'none'; return; }
    const logs = await r.json();
    card.style.display = '';
    if (!logs.length) {
      list.innerHTML = `<div style="text-align:center; padding:20px; color:var(--text-muted); font-family:inherit; font-size:0.84rem;">Nog geen MCP-activiteit geregistreerd.</div>`;
      return;
    }
    const levelColor = { error: 'var(--danger)', warn: 'var(--warning,#e8c547)', success: 'var(--success)', info: 'var(--text-muted)' };
    list.innerHTML = logs.map(l => {
      const ts = (l.timestamp || '').replace('T',' ').slice(0,19);
      const tool = (l.message || '').replace(/^Tool:\s*/i, '');
      const color = levelColor[l.level] || 'var(--text-muted)';
      const detail = l.detail ? `<div style="color:var(--text-muted); margin-top:3px; font-size:0.70rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${(l.detail||'').slice(0,120)}</div>` : '';
      return `<div style="padding:8px 10px; border-bottom:1px solid var(--border);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="color:${color}; font-weight:600;">${tool}</span>
          <span style="font-size:0.68rem; color:var(--text-muted); flex-shrink:0; margin-left:12px;">${ts}</span>
        </div>${detail}
      </div>`;
    }).join('');
  } catch(e) { card.style.display = 'none'; }
}

async function saveProfile() {
  const username = (document.getElementById('profileUsername').value || '').trim();
  const firstName = (document.getElementById('profileFirstName').value || '').trim();
  const lastName = (document.getElementById('profileLastName').value || '').trim();
  const errEl = document.getElementById('profileUsernameError');
  errEl.style.display = 'none';

  if (!username) {
    errEl.textContent = t('settings.profileUsernameRequired');
    errEl.style.display = 'block';
    return;
  }

  try {
    const r = await fetch(`${API}/auth/profile`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ username, first_name: firstName, last_name: lastName })
    });
    const d = await r.json();
    if (!r.ok) {
      if (d.field === 'username') {
        errEl.textContent = t('settings.profileUsernameTaken');
        errEl.style.display = 'block';
      } else {
        showStatus('profileStatus', d.error || 'Error', 'error');
      }
      return;
    }
    // Update token if re-issued
    if (d.token) {
      authToken = d.token;
      localStorage.setItem('authToken', d.token);
    }
    showStatus('profileStatus', t('settings.profileSaved'), 'success');
  } catch(e) {
    showStatus('profileStatus', t('js.error', e.message), 'error');
  }
}

async function uploadProfileAvatar(input) {
  if (!input.files || !input.files[0]) return;
  const file = input.files[0];
  if (file.size > 2 * 1024 * 1024) {
    showStatus('profileStatus', t('settings.profilePhotoTooLarge'), 'error');
    input.value = '';
    return;
  }
  const formData = new FormData();
  formData.append('avatar', file);
  try {
    const r = await fetch(`${API}/auth/profile/avatar`, { method: 'POST', body: formData });
    const d = await r.json();
    if (!r.ok) {
      showStatus('profileStatus', d.error || 'Error', 'error');
      return;
    }
    const img = document.getElementById('profileAvatarImg');
    img.src = d.avatar_url + '?t=' + Date.now();
    img.style.display = 'block';
    document.getElementById('profileAvatarPlaceholder').style.display = 'none';
    document.getElementById('profileAvatarRemoveBtn').style.display = 'inline-flex';
    showStatus('profileStatus', t('settings.profilePhotoUpdated'), 'success');
  } catch(e) {
    showStatus('profileStatus', t('js.error', e.message), 'error');
  }
  input.value = '';
}

async function removeProfileAvatar() {
  try {
    const r = await fetch(`${API}/auth/profile/avatar`, { method: 'DELETE' });
    if (!r.ok) return;
    document.getElementById('profileAvatarImg').style.display = 'none';
    document.getElementById('profileAvatarImg').src = '';
    document.getElementById('profileAvatarPlaceholder').style.display = '';
    document.getElementById('profileAvatarRemoveBtn').style.display = 'none';
    showStatus('profileStatus', t('settings.profilePhotoRemoved'), 'success');
  } catch(e) {}
}

async function loadAuthSettings() {
  try {
    const sr = await fetch(`${API}/auth/status`);
    const status = await sr.json();
    authEnabled = !!status.auth_enabled;
    const isAdmin = status.role === 'admin';
    updateLogoutButton();

    // Auth toggle: admin only
    const authToggleRow = document.getElementById('authToggle').closest('div');
    if (authToggleRow) authToggleRow.style.display = (isAdmin || !status.auth_enabled) ? '' : 'none';

    document.getElementById('authToggle').checked = status.auth_enabled;
    document.getElementById('authStatusBadge').textContent = status.auth_enabled ? t('js.authActive') : t('js.authOff');
    document.getElementById('authStatusBadge').style.color = status.auth_enabled ? 'var(--success)' : 'var(--text-muted)';

    if (status.has_credentials) {
      document.getElementById('authNoCredentials').style.display = 'none';
      document.getElementById('addPasskeySection').style.display = 'block';
    } else {
      document.getElementById('authNoCredentials').style.display = 'block';
      document.getElementById('addPasskeySection').style.display = 'none';
    }

    const cr = await fetch(`${API}/auth/credentials`);
    const creds = await cr.json();
    const list = document.getElementById('credentialsList');
    if (creds.length) {
      list.innerHTML = creds.map(c => `
        <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
          <div style="font-size:1.2rem;">🔑</div>
          <div style="flex:1;">
            <div style="font-weight:500; font-size:0.88rem;">${c.credential_name || 'Passkey'}</div>
            <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
              ${c.username} · ${(c.created_at||'').slice(0,10)} · ${c.sign_count} ${t('js.logins')}
            </div>
          </div>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.75rem;" onclick="deleteCredential('${c.id}')">✕</button>
        </div>
      `).join('');
    } else {
      list.innerHTML = '';
    }
    // Load admin panel if user is admin
    loadAdminPanel();

    // Show registration toggle for admin only
    const regToggleRow = document.getElementById('registrationToggleRow');
    if (regToggleRow) {
      if (status.auth_enabled && isAdmin) {
        regToggleRow.style.display = 'block';
        document.getElementById('registrationToggle').checked = !!status.registration_enabled;
      } else {
        regToggleRow.style.display = 'none';
      }
    }
  } catch(e) {}
}

async function deleteCredential(id) {
  if (!confirm(t('js.confirmDeletePasskey'))) return;
  await fetch(`${API}/auth/credentials/${encodeURIComponent(id)}`, { method: 'DELETE' });
  loadAuthSettings();
}

async function toggleAuth() {
  const enabled = document.getElementById('authToggle').checked;
  authEnabled = !!enabled;
  updateLogoutButton();
  const r = await fetch(`${API}/auth/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  });
  const d = await r.json();
  if (d.error) {
    showStatus('authStatus', d.error, 'error');
    document.getElementById('authToggle').checked = !enabled;
    authEnabled = !enabled;
    updateLogoutButton();
  } else {
    showStatus('authStatus', enabled ? t('js.authEnabled') : t('js.authDisabled'), 'success');
    authEnabled = !!enabled;
    updateLogoutButton();
  }
  loadAuthSettings();
}

// ── Recovery Login ────────────────────────────────────────────────────────────

function toggleRecoveryLogin() {
  const form = document.getElementById('recoveryLoginForm');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

async function recoveryLogin() {
  const username = document.getElementById('recoveryUsername').value.trim();
  const code = document.getElementById('recoveryCode').value.trim();
  if (!username || !code) { showStatus('loginStatus', t('login.recoveryFillIn'), 'error'); return; }
  try {
    const r = await _origFetch(`${API}/auth/recovery`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, recovery_code: code })
    });
    const d = await r.json();
    if (d.token) {
      authToken = d.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      hideLoginOverlay();
      init();
      // Show new recovery code
      if (d.new_recovery_code) {
        setTimeout(() => {
          alert(t('login.newRecoveryAlert', d.new_recovery_code));
        }, 500);
      }
    } else {
      showStatus('loginStatus', d.error || t('login.recoveryFailed'), 'error');
    }
  } catch(e) {
    showStatus('loginStatus', `Fout: ${e.message}`, 'error');
  }
}

// ── Login: Registration ───────────────────────────────────────────────────────

function toggleLoginRegister() {
  const form = document.getElementById('loginRegisterForm');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

async function loginRegisterPasskey() {
  const username = document.getElementById('loginRegUsername').value.trim();
  const credName = document.getElementById('loginRegCredName').value.trim() || 'Passkey';
  const inviteCode = (document.getElementById('loginInviteCode') ? document.getElementById('loginInviteCode').value.trim() : '') || undefined;
  if (!username) { showStatus('loginStatus', t('login.registerFillIn'), 'error'); return; }
  const btn = document.getElementById('btnLoginRegister');
  btn.innerHTML = t('js.waitingPasskey');
  btn.disabled = true;
  try {
    const optResp = await _origFetch(`${API}/auth/register/options`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, invite_code: inviteCode })
    });
    const optData = await optResp.json();
    if (optData.error) { showStatus('loginStatus', optData.error, 'error'); btn.innerHTML = t('login.registerBtn'); btn.disabled = false; return; }
    const { user_id, options } = optData;

    options.challenge = base64urlToBuffer(options.challenge);
    options.user.id = base64urlToBuffer(options.user.id);
    if (options.excludeCredentials) {
      options.excludeCredentials = options.excludeCredentials.map(c => ({
        ...c, id: base64urlToBuffer(c.id)
      }));
    }

    const attestation = await navigator.credentials.create({ publicKey: options });

    const credential = {
      id: attestation.id,
      rawId: bufferToBase64url(attestation.rawId),
      response: {
        attestationObject: bufferToBase64url(attestation.response.attestationObject),
        clientDataJSON: bufferToBase64url(attestation.response.clientDataJSON),
      },
      type: attestation.type,
      authenticatorAttachment: attestation.authenticatorAttachment,
    };

    const verResp = await _origFetch(`${API}/auth/register/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id, username, credential_name: credName, invite_code: inviteCode, credential })
    });
    const verData = await verResp.json();
    if (verData.token) {
      authToken = verData.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      hideLoginOverlay();
      init();
      if (verData.recovery_code) {
        setTimeout(() => {
          alert(t('login.newRecoveryAlert', verData.recovery_code));
        }, 500);
      }
    } else {
      showStatus('loginStatus', verData.error || t('js.registrationFailed'), 'error');
    }
  } catch(e) {
    if (e.name === 'NotAllowedError') {
      showStatus('loginStatus', t('js.passkeyRegCancelled'), 'error');
    } else {
      showStatus('loginStatus', t('js.error', e.message), 'error');
    }
  }
  btn.innerHTML = t('login.registerBtn');
  btn.disabled = false;
}

async function toggleRegistration() {
  const enabled = document.getElementById('registrationToggle').checked;
  const r = await fetch(`${API}/settings/registration`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ registration_enabled: enabled })
  });
  const d = await r.json();
  if (!r.ok) {
    // Revert checkbox on failure
    document.getElementById('registrationToggle').checked = !enabled;
    showStatus('authStatus', d.error || 'Error', 'error');
  } else {
    showStatus('authStatus', enabled ? t('js.registrationEnabled') : t('js.registrationDisabled'), 'success');
  }
}

// ---------------------------------------------------------------------------
// Admin: Invite Codes
// ---------------------------------------------------------------------------

async function createInviteCode() {
  const username = (document.getElementById('newInviteUsername').value || '').trim();
  if (!username) { showStatus('adminInviteStatus', t('login.registerFillIn'), 'error'); return; }
  try {
    const r = await fetch(`${API}/auth/invite`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ username })
    });
    const d = await r.json();
    if (!r.ok) { showStatus('adminInviteStatus', d.error || t('js.inviteCreateError'), 'error'); return; }
    document.getElementById('inviteCodeForUser').textContent = d.username;
    document.getElementById('inviteCodeDisplay').textContent = d.code;
    document.getElementById('inviteCodeExpiry').textContent = `${t('settings.inviteExpires')}: ${(d.expires_at||'').slice(0,16).replace('T',' ')} UTC`;
    document.getElementById('inviteCodeResult').style.display = 'block';
    document.getElementById('newInviteUsername').value = '';
    showStatus('adminInviteStatus', t('js.inviteCreated'), 'success');
    await loadInviteCodes();
  } catch(e) {
    showStatus('adminInviteStatus', t('js.error', e.message), 'error');
  }
}

function copyInviteCode() {
  const code = document.getElementById('inviteCodeDisplay').textContent;
  navigator.clipboard.writeText(code).then(() => {
    showStatus('adminInviteStatus', t('js.inviteCopied'), 'success');
  }).catch(() => {});
}

async function loadInviteCodes() {
  try {
    const r = await fetch(`${API}/auth/invite`, { headers: authHeaders() });
    if (!r.ok) return;
    const codes = await r.json();
    const list = document.getElementById('adminInviteList');
    if (!list) return;
    if (!codes.length) {
      list.innerHTML = `<div style="font-size:0.82rem; color:var(--text-muted);">${t('js.noInviteCodes')}</div>`;
      return;
    }
    const now = new Date();
    list.innerHTML = codes.map(c => {
      const isUsed = !!c.used_at;
      const isExpired = !isUsed && new Date(c.expires_at) < now;
      const statusLabel = isUsed ? `<span style="color:var(--text-muted)">${t('settings.inviteUsed')}</span>`
                        : isExpired ? `<span style="color:var(--danger)">${t('settings.inviteExpired')}</span>`
                        : `<span style="color:var(--success)">${t('settings.inviteActive')}</span>`;
      return `
        <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:6px; font-size:0.82rem;">
          <div style="flex:1;">
            <strong>${c.username}</strong>
            <span style="color:var(--text-muted); margin-left:8px;">${(c.expires_at||'').slice(0,16).replace('T',' ')} UTC</span>
            <span style="margin-left:8px;">${statusLabel}</span>
          </div>
          ${!isUsed ? `<button class="btn btn-danger" style="padding:4px 8px; font-size:0.75rem;" onclick="revokeInviteCode(${c.id})">${t('settings.inviteRevoke')}</button>` : ''}
        </div>`;
    }).join('');
  } catch(e) {}
}

async function revokeInviteCode(id) {
  try {
    const r = await fetch(`${API}/auth/invite/${id}`, { method: 'DELETE', headers: authHeaders() });
    if (r.ok) {
      showStatus('adminInviteStatus', t('js.inviteRevoked'), 'success');
      await loadInviteCodes();
    }
  } catch(e) {}
}

async function loadAdminPanel() {
  try {
    const me = await fetch(`${API}/auth/me`).then(r => r.json());
    const isAdmin = !me.authenticated || me.role === 'admin';
    const isMemberGroups = me.authenticated && me.role === 'MemberGroups';
    if (!isAdmin && !isMemberGroups) {
      document.getElementById('adminUsersCard').style.display = 'none';
      document.getElementById('adminGroupsCard').style.display = 'none';
      document.getElementById('myGroupsCard').style.display = 'none';
      return;
    }
    if (isAdmin) {
      document.getElementById('adminGroupsCard').style.display = 'block';
      document.getElementById('myGroupsCard').style.display = 'none';
      if (me.authenticated) {
        document.getElementById('adminUsersCard').style.display = 'block';
        await loadAdminUsers();
        document.getElementById('adminInviteCard').style.display = 'block';
        await loadInviteCodes();
      } else {
        document.getElementById('adminUsersCard').style.display = 'none';
        document.getElementById('adminInviteCard').style.display = 'none';
      }
      await loadAdminGroups();
    } else {
      // MemberGroups
      document.getElementById('adminGroupsCard').style.display = 'none';
      document.getElementById('adminUsersCard').style.display = 'none';
      document.getElementById('adminInviteCard').style.display = 'none';
      document.getElementById('myGroupsCard').style.display = 'block';
      await loadMyGroups();
    }
  } catch(e) {
    document.getElementById('adminUsersCard').style.display = 'none';
    document.getElementById('adminGroupsCard').style.display = 'none';
    document.getElementById('adminInviteCard').style.display = 'none';
    const mgc = document.getElementById('myGroupsCard');
    if (mgc) mgc.style.display = 'none';
  }
}

async function loadAdminUsers() {
  try {
    const r = await fetch(`${API}/auth/users`);
    const users = await r.json();
    const list = document.getElementById('adminUsersList');
    list.innerHTML = users.map(u => {
      const roleIcon = u.role === 'admin' ? '👑' : u.role === 'MemberGroups' ? '🔑' : '👤';
      const nextRole = u.role === 'admin' ? 'MemberGroups' : u.role === 'MemberGroups' ? 'user' : 'admin';
      const nextIcon = nextRole === 'admin' ? '👑' : nextRole === 'MemberGroups' ? '🔑' : '👤';
      return `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
        <div style="font-size:1.2rem;">${roleIcon}</div>
        <div style="flex:1;">
          <div style="font-weight:500; font-size:0.88rem;">${u.display_name || u.username}
            <span class="tag" style="font-size:0.65rem; margin-left:6px;">${u.role}</span>
          </div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
            ${u.username} · ${(u.created_at||'').slice(0,10)} · ${t('js.passkeyCount', u.credential_count)}
          </div>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="resetUserPasskey('${u.id}','${u.username}')" title="Reset passkey">🔑</button>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="toggleUserRole('${u.id}','${u.role}')" title="Wissel rol (${u.role} \u2192 ${nextRole})">${nextIcon}</button>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.7rem;" onclick="deleteUser('${u.id}','${u.username}')" title="Verwijder">✕</button>
        </div>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function deleteUser(id, name) {
  if (!confirm(t('js.confirmDeleteUser', name))) return;
  await fetch(`${API}/auth/users/${encodeURIComponent(id)}`, { method: 'DELETE' });
  loadAdminUsers();
  loadAuthSettings();
}

async function toggleUserRole(id, currentRole) {
  const newRole = currentRole === 'admin' ? 'MemberGroups' : currentRole === 'MemberGroups' ? 'user' : 'admin';
  await fetch(`${API}/auth/users/${encodeURIComponent(id)}/role`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: newRole })
  });
  loadAdminUsers();
}

async function resetUserPasskey(id, name) {
  if (!confirm(t('js.confirmResetPasskey', name))) return;
  await fetch(`${API}/auth/users/${encodeURIComponent(id)}/reset-passkey`, { method: 'POST' });
  loadAdminUsers();
  loadAuthSettings();
}

// ── Admin: Group Management ───────────────────────────────────────────────────

async function loadAdminGroups() {
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    const list = document.getElementById('adminGroupsList');
    if (!groups.length) {
      list.innerHTML = '<div style="color:var(--text-muted); font-size:0.85rem;">' + t('js.noGroups') + '</div>';
      return;
    }
    list.innerHTML = groups.map(g => `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
        <div style="font-size:1.2rem;">📁</div>
        <div style="flex:1;">
          <div style="font-weight:500; font-size:0.88rem;">${g.name}</div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
            ${t('js.groupStatsFull', g.member_count, g.movie_count, g.created_by_username || '?')}
          </div>
        </div>
        <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="manageGroupMembers(${g.id},'${g.name}')" title="${t('js.manageMembersBtn')}">👥</button>
        <button class="btn btn-danger" style="padding:6px 10px; font-size:0.7rem;" onclick="deleteGroup(${g.id},'${g.name}')" title="${t('js.deleteTitle')}">✕</button>
      </div>
    `).join('');
  } catch(e) {}
}

async function createGroup() {
  const name = document.getElementById('newGroupName').value.trim();
  if (!name) return;
  const r = await fetch(`${API}/groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  document.getElementById('newGroupName').value = '';
  loadAdminGroups();
}

async function deleteGroup(id, name) {
  if (!confirm(t('js.confirmDeleteGroup', name))) return;
  await fetch(`${API}/groups/${id}`, { method: 'DELETE' });
  loadAdminGroups();
}

async function manageGroupMembers(groupId, groupName) {
  const [membersResp, usersResp] = await Promise.all([
    fetch(`${API}/groups/${groupId}/members`),
    fetch(`${API}/auth/users`)
  ]);
  const members = await membersResp.json();
  const allUsers = await usersResp.json();
  const memberIds = new Set(members.map(m => m.id));
  const nonMembers = allUsers.filter(u => !memberIds.has(u.id));

  let html = `<h3 style="margin:0 0 16px 0; font-size:1rem;">${t('js.membersOf', groupName)}</h3>`;
  if (members.length) {
    html += members.map(m => `
      <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:6px;">
        <span style="flex:1; font-size:0.85rem;">${m.display_name || m.username}</span>
        <button class="btn btn-danger" style="padding:4px 8px; font-size:0.7rem;" onclick="removeGroupMember(${groupId},'${m.id}','${groupName}')">✕</button>
      </div>
    `).join('');
  } else {
    html += `<div style="color:var(--text-muted); font-size:0.85rem; margin-bottom:10px;">${t('js.noMembers')}</div>`;
    html += `<div style="display:flex; gap:8px; align-items:center; margin-top:12px;">
      <select id="addMemberSelect" style="flex:1; padding:8px; background:var(--surface2); color:var(--text); border:1px solid var(--border); border-radius:6px;">
        ${nonMembers.map(u => `<option value="${u.id}">${u.display_name || u.username}</option>`).join('')}
      </select>
      <button class="btn btn-primary" style="padding:8px 14px; font-size:0.8rem;" onclick="addGroupMember(${groupId},'${groupName}')">${t('js.addMemberBtn')}</button>
    </div>`;
  }
  html += `<button class="btn btn-secondary" style="margin-top:16px; width:100%; justify-content:center;" onclick="loadAdminGroups(); this.closest('.card').querySelector('#groupMemberPanel').remove();">${t('js.close')}</button>`;

  // Replace groups list content with member management
  const panel = document.createElement('div');
  panel.id = 'groupMemberPanel';
  panel.style.cssText = 'padding:16px; background:var(--surface); border:1px solid var(--border); border-radius:8px; margin-top:12px;';
  panel.innerHTML = html;
  const existing = document.getElementById('groupMemberPanel');
  if (existing) existing.remove();
  document.getElementById('adminGroupsList').appendChild(panel);
}

async function addGroupMember(groupId, groupName) {
  const userId = document.getElementById('addMemberSelect').value;
  await fetch(`${API}/groups/${groupId}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId })
  });
  manageGroupMembers(groupId, groupName);
}

async function removeGroupMember(groupId, userId, groupName) {
  await fetch(`${API}/groups/${groupId}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' });
  manageGroupMembers(groupId, groupName);
}

// ── MemberGroups: Mijn Groepen ────────────────────────────────────────────────

async function loadMyGroups() {
  const list = document.getElementById('myGroupsList');
  if (!list) return;
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    const owned = groups.filter(g => g.my_role === 'owner');
    const member = groups.filter(g => g.my_role !== 'owner');
    if (!owned.length && !member.length) {
      list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noMyGroups')}</div>`;
      return;
    }
    let html = '';
    if (owned.length) {
      html += `<div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em; margin-bottom:8px;">${t('js.myGroupsOwned')}</div>`;
      html += owned.map(g => `
        <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
          <div style="font-size:1.2rem;">👑</div>
          <div style="flex:1;">
            <div style="font-weight:500; font-size:0.88rem;">${g.name}</div>
            <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">${t('js.groupStats', g.member_count, g.movie_count)}</div>
          </div>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="manageMyGroupMembers(${g.id},'${g.name}')" title="${t('js.manageMembersTitle')}">👥</button>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.7rem;" onclick="deleteMyGroup(${g.id},'${g.name}')" title="${t('js.deleteTitle')}">✕</button>
        </div>
      `).join('');
    }
    if (member.length) {
      html += `<div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em; margin-bottom:8px; margin-top:16px;">${t('js.myGroupsMember')}</div>`;
      html += member.map(g => `
        <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
          <div style="font-size:1.2rem;">📁</div>
          <div style="flex:1;">
            <div style="font-weight:500; font-size:0.88rem;">${g.name}</div>
            <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">${t('js.groupStatsByOwner', g.created_by_username || '?', g.movie_count)}</div>
          </div>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="leaveMyGroup(${g.id},'${g.name}')">${t('js.leaveGroup')}</button>
        </div>
      `).join('');
    }
    list.innerHTML = html;
    // Manage panel passthrough
    const existing = document.getElementById('myGroupMemberPanel');
    if (existing) existing.remove();
  } catch(e) {
    list.innerHTML = `<div style="color:var(--danger); font-size:0.85rem;">${t('js.myGroupsLoadError')}</div>`;
  }
}

async function createMyGroup() {
  const nameEl = document.getElementById('myNewGroupName');
  const name = nameEl.value.trim();
  if (!name) return;
  const r = await fetch(`${API}/groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  const d = await r.json();
  if (d.error) { showStatus('myGroupStatus', d.error, 'error'); return; }
  nameEl.value = '';
  showStatus('myGroupStatus', t('js.myGroupCreated', name), 'success');
  await loadMyGroups();
  await _loadGroupFilter();
}

async function deleteMyGroup(id, name) {
  if (!confirm(t('js.confirmDeleteMyGroup', name))) return;
  await fetch(`${API}/groups/${id}`, { method: 'DELETE' });
  await loadMyGroups();
  await _loadGroupFilter();
  filterMovies();
}

async function leaveMyGroup(groupId, groupName) {
  if (!confirm(t('js.confirmLeaveGroup', groupName))) return;
  await fetch(`${API}/groups/${groupId}/members/${encodeURIComponent(currentUserId)}`, { method: 'DELETE' });
  await loadMyGroups();
  await _loadGroupFilter();
  filterMovies();
}

async function manageMyGroupMembers(groupId, groupName) {
  const members = await fetch(`${API}/groups/${groupId}/members`).then(r => r.json());
  let html = `<div style="font-weight:500; margin-bottom:12px; font-size:0.9rem;">${t('js.membersOf', groupName)}</div>`;
  if (members.length) {
    html += members.map(m => {
      const isSelf = m.id === currentUserId;
      const isOwner = m.group_role === 'owner';
      return `
        <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:var(--surface); border:1px solid var(--border); border-radius:6px; margin-bottom:6px;">
          <span style="font-size:0.9rem;">${isOwner ? '👑' : '👤'}</span>
          <span style="flex:1; font-size:0.85rem;">${m.display_name || m.username}${isSelf ? t('js.selfIndicator') : ''}</span>
          ${!isOwner ? `<button class="btn btn-danger" style="padding:4px 8px; font-size:0.7rem;" onclick="removeMyGroupMember(${groupId},'${m.id}','${groupName}')">✕</button>` : ''}
        </div>`;
    }).join('');
  } else {
    html += `<div style="color:var(--text-muted); font-size:0.85rem; margin-bottom:10px;">${t('js.noMembers')}</div>`;
  }
  html += `
    <div style="border-top:1px solid var(--border); margin-top:14px; padding-top:14px;">
      <div style="font-size:0.8rem; font-weight:500; margin-bottom:8px;">${t('js.inviteUser')}</div>
      <div style="display:flex; gap:8px; align-items:center;">
        <input type="text" id="inviteUsernameInput_${groupId}" placeholder="${t('js.usernamePlaceholder')}"
               style="flex:1; padding:8px; background:var(--surface2); color:var(--text); border:1px solid var(--border); border-radius:6px; font-size:0.84rem;">
        <button class="btn btn-primary" style="padding:8px 14px; font-size:0.8rem;" onclick="sendGroupInvite(${groupId},'${groupName}')">${t('js.inviteBtn')}</button>
      </div>
      <div id="inviteStatus_${groupId}" class="status-msg" style="margin-top:6px;"></div>
    </div>
    <button class="btn btn-secondary" style="margin-top:14px; width:100%; justify-content:center;" onclick="document.getElementById('myGroupMemberPanel').remove(); loadMyGroups();">${t('js.close')}</button>
  `;
  const panel = document.createElement('div');
  panel.id = 'myGroupMemberPanel';
  panel.style.cssText = 'padding:16px; background:var(--surface); border:1px solid var(--border); border-radius:8px; margin-top:12px;';
  panel.innerHTML = html;
  const existing = document.getElementById('myGroupMemberPanel');
  if (existing) existing.remove();
  document.getElementById('myGroupsList').appendChild(panel);
}

async function removeMyGroupMember(groupId, userId, groupName) {
  await fetch(`${API}/groups/${groupId}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' });
  manageMyGroupMembers(groupId, groupName);
}

async function sendGroupInvite(groupId, groupName) {
  const input = document.getElementById(`inviteUsernameInput_${groupId}`);
  const statusEl = document.getElementById(`inviteStatus_${groupId}`);
  const username = input.value.trim();
  if (!username) return;
  const r = await fetch(`${API}/groups/${groupId}/invite`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username })
  });
  const d = await r.json();
  if (d.error) {
    showStatus(`inviteStatus_${groupId}`, d.error, 'error');
  } else {
    showStatus(`inviteStatus_${groupId}`, t('js.inviteSent', username), 'success');
    input.value = '';
  }
}

// ── Invite notifications ──────────────────────────────────────────────────────

async function loadInviteNotifications() {
  try {
    const r = await fetch(`${API}/invites`);
    if (!r.ok) return;
    const invites = await r.json();
    const bell = document.getElementById('inviteBell');
    const badge = document.getElementById('inviteBadge');
    if (!bell) return;
    if (invites.length > 0) {
      bell.style.display = '';
      badge.style.display = '';
      badge.textContent = invites.length;
    } else {
      bell.style.display = 'none';
    }
  } catch(e) {}
}

async function openInvitePanel() {
  const panel = document.getElementById('invitePanel');
  const list = document.getElementById('invitePanelList');
  panel.style.display = 'flex';
  list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('general.loading')}</div>`;
  try {
    const r = await fetch(`${API}/invites`);
    const invites = await r.json();
    if (!invites.length) {
      list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem; text-align:center; padding:20px;">${t('js.noInvites')}</div>`;
      return;
    }
    list.innerHTML = invites.map(inv => `
      <div style="background:var(--surface2); border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:10px;">
        <div style="font-weight:500; margin-bottom:4px;">📁 ${inv.group_name}</div>
        <div style="font-size:0.8rem; color:var(--text-muted); margin-bottom:12px;">
          ${t('js.inviteFrom', `<strong>${inv.inviter_display_name || inv.inviter_username}</strong>`)} · ${(inv.created_at||'').slice(0,10)}
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-primary" style="flex:1; justify-content:center;" onclick="respondInvite(${inv.id},'accept')">${t('js.inviteAccept')}</button>
          <button class="btn btn-secondary" style="flex:1; justify-content:center;" onclick="respondInvite(${inv.id},'decline')">${t('js.inviteDecline')}</button>
        </div>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = `<div style="color:var(--danger); font-size:0.85rem;">${t('js.inviteLoadError')}</div>`;
  }
}

function closeInvitePanel() {
  document.getElementById('invitePanel').style.display = 'none';
}

async function respondInvite(inviteId, action) {
  const r = await fetch(`${API}/invites/${inviteId}/${action}`, { method: 'POST' });
  const d = await r.json();
  if (action === 'accept' && d.status === 'accepted') {
    await loadCollection();
    await _loadGroupFilter();
  }
  await loadInviteNotifications();
  await openInvitePanel();
}

// ── Lists: Watchlist & Watched ────────────────────────────────────────────────

function switchListsSubmenu(sub) {
  document.querySelectorAll('[data-lists-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-lists-sub') === sub);
  });
  document.querySelectorAll('#panel-lists .settings-sub-section').forEach(s => s.classList.remove('active'));
  if (sub === 'watchlist') {
    const el = document.getElementById('listsSubWatchlist');
    if (el) el.classList.add('active');
    loadWatchlist();
  } else if (sub === 'watchhistory') {
    const el = document.getElementById('listsSubWatchHistory');
    if (el) el.classList.add('active');
    loadWatchHistory();
  }
}

async function loadWatchlist() {
  const grid  = document.getElementById('watchlistGrid');
  const empty = document.getElementById('watchlistEmpty');
  if (!grid) return;
  grid.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem; padding:20px;">${t('general.loading')}</div>`;
  try {
    const r = await fetch(`${API}/watchlist`, { headers: authHeaders() });
    if (!r.ok) { grid.innerHTML = ''; return; }
    const movies = await r.json();
    if (!movies.length) {
      grid.innerHTML = '';
      if (empty) empty.style.display = '';
      return;
    }
    if (empty) empty.style.display = 'none';
    grid.innerHTML = movies.map(m => {
      const src = posterSrc(m);
      const imgHtml = src
        ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
        : '<div class="no-img">🎬</div>';
      const watched = m.last_watched ? `<div class="watched-check" title="${t('js.watchedOnDate', m.last_watched)}">✓</div>` : '';
      return `
      <div class="movie-card" data-id="${m.id}" onclick="openMovieDetail(${m.id})" style="position:relative;">
        ${watched}
        <div class="movie-card-poster">${imgHtml}
          <div class="movie-card-format">${m.format || '4K'}</div>
        </div>
        <div class="movie-card-info">
          <div class="movie-card-title">${m.title}</div>
          <div class="movie-card-year">${m.year || '—'}</div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    grid.innerHTML = '';
  }
}

async function loadWatchHistory() {
  const list  = document.getElementById('watchHistoryList');
  const empty = document.getElementById('watchHistoryEmpty');
  if (!list) return;
  list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem; padding:20px;">${t('general.loading')}</div>`;
  try {
    const r = await fetch(`${API}/watch-history`, { headers: authHeaders() });
    if (!r.ok) { list.innerHTML = ''; return; }
    const entries = await r.json();
    if (!entries.length) {
      list.innerHTML = '';
      if (empty) empty.style.display = '';
      return;
    }
    if (empty) empty.style.display = 'none';
    // Group by date
    const byDate = {};
    entries.forEach(e => {
      const d = e.watched_at.slice(0,10);
      if (!byDate[d]) byDate[d] = [];
      byDate[d].push(e);
    });
    list.innerHTML = Object.keys(byDate).map(date => {
      const label = _formatDate(date);
      const rows = byDate[date].map(e => {
        const src = posterSrc(e);
        const img = src
          ? `<img src="${src}" onerror="this.outerHTML='<div class=\\'no-img-sm\\'>🎬</div>'">`
          : '<div class="no-img-sm">🎬</div>';
        return `<div class="watch-history-row" onclick="openMovieDetail(${e.movie_id})">
          ${img}
          <div>
            <div class="whr-title">${e.title}</div>
            <div class="whr-meta">${e.year || '—'} · ${e.format || '—'}</div>
          </div>
        </div>`;
      }).join('');
      return `<div class="watch-history-day">
        <div class="watch-history-day-label">${label}</div>
        ${rows}
      </div>`;
    }).join('');
  } catch(e) {
    list.innerHTML = '';
  }
}

// Current movie watchlist/watched state (set when modal opens)
let _modalMovieOnWatchlist = false;
let _modalMovieLastWatched = null;

async function toggleWatchlistModal() {
  if (!currentMovieId) return;
  const btn = document.getElementById('btnWatchlistModal');
  if (_modalMovieOnWatchlist) {
    await fetch(`${API}/watchlist/${currentMovieId}`, { method: 'DELETE', headers: authHeaders() });
    _modalMovieOnWatchlist = false;
  } else {
    await fetch(`${API}/watchlist/${currentMovieId}`, { method: 'POST', headers: authHeaders() });
    _modalMovieOnWatchlist = true;
  }
  _updateWatchlistBtn();
  // Update allMovies cache
  const m = allMovies.find(x => x.id === currentMovieId);
  if (m) m.on_watchlist = _modalMovieOnWatchlist;
}

function _updateWatchlistBtn() {
  const btn = document.getElementById('btnWatchlistModal');
  if (!btn) return;
  btn.textContent = _modalMovieOnWatchlist ? t('modal.inWatchlist') : t('modal.watchlist');
  btn.style.color = _modalMovieOnWatchlist ? 'var(--accent)' : '';
  btn.style.borderColor = _modalMovieOnWatchlist ? 'rgba(232,197,71,.5)' : '';
}

function _updateWatchedBtn() {
  const btn = document.getElementById('btnWatchedModal');
  if (!btn) return;
  if (_modalMovieLastWatched) {
    btn.textContent = t('modal.watchedOn', _modalMovieLastWatched.slice(0,10));
    btn.style.color = '#4caf50';
    btn.style.borderColor = 'rgba(76,175,80,.4)';
  } else {
    btn.textContent = t('modal.watched');
    btn.style.color = '';
    btn.style.borderColor = '';
  }
}

function toggleWatchedMenu(e) {
  e.stopPropagation();
  const popup = document.getElementById('watchedPopup');
  if (!popup) return;
  const isVisible = popup.style.display !== 'none';
  if (isVisible) {
    popup.style.display = 'none';
    return;
  }
  popup.style.display = 'block';
  // Set today as default
  const inp = document.getElementById('watchedDateInput');
  if (inp) inp.value = new Date().toISOString().slice(0,10);
  // Load existing history entries
  if (currentMovieId) _refreshWatchedHistoryPopup(currentMovieId);
  // Close on click OUTSIDE the popup
  setTimeout(() => {
    const closeHandler = (ev) => {
      if (!popup.contains(ev.target)) {
        popup.style.display = 'none';
        document.removeEventListener('click', closeHandler);
      }
    };
    document.addEventListener('click', closeHandler);
  }, 0);
}

async function _refreshWatchedHistoryPopup(movieId) {
  const section = document.getElementById('watchedHistorySection');
  const container = document.getElementById('watchedHistoryEntries');
  if (!section || !container) return [];
  const r = await fetch(`${API}/watched/${movieId}`, { headers: authHeaders() });
  if (!r.ok) { section.style.display = 'none'; return []; }
  const entries = await r.json();
  if (!entries.length) { section.style.display = 'none'; return []; }
  section.style.display = '';
  container.innerHTML = entries.map(e => {
    const d = e.watched_at.slice(0,10);
    const label = _formatDate(d);
    return `<div class="watched-history-entry">
      <span>${label}</span>
      <button class="del-btn" onclick="_deleteWatchedEntry(${e.id}, ${movieId})" title="Verwijderen">\u2715</button>
    </div>`;
  }).join('');
  return entries;
}

async function _deleteWatchedEntry(entryId, movieId) {
  await fetch(`${API}/watched/entry/${entryId}`, { method: 'DELETE', headers: authHeaders() });
  await _refreshWatchedHistoryPopup(movieId);
  // Refresh the movie's last_watched in allMovies
  const r2 = await fetch(`${API}/watched/${movieId}`, { headers: authHeaders() });
  const remaining = r2.ok ? await r2.json() : [];
  const newLastWatched = remaining.length ? remaining[0].watched_at.slice(0,10) : null;
  _modalMovieLastWatched = newLastWatched;
  _updateWatchedBtn();
  const m = allMovies.find(x => x.id === movieId);
  if (m) m.last_watched = newLastWatched;
}

function _formatDate(dateStr) {
  if (!dateStr) return '';
  const months = t('months.short').split(',');
  const [y, mo, d] = dateStr.split('-');
  return `${parseInt(d)} ${months[parseInt(mo)-1]} ${y}`;
}

async function markWatchedModal(type) {
  if (!currentMovieId) return;
  let date;
  const now = new Date();
  if (type === 'today') {
    date = now.toISOString().slice(0,10);
  } else if (type === 'yesterday') {
    const y = new Date(now); y.setDate(y.getDate() - 1);
    date = y.toISOString().slice(0,10);
  } else {
    date = document.getElementById('watchedDateInput').value;
    if (!date) return;
  }
  // Close popup immediately
  document.getElementById('watchedPopup').style.display = 'none';
  const r = await fetch(`${API}/watched/${currentMovieId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ watched_at: date })
  });
  if (!r.ok) return;
  // Re-fetch history to get accurate most-recent date and update history section
  const entries = await _refreshWatchedHistoryPopup(currentMovieId);
  _modalMovieLastWatched = entries.length ? entries[0].watched_at.slice(0,10) : date;
  _updateWatchedBtn();
  const m = allMovies.find(x => x.id === currentMovieId);
  if (m) m.last_watched = _modalMovieLastWatched;
  // Refresh Watch History page if currently open
  if (document.getElementById('listsSubWatchHistory')?.classList.contains('active')) {
    loadWatchHistory();
  }
}

async function unmarkWatchedModal() {
  // Kept for backwards compat but no longer called from UI
  if (!currentMovieId) return;
  document.getElementById('watchedPopup').style.display = 'none';
  await fetch(`${API}/watched/entry`, { method: 'DELETE' });
}

async function bulkAddToWatchlist() {
  if (!selectedIds.size) return;
  const ids = Array.from(selectedIds);
  await fetch(`${API}/watchlist/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ movie_ids: ids })
  });
  // Update local cache
  ids.forEach(id => {
    const m = allMovies.find(x => x.id === id);
    if (m) m.on_watchlist = true;
  });
  showStatus('bulkGroupStatus', t('js.bulkWatchlistAdded', ids.length), 'success');
  setTimeout(() => toggleSelectMode(), 1200);
}

// ── Backup / Restore / Reset ──────────────────────────────────────────────────

async function loadSettings() {
  loadDbStats();
  loadBackups();
  loadAuthSettings();
  loadQueueSettings();
  loadSourceSettings();

  // Check admin status and show/hide restricted tabs
  try {
    const mr = await fetch(`${API}/auth/me`);
    const me = await mr.json();
    const isSettingsAdmin = !me.authenticated || me.role === 'admin';
    const logsBtn   = document.querySelector('[data-settings-sub="logs"]');
    const advBtn    = document.querySelector('[data-settings-sub="advanced"]');
    const backupBtn = document.querySelector('[data-settings-sub="backup"]');
    if (logsBtn)   logsBtn.style.display   = isSettingsAdmin ? '' : 'none';
    if (advBtn)    advBtn.style.display    = isSettingsAdmin ? '' : 'none';
    if (backupBtn) backupBtn.style.display = isSettingsAdmin ? '' : 'none';
    if (isSettingsAdmin) {
      loadDebugSettings();
      loadMcpSettings();
    }
    // If non-admin is on a restricted tab, redirect to general
    if (!isSettingsAdmin && (currentSettingsSubmenu === 'logs' || currentSettingsSubmenu === 'advanced' || currentSettingsSubmenu === 'backup')) {
      currentSettingsSubmenu = 'general';
    }
  } catch(e) {
    loadDebugSettings();
    loadMcpSettings();
  }
  switchSettingsSubmenu(currentSettingsSubmenu);
}

let currentSettingsSubmenu = 'profile';

function switchSettingsSubmenu(name) {
  currentSettingsSubmenu = name;
  document.querySelectorAll('[data-settings-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-settings-sub') === name);
  });
  const map = {
    profile: 'settingsSubProfile',
    general: 'settingsSubGeneral',
    security: 'settingsSubSecurity',
    backup: 'settingsSubBackup',
    logs: 'settingsSubLogs',
    advanced: 'settingsSubAdvanced',
    notifications: 'settingsSubNotifications',
  };
  Object.values(map).forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
  });
  const target = document.getElementById(map[name] || map.profile);
  if (target) target.classList.add('active');
  if (name === 'profile') { loadProfile(); loadApiKeys(); loadMcpLogs(); }
  if (name === 'logs') loadLogs();
  if (name === 'notifications') initPushNotifications();
}

async function loadSourceSettings() {
  try {
    const r = await fetch(`${API}/settings/sources`);
    const d = await r.json();
    const elOmdb = document.getElementById('sourceOmdbToggle');
    const elTmdb = document.getElementById('sourceTmdbToggle');
    const el = document.getElementById('sourceBlurayToggle');
    const el2 = document.getElementById('sourceBlurayDiscDeToggle');
    if (elOmdb) elOmdb.checked = !!d.omdb_enabled;
    if (elTmdb) elTmdb.checked = !!d.tmdb_enabled;
    if (el) el.checked = !!d.bluray_scrape_enabled;
    if (el2) el2.checked = !!d.bluraydiscde_scrape_enabled;
  } catch(e) {}
}

async function saveSourceSettings() {
  const elOmdb = document.getElementById('sourceOmdbToggle');
  const elTmdb = document.getElementById('sourceTmdbToggle');
  const el = document.getElementById('sourceBlurayToggle');
  const el2 = document.getElementById('sourceBlurayDiscDeToggle');
  const body = {
    omdb_enabled: !!(elOmdb && elOmdb.checked),
    tmdb_enabled: !!(elTmdb && elTmdb.checked),
    bluray_scrape_enabled: !!(el && el.checked),
    bluraydiscde_scrape_enabled: !!(el2 && el2.checked),
  };
  try {
    const r = await fetch(`${API}/settings/sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) {
      showStatus('sourceSettingsStatus', d.error || t('js.saveFailed'), 'error');
      return;
    }
    showStatus('sourceSettingsStatus', t('js.sourceSettingsSaved'), 'success');
  } catch(e) {
    showStatus('sourceSettingsStatus', t('js.error', e.message), 'error');
  }
}

function queueActionLabel(item) {
  if (!item || !item.method || !item.url) return t('js.unknownAction');
  const u = item.url;
  const m = item.method;
  if (m === 'POST' && u === '/api/movies') return t('js.actionAdd');
  if (m === 'PUT' && u.startsWith('/api/movies/')) return t('js.actionUpdate');
  if (m === 'DELETE' && u.startsWith('/api/movies/')) return t('js.actionDelete');
  if (m === 'POST' && u === '/api/movies/bulk-delete') return t('js.actionBulkDelete');
  return `${m} ${u}`;
}

function loadQueueSettings() {
  const list = document.getElementById('queueList');
  if (!list) return;
  const queue = readWriteQueue();

  if (!queue.length) {
    list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noQueuedActions')}</div>`;
    return;
  }

  list.innerHTML = queue.map(item => {
    const ts = new Date(item.created_at || Date.now()).toLocaleString();
    const label = queueActionLabel(item);
    return `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px; flex-wrap:wrap;">
        <div style="font-size:1.05rem;">⏳</div>
        <div style="flex:1; min-width:200px;">
          <div style="font-weight:500; font-size:0.85rem;">${label}</div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">${ts}</div>
        </div>
        <button class="btn btn-danger" style="padding:6px 10px; font-size:0.75rem;" onclick="removeQueuedMutation('${item.id}')">✕</button>
      </div>
    `;
  }).join('');
}

function removeQueuedMutation(id) {
  const queue = readWriteQueue().filter(item => item.id !== id);
  writeWriteQueue(queue);
  updateQueueIndicator();
  loadQueueSettings();
}

function clearQueuedMutations() {
  if (!confirm(t('js.confirmClearQueue'))) return;
  writeWriteQueue([]);
  updateQueueIndicator();
  loadQueueSettings();
  showStatus('queueStatus', t('js.queueCleared'), 'success');
}

async function syncQueueNow() {
  if (!navigator.onLine) {
    showStatus('queueStatus', t('js.offlineSyncError'), 'error');
    return;
  }
  showStatus('queueStatus', t('js.synchronizing'), 'info');
  const before = readWriteQueue().length;
  await flushQueuedMutations();
  const after = readWriteQueue().length;
  if (after === 0) {
    showStatus('queueStatus', t('js.syncComplete', before), 'success');
  } else if (after < before) {
    showStatus('queueStatus', t('js.syncPartial', before - after, after), 'info');
  } else {
    showStatus('queueStatus', t('js.syncFailed'), 'error');
  }
  loadQueueSettings();
}

async function loadDbStats() {
  try {
    const r = await fetch(`${API}/settings/db-stats`);
    const d = await r.json();
    const el = document.getElementById('dbStatsContent');
    el.innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px;">
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--accent);">${d.movie_count}</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.movies')}</div>
        </div>
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--accent);">${d.poster_count}</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.posters')}</div>
        </div>
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--text-muted);">${(d.db_size / 1024).toFixed(0)} KB</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.database')}</div>
        </div>
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--text-muted);">${(d.poster_size / 1024 / 1024).toFixed(1)} MB</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.postersSize')}</div>
        </div>
      </div>`;
  } catch(e) {}
}

async function createBackup() {
  showStatus('settingsBackupStatus', t('js.creatingBackup'), 'info');
  try {
    const r = await fetch(`${API}/settings/backup`, { method: 'POST', headers: authHeaders() });
    const d = await r.json();
    if (d.error) { showStatus('settingsBackupStatus', d.error, 'error'); return; }
    showStatus('settingsBackupStatus', t('js.backupCreated', d.name, (d.size/1024).toFixed(0)), 'success');
    loadBackups();
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

async function loadBackups() {
  try {
    const r = await fetch(`${API}/settings/backups`, { headers: authHeaders() });
    const backups = await r.json();
    const el = document.getElementById('backupsList');
    if (!backups.length) {
      el.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noBackups')}</div>`;
      return;
    }
    el.innerHTML = backups.map(b => `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px; flex-wrap:wrap;">
        <div style="font-size:1.2rem;">📦</div>
        <div style="flex:1; min-width:180px;">
          <div style="font-weight:500; font-size:0.85rem;">${b.name}</div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
            ${(b.size/1024).toFixed(0)} KB · ${b.poster_count} posters${b.movie_count ? ' · ' + b.movie_count + ' films' : ''}${b.format === 'v1' ? ' · legacy' : ''}
          </div>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-success" style="padding:6px 10px; font-size:0.75rem;" onclick="restoreBackup('${b.name}')">${t('js.restore')}</button>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.75rem;" onclick="downloadBackup('${b.name}')">${t('js.download')}</button>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.75rem;" onclick="deleteBackup('${b.name}')">✕</button>
        </div>
      </div>
    `).join('');
  } catch(e) {}
}

async function downloadBackup(name) {
  try {
    const r = await fetch(`${API}/settings/backup/${encodeURIComponent(name)}/download`, { headers: authHeaders() });
    if (!r.ok) { showStatus('settingsBackupStatus', t('js.downloadFailed'), 'error'); return; }
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name + '.tar.gz';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

async function uploadBackup(input) {
  const file = input.files[0];
  input.value = '';
  if (!file) return;
  if (!file.name.match(/\.(tar\.gz|tgz)$/i)) {
    showStatus('settingsBackupStatus', t('js.tarGzOnly'), 'error');
    return;
  }
  showStatus('settingsBackupStatus', t('js.uploading'), 'info');
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`${API}/settings/backup/upload`, {
      method: 'POST',
      headers: authHeaders(),
      body: fd
    });
    const d = await r.json();
    if (!r.ok || d.error) { showStatus('settingsBackupStatus', d.error || t('js.uploadFailed'), 'error'); return; }
    showStatus('settingsBackupStatus', t('js.uploadDone'), 'success');
    loadBackups();
    // Auto-trigger restore for the uploaded backup
    restoreBackup(d.name);
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

async function restoreBackup(name, groupMapping) {
  if (!groupMapping && !confirm(t('js.confirmRestore', name))) return;
  showStatus('settingsBackupStatus', t('js.restoring'), 'info');
  try {
    const body = groupMapping ? { group_mapping: groupMapping } : {};
    const r = await fetch(`${API}/settings/restore/${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    });
    const d = await r.json();

    // Group conflict: show resolution dialog
    if (r.status === 409 && d.status === 'groups_conflict') {
      showGroupConflictDialog(name, d);
      return;
    }
    if (d.error) { showStatus('settingsBackupStatus', d.error, 'error'); return; }
    showStatus('settingsBackupStatus', t('js.restored', name), 'success');
    loadDbStats(); loadCollection(); loadStats(); loadBackups();
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

function showGroupConflictDialog(backupName, conflictData) {
  const missing = conflictData.missing_groups;
  const existing = conflictData.existing_groups;

  let html = `<div style="position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;" id="groupConflictOverlay">
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:480px;width:90%;max-height:80vh;overflow-y:auto;">
      <div style="font-family:'DM Serif Display',serif;font-size:1.1rem;margin-bottom:16px;">${t('js.groupConflictTitle')}</div>
      <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:16px;">${t('js.groupConflictDesc', conflictData.movie_count)}</div>`;

  missing.forEach((g, i) => {
    html += `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px;">
      <div style="font-weight:500;font-size:0.88rem;margin-bottom:8px;">📁 ${g}</div>
      <select id="groupAction_${i}" onchange="toggleGroupAssign(${i})" style="width:100%;padding:6px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-size:0.82rem;">
        <option value="create">${t('js.groupActionCreate')}</option>
        <option value="skip">${t('js.groupActionSkip')}</option>
        ${existing.map(eg => `<option value="assign_${eg.id}">${t('js.groupActionAssign', eg.name)}</option>`).join('')}
      </select>
    </div>`;
  });

  html += `<div style="display:flex;gap:10px;margin-top:16px;">
      <button class="btn btn-primary" onclick="applyGroupConflict('${backupName}')">${t('js.groupConflictApply')}</button>
      <button class="btn btn-secondary" onclick="document.getElementById('groupConflictOverlay').remove()">${t('js.cancel')}</button>
    </div>
    </div></div>`;

  // Store missing group names for the apply function
  window._groupConflictMissing = missing;
  document.body.insertAdjacentHTML('beforeend', html);
}

function applyGroupConflict(backupName) {
  const missing = window._groupConflictMissing || [];
  const mapping = {};
  missing.forEach((g, i) => {
    const sel = document.getElementById(`groupAction_${i}`);
    const val = sel ? sel.value : 'skip';
    if (val === 'create') {
      mapping[g] = { action: 'create' };
    } else if (val === 'skip') {
      mapping[g] = { action: 'skip' };
    } else if (val.startsWith('assign_')) {
      mapping[g] = { action: 'assign', group_id: parseInt(val.split('_')[1]) };
    }
  });
  document.getElementById('groupConflictOverlay').remove();
  restoreBackup(backupName, mapping);
}

async function deleteBackup(name) {
  if (!confirm(t('js.confirmDeleteBackup', name))) return;
  await fetch(`${API}/settings/backup/${name}`, { method: 'DELETE', headers: authHeaders() });
  loadBackups();
}

async function resetDatabase() {
  if (!confirm(t('js.confirmReset1'))) return;
  if (!confirm(t('js.confirmReset2'))) return;
  try {
    const r = await fetch(`${API}/settings/reset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: 'RESET' })
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    alert(t('js.resetComplete'));
    loadDbStats();
    loadCollection();
    loadStats();
  } catch(e) {
    alert(t('js.error', e.message));
  }
}

// ── Language & MCP Settings ───────────────────────────────────────────────────
function changeLanguage(lang) {
  setLanguage(lang);
  _updateWatchedBtn();
  _updateWatchlistBtn();
  loadCollection();
  loadStats();
  if (document.getElementById('panel-settings').classList.contains('active')) loadSettings();
  if (document.getElementById('panel-lists').classList.contains('active')) {
    const activeListsSub = document.querySelector('[data-lists-sub].active');
    if (activeListsSub) switchListsSubmenu(activeListsSub.getAttribute('data-lists-sub'));
  }
}

async function saveMcpSettings() {
  const enabled = document.getElementById('mcpEnabledToggle').checked;
  try {
    const r = await fetch(`${API}/settings/mcp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ mcp_enabled: enabled })
    });
    await r.json();
    showStatus('mcpSettingsStatus', t('js.mcpSettingsSaved'), 'success');
  } catch(e) {
    showStatus('mcpSettingsStatus', t('js.error', e.message), 'error');
  }
}

async function saveDebugSettings() {
  const el = document.getElementById('debugEnabledToggle');
  const enabled = !!(el && el.checked);
  try {
    const r = await fetch(`${API}/settings/debug`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ debug_enabled: enabled })
    });
    await r.json();
    debugModeEnabled = enabled;
    applyDebugVisibility();
    showStatus('mcpSettingsStatus', t('js.advancedSettingsSaved'), 'success');
  } catch(e) {
    showStatus('mcpSettingsStatus', t('js.error', e.message), 'error');
  }
}

async function loadDebugSettings() {
  try {
    const r = await fetch(`${API}/settings/debug`, { headers: authHeaders() });
    const d = await r.json();
    debugModeEnabled = d.debug_enabled === true;
    const el = document.getElementById('debugEnabledToggle');
    if (el) el.checked = debugModeEnabled;
    applyDebugVisibility();
  } catch(e) {
    debugModeEnabled = false;
    const el = document.getElementById('debugEnabledToggle');
    if (el) el.checked = false;
    applyDebugVisibility();
  }
}

async function loadMcpSettings() {
  try {
    const r = await fetch(`${API}/settings/mcp`, { headers: authHeaders() });
    const d = await r.json();
    document.getElementById('mcpEnabledToggle').checked = d.mcp_enabled !== false;
  } catch(e) {}
}

init();

// ── Push notifications ────────────────────────────────────────────────────────
let _pushSubscription = null;

function _urlB64ToUint8Array(b64) {
  const pad = '='.repeat((4 - b64.length % 4) % 4);
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function initPushNotifications() {
  const statusEl  = document.getElementById('pushNotifStatus');
  const subBtn    = document.getElementById('pushSubscribeBtn');
  const unsubBtn  = document.getElementById('pushUnsubscribeBtn');
  const testBtn   = document.getElementById('pushTestBtn');
  const prefsCard = document.getElementById('notifPrefsCard');
  if (!statusEl) return;

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    statusEl.innerHTML = `<span style="color:var(--text-muted);">${t('settings.pushUnsupported')}</span>`;
    if (subBtn) subBtn.disabled = true;
    return;
  }
  const perm = Notification.permission;
  const reg  = await navigator.serviceWorker.ready;
  _pushSubscription = await reg.pushManager.getSubscription();

  if (_pushSubscription) {
    statusEl.innerHTML = `<span style="color:var(--success);">✓ ${t('settings.pushActive')}</span>`;
    if (subBtn)    subBtn.style.display    = 'none';
    if (unsubBtn)  unsubBtn.style.display  = '';
    if (testBtn)   testBtn.style.display   = '';
    if (prefsCard) { prefsCard.style.display = ''; loadNotifPrefs(); }
  } else if (perm === 'denied') {
    statusEl.innerHTML = `<span style="color:var(--danger);">${t('settings.pushBlocked')}</span>`;
    if (subBtn)    subBtn.disabled = true;
    if (prefsCard) prefsCard.style.display = 'none';
  } else {
    statusEl.innerHTML = `<span style="color:var(--text-muted);">${t('settings.pushInactive')}</span>`;
    if (subBtn)    { subBtn.style.display = ''; subBtn.disabled = false; }
    if (unsubBtn)  unsubBtn.style.display = 'none';
    if (testBtn)   testBtn.style.display  = 'none';
    if (prefsCard) prefsCard.style.display = 'none';
  }
}

async function subscribeToPush() {
  const statusEl = document.getElementById('pushNotifStatus');
  try {
    const r = await fetch(`${API}/push/vapid-public-key`);
    const { publicKey } = await r.json();
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlB64ToUint8Array(publicKey),
    });
    await fetch(`${API}/push/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    });
    _pushSubscription = sub;
    await initPushNotifications();
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">${t('js.error', e.message)}</span>`;
  }
}

async function unsubscribeFromPush() {
  if (!_pushSubscription) return;
  const statusEl = document.getElementById('pushNotifStatus');
  try {
    const endpoint = _pushSubscription.endpoint;
    await _pushSubscription.unsubscribe();
    await fetch(`${API}/push/subscribe`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint }),
    });
    _pushSubscription = null;
    await initPushNotifications();
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">${t('js.error', e.message)}</span>`;
  }
}

async function testPushNotification() {
  const btn      = document.getElementById('pushTestBtn');
  const statusEl = document.getElementById('pushNotifStatus');
  if (btn) btn.disabled = true;
  try {
    const r    = await fetch(`${API}/push/test`, { method: 'POST' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) {
      const errors = data.errors || [data.error || r.statusText];
      const isStale = data.stale || errors.some(e => String(e).includes('410'));
      if (isStale) {
        // Subscription was revoked by the push service — clean up browser-side too.
        if (_pushSubscription) {
          try { await _pushSubscription.unsubscribe(); } catch(e) {}
          _pushSubscription = null;
        }
        await initPushNotifications();
        if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent);">⚠ Je push-abonnement is verlopen. Schakel meldingen opnieuw in.</span>`;
      } else {
        const msg = errors.join('; ');
        if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${msg}</span>`;
      }
    } else {
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--success);">✓ Verzonden — controleer je meldingen</span>`;
    }
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${e.message}</span>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadNotifPrefs() {
  try {
    const r = await fetch(`${API}/push/prefs`);
    if (!r.ok) return;
    const prefs = await r.json();
    const el = document.getElementById('prefGroupInvite');
    if (el && prefs.group_invite !== undefined) el.checked = prefs.group_invite;
  } catch(e) { /* silently ignore */ }
}

async function saveNotifPref(key, value) {
  try {
    await fetch(`${API}/push/prefs`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
  } catch(e) { /* silently ignore */ }
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  });
  // Handle messages from the service worker (e.g. notification clicks on an open app).
  navigator.serviceWorker.addEventListener('message', event => {
    if (event.data && event.data.type === 'open-invites') {
      openInvitePanel();
    }
  });
}

// ── PWA install prompt ────────────────────────────────────────────────────────
let _deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _deferredInstallPrompt = e;
  showPwaInstallBanner();
});

const _isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);

function showPwaInstallBanner() {
  // Don't show if already installed as PWA or dismissed recently
  if (window.matchMedia('(display-mode: standalone)').matches) return;
  if (navigator.standalone) return; // iOS
  if (localStorage.getItem('dv_pwa_dismissed')) return;
  const banner = document.getElementById('pwaInstallBanner');
  if (!banner) return;
  // On iOS: show instructions instead of install button
  const installBtn = document.getElementById('pwaInstallBtn');
  const iosHint = document.getElementById('pwaIosHint');
  if (_isIos && !_deferredInstallPrompt) {
    if (installBtn) installBtn.style.display = 'none';
    if (iosHint) iosHint.style.display = '';
  } else {
    if (installBtn) installBtn.style.display = '';
    if (iosHint) iosHint.style.display = 'none';
  }
  setTimeout(() => banner.classList.add('visible'), 600);
}

function pwaInstall() {
  if (_deferredInstallPrompt) {
    _deferredInstallPrompt.prompt();
    _deferredInstallPrompt.userChoice.then(r => {
      if (r.outcome === 'accepted') hidePwaInstallBanner();
      _deferredInstallPrompt = null;
    });
  }
}

function dismissPwaInstall() {
  localStorage.setItem('dv_pwa_dismissed', Date.now().toString());
  hidePwaInstallBanner();
}

function hidePwaInstallBanner() {
  const banner = document.getElementById('pwaInstallBanner');
  if (banner) banner.classList.remove('visible');
}

// For iOS/Safari (no beforeinstallprompt) – show after a delay
window.addEventListener('load', () => {
  if (!_deferredInstallPrompt && !window.matchMedia('(display-mode: standalone)').matches && !navigator.standalone) {
    const isSafari = /safari/i.test(navigator.userAgent) && !/chrome|crios|fxios/i.test(navigator.userAgent);
    if (_isIos || isSafari) {
      setTimeout(() => showPwaInstallBanner(), 2000);
    }
  }
});
