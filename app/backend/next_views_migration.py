"""Migration dashboard renderer (extracted from next_app).

Move-only extraction: function bodies are unchanged. Shared helpers are
imported back from next_app. next_app re-imports these renderers near the end
of its module body (after every helper is defined), so there is no
circular-import problem at load time.
"""
from __future__ import annotations

try:  # package import (Flask app factory)
    from .next_app import (
        pwa_head_tags,
    )
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_app import (
        pwa_head_tags,
    )


def migration_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover, interactive-widget=resizes-content">
  <title>DiscVault Data Migration</title>
""" + pwa_head_tags() + """
  <style>
    :root {
      color-scheme: dark;
      --bg: #090F1A;
      --panel: #111A2A;
      --panel-2: #1A2438;
      --line: #2B3852;
      --text: #EAF0F7;
      --muted: #9AA7B8;
      --accent: #3C7CE1;
      --blue: #6CB4FC;
      --green: #34D399;
      --red: #FF5A52;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body.auth-mode {
      min-height: 100vh;
    }
    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }
    body.auth-mode main {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px 0;
    }
    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 24px;
    }
    h1, h2, p { margin: 0; }
    h1 { font-size: clamp(1.7rem, 3vw, 2.5rem); font-weight: 760; letter-spacing: 0; }
    h2 { font-size: 1rem; margin-bottom: 12px; }
    p { color: var(--muted); line-height: 1.55; }
    button, a.button, select {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      color: var(--text);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 0 14px;
      text-decoration: none;
      white-space: nowrap;
    }
    select {
      padding: 0 10px;
      min-width: 132px;
    }
    .language-control {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      color: var(--muted);
      font-size: .82rem;
      white-space: nowrap;
    }
    .auth-gate {
      display: grid;
      gap: 20px;
      width: min(660px, calc(100vw - 32px));
      max-width: 660px;
      margin: 72px auto 18px;
      padding: 30px;
      box-sizing: border-box;
      background:
        linear-gradient(180deg, rgba(255,255,255,.075), rgba(255,255,255,.025)),
        var(--panel);
      border-color: rgba(255,255,255,.14);
      box-shadow: 0 24px 70px rgba(0,0,0,.28);
    }
    body.auth-mode .auth-gate {
      margin: 0 auto;
    }
    .auth-gate-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }
    .login-brand {
      display: grid;
      grid-template-columns: auto 1fr;
      align-items: center;
      gap: 14px;
      min-width: 0;
      flex: 1 1 auto;
    }
    .login-mark {
      width: 54px;
      height: 54px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--text);
      font-weight: 780;
      letter-spacing: 0;
    }
    .login-mark img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      filter: drop-shadow(0 1px 3px rgba(0,0,0,.55));
    }
    .login-copy {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .auth-gate h2 {
      margin: 0;
      font-size: clamp(2rem, 6vw, 3rem);
      font-weight: 780;
      letter-spacing: 0;
    }
    .auth-gate p {
      max-width: 70ch;
      font-size: .98rem;
    }
    .auth-gate .language-control {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255,255,255,.035);
      padding: 6px 8px 6px 12px;
      font-size: .78rem;
      flex: 0 1 auto;
      max-width: 100%;
    }
    .auth-gate .language-control select {
      flex: 1 1 148px;
      border-radius: 999px;
      min-width: 0;
      width: clamp(148px, 18vw, 230px);
      max-width: 100%;
      min-height: 32px;
      background: var(--panel-2);
      color: var(--text);
    }
    .auth-gate .language-control select option {
      background: var(--panel-2);
      color: var(--text);
    }
    .auth-message {
      color: var(--muted);
      min-height: 20px;
    }
    .auth-message.bad {
      color: var(--red);
    }
    .auth-message.good {
      color: var(--green);
    }
    .auth-secondary {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      padding-top: 2px;
    }
    .auth-gate button {
      white-space: normal;
      text-align: center;
    }
    .auth-secondary button {
      flex: 1 1 180px;
      min-width: 0;
      min-height: 34px;
      border-radius: 999px;
      background: rgba(255,255,255,.035);
      color: var(--muted);
      font-size: .82rem;
      padding-top: 8px;
      padding-bottom: 8px;
    }
    button.primary,
    a.button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #111217;
      font-weight: 700;
    }
    .completion-open-button {
      min-height: 46px;
      padding: 0 20px;
      box-shadow: 0 14px 34px rgba(232,197,71,.18);
    }
    button.danger {
      background: rgba(255,107,107,.08);
      border-color: rgba(255,107,107,.45);
      color: var(--red);
    }
    button.danger:hover {
      background: rgba(255,107,107,.14);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: .48;
    }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
    .hidden { display: none !important; }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 14px;
    }
    .card {
      grid-column: span 4;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      min-width: 0;
    }
    .wide { grid-column: span 8; }
    .full { grid-column: 1 / -1; }
    .wizard-card {
      display: grid;
      gap: 16px;
    }
    .migration-intro {
      display: grid;
      gap: 16px;
      margin-bottom: 14px;
    }
    .migration-intro h2 {
      margin: 0;
      font-size: 1.35rem;
    }
    .migration-intro p {
      max-width: 78ch;
      color: var(--muted);
      line-height: 1.58;
      margin: 0;
    }
    .migration-intro-list {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .migration-intro-list div {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.025);
      padding: 13px;
      min-width: 0;
    }
    .migration-intro-list strong {
      display: block;
      margin-bottom: 5px;
    }
    .migration-intro-list span {
      color: var(--muted);
      font-size: .88rem;
      line-height: 1.45;
    }
    .wizard-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    .wizard-summary {
      color: var(--muted);
      line-height: 1.5;
      max-width: 760px;
    }
    .wizard-steps {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .wizard-step {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.025);
      padding: 13px;
      min-width: 0;
    }
    .wizard-step-marker {
      width: 25px;
      height: 25px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: .78rem;
      margin-bottom: 10px;
    }
    .wizard-step strong {
      display: block;
      margin-bottom: 4px;
    }
    .wizard-step span {
      color: var(--muted);
      font-size: .86rem;
      line-height: 1.45;
    }
    .wizard-step.done {
      border-color: rgba(72,199,142,.35);
      background: rgba(72,199,142,.06);
    }
    .wizard-step.done .wizard-step-marker {
      border-color: rgba(72,199,142,.65);
      color: var(--green);
    }
    .wizard-step.active {
      border-color: rgba(232,197,71,.65);
      background: rgba(232,197,71,.07);
    }
    .wizard-step.active .wizard-step-marker {
      border-color: rgba(232,197,71,.75);
      color: var(--accent);
    }
    .wizard-step.blocked {
      border-color: rgba(255,107,107,.55);
      background: rgba(255,107,107,.06);
    }
    .wizard-step.blocked .wizard-step-marker {
      border-color: rgba(255,107,107,.7);
      color: var(--red);
    }
    .flow-panel {
      display: grid;
      gap: 16px;
    }
    .wizard-panel-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    .wizard-panel-header p {
      max-width: 78ch;
      margin-top: 4px;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
    }
    .summary-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.025);
      padding: 13px;
      min-width: 0;
    }
    .summary-item strong {
      display: block;
      color: var(--text);
      font-size: 1.18rem;
      line-height: 1.1;
      margin-bottom: 5px;
    }
    .summary-item span {
      color: var(--muted);
      font-size: .84rem;
      overflow-wrap: anywhere;
    }
    .option-list {
      display: grid;
      gap: 10px;
    }
    .option-row {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.025);
      padding: 13px;
    }
    .option-row input {
      margin-top: 3px;
      accent-color: var(--accent);
    }
    .option-row strong {
      display: block;
      margin-bottom: 3px;
    }
    .option-row span {
      color: var(--muted);
      font-size: .86rem;
      line-height: 1.45;
    }
    .confirm-box {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      border: 1px solid rgba(232,197,71,.38);
      border-radius: 8px;
      background: rgba(232,197,71,.06);
      padding: 13px;
    }
    .confirm-box input {
      margin-top: 3px;
      accent-color: var(--accent);
    }
    .confirm-box strong {
      display: block;
      margin-bottom: 3px;
    }
    .confirm-box span {
      color: var(--muted);
      font-size: .86rem;
      line-height: 1.45;
    }
    .metric {
      color: var(--text);
      font-size: 2rem;
      font-weight: 780;
      line-height: 1.1;
    }
    .label {
      color: var(--muted);
      font-size: .82rem;
      margin-top: 6px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 4px 10px;
      font-size: .8rem;
      color: var(--muted);
      background: rgba(255,255,255,.03);
    }
    .badge.ok { color: var(--green); border-color: rgba(72,199,142,.45); }
    .badge.warn { color: var(--accent); border-color: rgba(232,197,71,.45); }
    .badge.error { color: var(--red); border-color: rgba(255,107,107,.45); }
    .list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .section-help {
      font-size: .9rem;
      margin-bottom: 12px;
    }
    .source-chooser {
      display: grid;
      gap: 10px;
    }
    .source-option {
      width: 100%;
      height: auto;
      min-height: 0;
      padding: 13px;
      display: grid;
      gap: 10px;
      justify-content: stretch;
      text-align: left;
      white-space: normal;
      background: rgba(255,255,255,.025);
    }
    .source-option.selected {
      border-color: rgba(232,197,71,.85);
      box-shadow: 0 0 0 1px rgba(232,197,71,.28);
    }
    .source-option-main {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
    }
    .source-option-title {
      display: grid;
      gap: 4px;
      min-width: 0;
    }
    .source-option-title strong {
      color: var(--text);
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .source-stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
      gap: 8px;
    }
    .source-stat {
      color: var(--muted);
      font-size: .82rem;
    }
    .source-stat strong {
      color: var(--text);
      display: block;
      font-size: 1.02rem;
    }
    .details-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    .technical-details {
      margin-top: 16px;
      border-top: 1px solid rgba(255,255,255,.08);
      padding-top: 12px;
    }
    .technical-grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 14px;
    }
    .technical-panel {
      grid-column: span 6;
      min-width: 0;
    }
    .technical-panel.full {
      grid-column: 1 / -1;
    }
    .technical-panel h3 {
      margin: 0 0 8px;
      font-size: .92rem;
    }
    .row {
      display: grid;
      grid-template-columns: minmax(120px, .8fr) minmax(0, 1.3fr);
      gap: 12px;
      padding: 9px 0;
      border-bottom: 1px solid rgba(255,255,255,.08);
      color: var(--muted);
      min-width: 0;
    }
    .row:last-child { border-bottom: 0; }
    .row strong {
      color: var(--text);
      font-weight: 620;
      overflow-wrap: anywhere;
    }
    .plugins {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
    }
    .plugin {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      padding: 12px;
    }
    .plugin strong { display: block; margin-bottom: 6px; }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: .82rem;
      overflow-wrap: anywhere;
    }
    #message {
      margin-top: 12px;
      min-height: 20px;
      color: var(--muted);
    }
    @media (max-width: 860px) {
      main { width: min(100vw - 20px, 720px); padding-top: 18px; }
      body.auth-mode main { width: min(100vw - 20px, 720px); padding: 16px 0; }
      header { flex-direction: column; }
      .actions { justify-content: flex-start; }
      .card, .wide { grid-column: 1 / -1; }
      .auth-gate { width: 100%; margin-top: 28px; padding: 22px; }
      .auth-gate-top { flex-direction: column; align-items: stretch; }
      .auth-gate .language-control {
        width: 100%;
        box-sizing: border-box;
        justify-content: space-between;
        white-space: normal;
      }
      .auth-gate .language-control select {
        flex: 1 1 120px;
        width: 100%;
      }
      .auth-gate .actions,
      .auth-secondary {
        width: 100%;
      }
      .auth-gate .actions button,
      .auth-secondary button {
        width: 100%;
      }
      .wizard-head, .wizard-panel-header, .details-header { flex-direction: column; }
      .migration-intro-list { grid-template-columns: 1fr; }
      .wizard-steps { grid-template-columns: 1fr; }
      .technical-panel { grid-column: 1 / -1; }
      .row { grid-template-columns: 1fr; gap: 4px; }
    }
  </style>
</head>
<body>
  <main>
    <header id="migrationHeader">
      <div>
        <h1 data-next-i18n="migration.title">DiscVault Data Migration</h1>
        <p data-next-i18n="migration.description">Review the detected legacy data and finish setup.</p>
      </div>
      <div class="actions">
        <label class="language-control">
          <span data-next-i18n="language.label">Language</span>
          <select id="nextLanguageSelect" aria-label="Language" data-next-i18n-aria="language.label" data-next-language-select></select>
        </label>
        <button type="button" onclick="loadReport()" data-next-i18n="common.refresh">Refresh</button>
        <button type="button" class="primary hidden" id="startButton" onclick="startMigration()" disabled data-next-i18n="migration.start">Start Migration</button>
        <a class="button" href="/api/next/migration/report" data-next-i18n="common.json">JSON</a>
      </div>
    </header>

    <section class="card full auth-gate hidden" id="migrationAuthGate" aria-live="polite">
      <div class="auth-gate-top">
        <div class="login-brand">
          <span class="login-mark" aria-hidden="true"><img src="/api/next/assets/logo.svg" alt=""></span>
          <div class="login-copy">
            <h2 data-next-i18n="auth.loginTitle">DiscVault</h2>
            <p data-next-i18n="auth.loginDescription">Log in with your Passkey</p>
          </div>
        </div>
        <label class="language-control">
          <span data-next-i18n="language.label">Language</span>
          <select id="migrationAuthLanguageSelect" aria-label="Language" data-next-i18n-aria="language.label" data-next-language-select></select>
        </label>
      </div>
      <div class="actions">
        <button type="button" class="primary" id="migrationLoginButton" data-next-i18n="auth.signIn">Sign in</button>
      </div>
      <div class="auth-secondary" aria-label="Additional access options">
        <button type="button" disabled data-next-i18n="auth.inviteOnly">Invite-only access</button>
        <button type="button" disabled data-next-i18n="auth.recovery">Recovery</button>
      </div>
      <div class="auth-message" id="migrationAuthMessage" data-next-i18n="auth.checking">Checking authentication status...</div>
    </section>

    <section class="card full migration-intro hidden" id="migrationIntro" aria-live="polite">
      <div>
        <span id="migrationIntroBadge" class="badge warn" data-next-i18n="migration.introBadge">Migration required</span>
      </div>
      <div>
        <h2 data-next-i18n="migration.introTitle">We will guide you through this migration</h2>
        <p data-next-i18n="migration.introLead">DiscVault Next uses an improved PostgreSQL database model. Before you can use the new version, your existing DiscVault data needs to be checked and imported carefully.</p>
      </div>
      <div class="migration-intro-list">
        <div>
          <strong data-next-i18n="migration.introStepReview">Review</strong>
          <span data-next-i18n="migration.introStepReviewText">We first inspect the legacy data source and show what can be imported.</span>
        </div>
        <div>
          <strong data-next-i18n="migration.introStepImport">Import</strong>
          <span data-next-i18n="migration.introStepImportText">Movies, collections, box-sets, users, groups and passkeys are moved into the new database.</span>
        </div>
        <div>
          <strong data-next-i18n="migration.introStepSafe">Safe start</strong>
          <span data-next-i18n="migration.introStepSafeText">Existing posters, backdrops and profile images stay on the filesystem and are referenced from their current location.</span>
        </div>
      </div>
      <p data-next-i18n="migration.introOnlyWhenNeeded">You will only see this page when this DiscVault environment still needs a migration.</p>
      <div class="actions">
        <button type="button" class="primary" id="migrationIntroContinueButton" data-next-i18n="migration.introContinue">Continue to migration wizard</button>
      </div>
    </section>

    <section class="grid hidden" id="migrationWorkspace" aria-live="polite">
      <div class="card full wizard-card">
        <div class="wizard-head">
          <div>
            <h2 data-next-i18n="migration.wizardTitle">Migration Wizard</h2>
            <p class="wizard-summary" id="wizardSummary" data-next-i18n="migration.loadingReport">Loading report...</p>
          </div>
          <span id="wizardModeBadge" class="badge" data-next-i18n="common.loading">Loading</span>
        </div>
        <div class="wizard-steps" id="wizardSteps"></div>
      </div>
      <div class="card full flow-panel" id="migrationFlowPanel">
        <div class="wizard-panel-header">
          <div>
            <h2 id="migrationFlowTitle" data-next-i18n="migration.flowTitle">Review migration</h2>
            <p id="migrationFlowDescription" data-next-i18n="migration.flowDescription">Check the detected data and confirm when you are ready to start.</p>
          </div>
          <span id="migrationFlowBadge" class="badge warn" data-next-i18n="common.loading">Loading</span>
        </div>
        <div class="summary-grid" id="migrationFlowSummary"></div>
        <div class="option-list" id="migrationRecoveryPanel"></div>
        <div class="option-list" id="migrationDryRunDiffPanel"></div>
        <div class="option-list" id="migrationFlowOptions"></div>
        <label class="confirm-box hidden" id="migrationConfirmBox">
          <input type="checkbox" id="migrationConfirmCheckbox">
          <span>
            <strong data-next-i18n="migration.confirmTitle">I am ready to start migration</strong>
            <span data-next-i18n="migration.confirmText">DiscVault Next will write the imported collection, users, groups and passkeys to PostgreSQL. Existing media files stay where they are.</span>
          </span>
        </label>
        <div class="actions">
          <button type="button" class="primary" id="migrationFlowStartButton" disabled data-next-i18n="migration.start">Start Migration</button>
          <a class="button primary completion-open-button hidden" id="migrationFlowAppButton" href="/">Open DiscVault 26</a>
          <button type="button" id="migrationFlowRefreshButton" data-next-i18n="common.refresh">Refresh</button>
          <button type="button" class="danger hidden" id="migrationTestResetButton" data-next-i18n="migration.testResetButton">Reset test database</button>
        </div>
      </div>
      <div class="card">
        <h2 data-next-i18n="migration.state">State</h2>
        <div id="stateBadge" class="badge" data-next-i18n="common.loading">Loading</div>
        <div id="message"></div>
      </div>
      <div class="card">
        <h2 data-next-i18n="collection.movies">Movies</h2>
        <div class="metric" id="moviesCount">-</div>
        <div class="label" data-next-i18n="migration.importedMovies">Imported movie records</div>
      </div>
      <div class="card">
        <h2 data-next-i18n="migration.people">People</h2>
        <div class="metric" id="peopleCount">-</div>
        <div class="label" data-next-i18n="migration.importedPeople">Imported people records</div>
      </div>
      <div class="card">
        <h2 data-next-i18n="migration.mediaAssets">Media Assets</h2>
        <div class="metric" id="mediaCount">-</div>
        <div class="label" data-next-i18n="migration.mediaAssetsHelp">References to existing filesystem media</div>
      </div>
      <div class="card">
        <h2 data-next-i18n="migration.containers">Containers</h2>
        <div class="metric" id="containerCount">-</div>
        <div class="label" data-next-i18n="migration.containersHelp">Box-sets and collections</div>
      </div>
      <div class="card">
        <h2 data-next-i18n="migration.plugins">Plugins</h2>
        <div class="metric" id="pluginCount">-</div>
        <div class="label" data-next-i18n="migration.pluginsHelp">Enabled metadata plugins</div>
      </div>
      <div class="card full">
        <h2 data-next-i18n="migration.importSource">Import Source</h2>
        <p class="section-help" data-next-i18n="migration.importSourceHelp">Choose the source that DiscVault Next should inspect and import from.</p>
        <div class="source-chooser" id="importSourceList"></div>
      </div>
      <div class="card full">
        <h2 data-next-i18n="migration.nextAction">Next Action</h2>
        <div class="list" id="actionsList"></div>
      </div>
      <div class="card full">
        <div class="details-header">
          <div>
            <h2 data-next-i18n="migration.technicalDetails">Technical Details</h2>
            <p class="section-help" data-next-i18n="migration.technicalDetailsHelp">Source paths, plugin readiness and raw migration counters.</p>
          </div>
          <button type="button" id="technicalDetailsButton" onclick="toggleTechnicalDetails()"></button>
        </div>
        <div class="technical-details hidden" id="technicalDetails">
          <div class="technical-grid">
            <section class="technical-panel">
              <h3 data-next-i18n="migration.source">Source</h3>
              <div class="list" id="sourceList"></div>
            </section>
            <section class="technical-panel">
              <h3 data-next-i18n="migration.skipped">Skipped</h3>
              <div class="list" id="skippedList"></div>
            </section>
            <section class="technical-panel full">
              <h3 data-next-i18n="migration.metadataPlugins">Metadata Plugins</h3>
              <div class="plugins" id="pluginsList"></div>
            </section>
            <section class="technical-panel full" id="warningsPanel" hidden>
              <h3 data-next-i18n="migration.warnings">Warnings</h3>
              <div class="list" id="warningsList"></div>
            </section>
          </div>
        </div>
      </div>
    </section>
  </main>

  <script>
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, function (char) {
        const escapes = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
        return escapes[char] || char;
      });
    }
    function formatNumber(value) {
      if (value === null || value === undefined || value === "") return "-";
      return Number(value).toLocaleString();
    }
    const NEXT_I18N_FALLBACK_LOCALES = [
      {locale: "nl-NL", legacy: "nl", nativeName: "Nederlands"},
      {locale: "en-US", legacy: "en", nativeName: "English"},
      {locale: "fr-FR", legacy: "fr", nativeName: "Francais"},
      {locale: "de-DE", legacy: "de", nativeName: "Deutsch"},
      {locale: "es-ES", legacy: "es", nativeName: "Espanol"},
      {locale: "pt-PT", legacy: "pt", nativeName: "Portugues"},
      {locale: "it-IT", legacy: "it", nativeName: "Italiano"},
      {locale: "sv-SE", legacy: "sv", nativeName: "Svenska"},
      {locale: "da-DK", legacy: "da", nativeName: "Dansk"},
      {locale: "nb-NO", legacy: "no", nativeName: "Norsk"},
      {locale: "fi-FI", legacy: "fi", nativeName: "Suomi"},
      {locale: "pl-PL", legacy: "pl", nativeName: "Polski"},
      {locale: "cs-CZ", legacy: "cs", nativeName: "Cestina"},
      {locale: "sk-SK", legacy: "sk", nativeName: "Slovencina"},
      {locale: "hu-HU", legacy: "hu", nativeName: "Magyar"},
      {locale: "ro-RO", legacy: "ro", nativeName: "Romana"},
      {locale: "bg-BG", legacy: "bg", nativeName: "Bulgarian"},
      {locale: "el-GR", legacy: "el", nativeName: "Greek"},
      {locale: "uk-UA", legacy: "uk", nativeName: "Ukrainian"},
      {locale: "hr-HR", legacy: "hr", nativeName: "Hrvatski"},
      {locale: "sl-SI", legacy: "sl", nativeName: "Slovenscina"},
      {locale: "et-EE", legacy: "et", nativeName: "Eesti"},
      {locale: "lv-LV", legacy: "lv", nativeName: "Latviesu"},
      {locale: "lt-LT", legacy: "lt", nativeName: "Lietuviu"},
      {locale: "tr-TR", legacy: "tr", nativeName: "Turkce"},
      {locale: "ja-JP", legacy: "ja", nativeName: "Japanese"},
      {locale: "zh-CN", legacy: "zh-CN", nativeName: "Chinese Simplified"},
      {locale: "zh-TW", legacy: "zh-TW", nativeName: "Chinese Traditional"},
      {locale: "ko-KR", legacy: "ko", nativeName: "Korean"}
    ];
    const nextI18n = {locale: "nl-NL", messages: {}, locales: NEXT_I18N_FALLBACK_LOCALES};
    let selectedImportSourceId = null;
    let lastMigrationReport = null;
    let showTechnicalDetails = false;
    let migrationIntroAccepted = false;
    let migrationConfirmed = false;
    let migrationLegacyAuthRequired = false;
    function normalizeNextLocale(value) {
      const raw = String(value || "").trim().replace("_", "-").toLowerCase();
      const aliases = {};
      nextI18n.locales.forEach((item) => {
        aliases[String(item.locale).toLowerCase()] = item.locale;
        aliases[String(item.legacy).toLowerCase()] = item.locale;
      });
      aliases.nb = "nb-NO";
      aliases["no-no"] = "nb-NO";
      aliases["en-gb"] = "en-US";
      aliases.zh = "zh-CN";
      aliases["zh-hans"] = "zh-CN";
      aliases["zh-hant"] = "zh-TW";
      if (raw && aliases[raw]) return aliases[raw];
      return aliases[raw.split("-", 1)[0]] || "nl-NL";
    }
    function legacyLocale(locale) {
      const found = nextI18n.locales.find((item) => item.locale === locale);
      return found ? found.legacy : String(locale || "nl-NL").split("-", 1)[0];
    }
    function preferredNextLocale() {
      if (localStorage.getItem("dv_next_locale")) return normalizeNextLocale(localStorage.getItem("dv_next_locale"));
      if (localStorage.getItem("dv_lang")) return normalizeNextLocale(localStorage.getItem("dv_lang"));
      const browserLocales = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
      for (const locale of browserLocales) return normalizeNextLocale(locale);
      return "nl-NL";
    }
    function tNext(key, fallback) {
      return nextI18n.messages[key] || fallback || key;
    }
    function migrationHeaders(extra) {
      const headers = {...(extra || {})};
      const token = localStorage.getItem("dv_next_token");
      if (token) headers.Authorization = `Bearer ${token}`;
      const legacyToken = sessionStorage.getItem("dv_next_legacy_migration_token");
      if (legacyToken) headers["X-DiscVault-Legacy-Migration-Token"] = legacyToken;
      return headers;
    }
    function setMigrationAuthMessage(message, tone) {
      const node = document.getElementById("migrationAuthMessage");
      if (!node) return;
      applyFaqMessage(node, message);
      node.className = `auth-message ${tone || ""}`.trim();
    }
    function showMigrationAuthGate(show) {
      const gate = document.getElementById("migrationAuthGate");
      const header = document.getElementById("migrationHeader");
      const intro = document.getElementById("migrationIntro");
      const workspace = document.getElementById("migrationWorkspace");
      document.body.classList.toggle("auth-mode", !!show);
      if (gate) gate.classList.toggle("hidden", !show);
      if (header) header.classList.toggle("hidden", !!show);
      if (intro && show) intro.classList.add("hidden");
      if (workspace && show) workspace.classList.add("hidden");
    }
    function isLikelyIpHost(hostname) {
      const host = String(hostname || "").trim().toLowerCase();
      if (!host || host === "localhost") return false;
      if (/^\\d{1,3}(?:\\.\\d{1,3}){3}$/.test(host)) return true;
      return host.includes(":");
    }
    function applyFaqMessage(node, message) {
      node.textContent = "";
      const text = String(message || "");
      const faqUrl = "https://discvault.eu/faq";
      let cursor = 0;
      let found = text.indexOf(faqUrl);
      if (found === -1) {
        node.textContent = text;
        return;
      }
      while (found !== -1) {
        if (found > cursor) node.appendChild(document.createTextNode(text.slice(cursor, found)));
        const anchor = document.createElement("a");
        anchor.href = faqUrl;
        anchor.textContent = faqUrl;
        anchor.target = "_blank";
        anchor.rel = "noreferrer";
        node.appendChild(anchor);
        cursor = found + faqUrl.length;
        found = text.indexOf(faqUrl, cursor);
      }
      if (cursor < text.length) node.appendChild(document.createTextNode(text.slice(cursor)));
    }
    function passkeyUpnSetupMessage() {
      return tNext(
        "auth.passkeyUpnRequired",
        "Passkeys cannot be created on an IP-only URL. Configure a UPN/hostname for DiscVault and open the app with that hostname over HTTPS. Help: https://discvault.eu/faq"
      );
    }
    function passkeyRpMismatchMessage() {
      return tNext(
        "auth.passkeyRpMismatch",
        "Passkeys are blocked because the configured Relying Party ID does not match the address you are using. Open DiscVault on the exact UPN/hostname that matches its passkey configuration (not an IP address), over HTTPS. Help: https://discvault.eu/faq"
      );
    }
    function passkeyClientErrorMessage(error, cancelledFallback, defaultFallback) {
      if (error && error.name === "NotAllowedError") return cancelledFallback;
      const rawMessage = String((error && error.message) || "");
      if (/registrable domain suffix|not equal to the current domain|relying party id/i.test(rawMessage)) {
        return passkeyRpMismatchMessage();
      }
      if ((error && error.name === "NotSupportedError") || /not supported/i.test(rawMessage)) {
        return webauthnUnavailableReason() || passkeyUpnSetupMessage();
      }
      return rawMessage || defaultFallback;
    }
    function webauthnUnavailableReason() {
      if (!window.PublicKeyCredential || !navigator.credentials) {
        return tNext("auth.passkeyUnavailable", "This browser does not support passkeys.");
      }
      if (isLikelyIpHost(window.location.hostname)) {
        return passkeyUpnSetupMessage();
      }
      if (!window.isSecureContext) {
        return tNext("auth.passkeyHttpsRequired", "Open this app over HTTPS to use passkeys.");
      }
      return "";
    }
    function base64urlToBuffer(value) {
      let normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
      while (normalized.length % 4) normalized += "=";
      const binary = atob(normalized);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return bytes.buffer;
    }
    function bufferToBase64url(buffer) {
      const bytes = new Uint8Array(buffer);
      let binary = "";
      bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
      return btoa(binary).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");
    }
    async function migrationJson(url, options) {
      const body = options && options.body;
      const headers = migrationHeaders((options && options.headers) || {});
      if (!(body instanceof FormData) && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        ...(options || {}),
        headers
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.error) {
        throw new Error(payload.error || `${url} failed with HTTP ${response.status}`);
      }
      return payload;
    }
    async function ensureMigrationAuth() {
      try {
        const response = await fetch("/api/next/auth/status", {
          cache: "no-store",
          credentials: "same-origin",
          headers: migrationHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        const needsAuth = !!payload.auth_enabled && !payload.authenticated;
        if (needsAuth) {
          showMigrationAuthGate(true);
          setMigrationAuthMessage(tNext("auth.loginDescription", "Log in with your Passkey"), "info");
          const loginButton = document.getElementById("migrationLoginButton");
          if (loginButton) loginButton.disabled = !!webauthnUnavailableReason();
          const unavailable = webauthnUnavailableReason();
          if (unavailable) setMigrationAuthMessage(unavailable, "bad");
          return false;
        }
        showMigrationAuthGate(false);
        return true;
      } catch (error) {
        showMigrationAuthGate(true);
        setMigrationAuthMessage(error.message, "bad");
        return false;
      }
    }
    async function loginMigrationPasskey() {
      const button = document.getElementById("migrationLoginButton");
      const unavailable = webauthnUnavailableReason();
      if (unavailable) {
        setMigrationAuthMessage(unavailable, "bad");
        return;
      }
      if (button) button.disabled = true;
      setMigrationAuthMessage(tNext("auth.checking", "Checking authentication status..."), "info");
      try {
        const optionsEndpoint = migrationLegacyAuthRequired
          ? "/api/next/migration/legacy-auth/options"
          : "/api/next/auth/login/options";
        const verifyEndpoint = migrationLegacyAuthRequired
          ? "/api/next/migration/legacy-auth/verify"
          : "/api/next/auth/login/verify";
        const optionsPayload = await migrationJson(optionsEndpoint, {
          method: "POST",
          body: "{}"
        });
        const options = optionsPayload.options || {};
        options.challenge = base64urlToBuffer(options.challenge);
        options.allowCredentials = (options.allowCredentials || []).map((credential) => ({
          ...credential,
          id: base64urlToBuffer(credential.id)
        }));
        const assertion = await navigator.credentials.get({publicKey: options});
        const credential = {
          id: assertion.id,
          rawId: bufferToBase64url(assertion.rawId),
          response: {
            authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
            clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
            signature: bufferToBase64url(assertion.response.signature),
            userHandle: assertion.response.userHandle ? bufferToBase64url(assertion.response.userHandle) : null
          },
          type: assertion.type,
          authenticatorAttachment: assertion.authenticatorAttachment
        };
        const verified = await migrationJson(verifyEndpoint, {
          method: "POST",
          body: JSON.stringify({credential})
        });
        if (verified.token) localStorage.setItem("dv_next_token", verified.token);
        if (verified.legacyMigrationToken) sessionStorage.setItem("dv_next_legacy_migration_token", verified.legacyMigrationToken);
        setMigrationAuthMessage(tNext("auth.signedIn", "Signed in."), "good");
        showMigrationAuthGate(false);
        await loadReport();
      } catch (error) {
        setMigrationAuthMessage(passkeyClientErrorMessage(error, tNext("auth.passkeyCancelled", "Passkey prompt was cancelled."), tNext("auth.passkeyUnavailable", "This browser does not support passkeys.")), "bad");
      } finally {
        if (button) button.disabled = false;
      }
    }
    function applyNextI18n() {
      document.documentElement.lang = nextI18n.locale;
      document.querySelectorAll("[data-next-i18n]").forEach((node) => {
        node.textContent = tNext(node.dataset.nextI18n, node.textContent);
      });
      document.querySelectorAll("[data-next-i18n-aria]").forEach((node) => {
        node.setAttribute("aria-label", tNext(node.dataset.nextI18nAria, node.getAttribute("aria-label") || ""));
      });
      document.querySelectorAll("[data-next-language-select]").forEach((select) => {
        select.value = nextI18n.locale;
      });
      updateTechnicalDetailsButton();
    }
    function renderLanguageOptions() {
      const options = nextI18n.locales.map((item) => {
        return `<option value="${escapeHtml(item.locale)}">${escapeHtml(item.nativeName || item.locale)}</option>`;
      }).join("");
      document.querySelectorAll("[data-next-language-select]").forEach((select) => {
        select.innerHTML = options;
        select.value = nextI18n.locale;
      });
    }
    async function setNextLocale(locale, options) {
      const normalized = normalizeNextLocale(locale);
      nextI18n.locale = normalized;
      if (options && options.persist) {
        localStorage.setItem("dv_next_locale", normalized);
        localStorage.setItem("dv_lang", legacyLocale(normalized));
      }
      try {
        const response = await fetch(`/api/next/i18n/${encodeURIComponent(normalized)}`, {cache: "no-store"});
        if (response.ok) {
          const payload = await response.json();
          nextI18n.locale = payload.locale || normalized;
          nextI18n.messages = payload.messages || {};
          if (Array.isArray(payload.locales) && payload.locales.length) nextI18n.locales = payload.locales;
        }
      } catch (error) {
        console.warn("Next i18n catalog unavailable", error);
      }
      renderLanguageOptions();
      applyNextI18n();
    }
    async function initNextI18n() {
      try {
        const response = await fetch("/api/next/i18n", {cache: "no-store"});
        if (response.ok) {
          const payload = await response.json();
          if (Array.isArray(payload.locales) && payload.locales.length) nextI18n.locales = payload.locales;
        }
      } catch (error) {
        console.warn("Next i18n manifest unavailable", error);
      }
      renderLanguageOptions();
      document.querySelectorAll("[data-next-language-select]").forEach((select) => {
        if (select.dataset.bound === "true") return;
        select.dataset.bound = "true";
        select.addEventListener("change", () => setNextLocale(select.value, {persist: true}).then(() => {
          const authGate = document.getElementById("migrationAuthGate");
          if (authGate && !authGate.classList.contains("hidden")) return null;
          return loadReport();
        }));
      });
      await setNextLocale(preferredNextLocale(), {persist: false});
    }
    function statusClass(state) {
      if (state === "already_completed" || state === "ready_for_confirmation") return "ok";
      if (String(state || "").startsWith("blocked")) return "error";
      return "warn";
    }
    function row(label, value) {
      return `<div class="row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }
    function rowsFromObject(data) {
      const entries = Object.entries(data || {});
      if (!entries.length) return row(tNext("common.none", "None"), "-");
      return entries.map(([key, value]) => row(key, typeof value === "object" ? JSON.stringify(value) : value)).join("");
    }
    function renderList(id, items) {
      const node = document.getElementById(id);
      const values = Array.isArray(items) ? items : [];
      node.innerHTML = values.length
        ? values.map((item) => row("", item)).join("")
        : row(tNext("common.none", "None"), "-");
    }
    function updateTechnicalDetailsButton() {
      const button = document.getElementById("technicalDetailsButton");
      if (!button) return;
      button.textContent = showTechnicalDetails
        ? tNext("migration.hideTechnicalDetails", "Hide Details")
        : tNext("migration.showTechnicalDetails", "Show Details");
      button.setAttribute(
        "aria-expanded",
        showTechnicalDetails ? "true" : "false"
      );
    }
    function toggleTechnicalDetails() {
      showTechnicalDetails = !showTechnicalDetails;
      const details = document.getElementById("technicalDetails");
      if (details) details.classList.toggle("hidden", !showTechnicalDetails);
      updateTechnicalDetailsButton();
    }
    function translatedState(state) {
      const key = `migration.state.${state || "unknown"}`;
      return tNext(key, String(state || "unknown").replaceAll("_", " "));
    }
    function wizardSummary(report) {
      const state = report.state || "unknown";
      const summaries = {
        ready_for_confirmation: tNext("migration.summaryReady", "The source is ready. Review the import source and start migration when you are ready."),
        ready_for_security_backfill: tNext("migration.summarySecurityBackfill", "The collection is present. Import legacy users, passkeys and media groups to complete migration."),
        blocked_target_not_empty: tNext("migration.summaryTargetNotEmpty", "This PostgreSQL database already contains collection data. Use an empty target database or wait for conflict-aware import."),
        already_completed: tNext("migration.summaryCompleted", "Migration is complete for this source."),
        not_required: tNext("migration.summaryNoSource", "No readable legacy source was found. Configure or mount a source to migrate data."),
        running: tNext("migration.summaryRunning", "Migration is running. Refresh to follow the latest status."),
        blocked_source_unreadable: tNext("migration.summarySourceUnreadable", "A legacy source was found, but DiscVault Next cannot read it yet."),
        blocked_schema_not_ready: tNext("migration.summarySchemaBlocked", "PostgreSQL schema migration must complete before import can start.")
      };
      return summaries[state] || (report.requiredActions || []).join(" ") || translatedState(state);
    }
    function stepState(report, key) {
      const state = report.state || "";
      const source = report.source || {};
      if (key === "source") {
        if (source.found && source.readable) return "done";
        return source.found ? "blocked" : "active";
      }
      if (key === "security") {
        if (state === "ready_for_security_backfill") return "active";
        if (state === "already_completed") return "done";
        return "pending";
      }
      if (key === "import") {
        if (state === "already_completed") return "done";
        if (state === "running" || state === "ready_for_confirmation") return "active";
        if (String(state).startsWith("blocked")) return "blocked";
        return "pending";
      }
      if (key === "done") {
        return state === "already_completed" ? "done" : "pending";
      }
      return "pending";
    }
    function renderWizard(report) {
      const source = report.source || {};
      const counts = importSourceCounts(source);
      const sourceDetail = pluginDisplayName(source.pluginId, source.pluginName || tNext("migration.importSource", "Import Source"));
      const securityCount = Number(counts.users || 0) + Number(counts.credentials || 0) + Number(counts.groups || 0);
      const steps = [
        {
          key: "source",
          label: tNext("migration.stepSource", "Source"),
          detail: source.found ? sourceDetail : tNext("migration.summaryNoSource", "No readable legacy source was found.")
        },
        {
          key: "security",
          label: tNext("migration.stepSecurity", "Users & Groups"),
          detail: securityCount
            ? `${formatNumber(counts.users)} ${tNext("migration.users", "Users")} / ${formatNumber(counts.groups)} ${tNext("migration.groups", "Groups")}`
            : tNext("migration.noSecurityData", "No legacy security data detected.")
        },
        {
          key: "import",
          label: tNext("migration.stepImport", "Import"),
          detail: translatedState(report.state)
        },
        {
          key: "done",
          label: tNext("migration.stepComplete", "Complete"),
          detail: report.latestRun?.status || tNext("migration.noRun", "No migration run has been recorded yet.")
        }
      ];
      const node = document.getElementById("wizardSteps");
      if (node) {
        node.innerHTML = steps.map((step, index) => {
          const state = stepState(report, step.key);
          const marker = state === "done" ? "OK" : String(index + 1);
          return `
            <div class="wizard-step ${escapeHtml(state)}">
              <div class="wizard-step-marker">${escapeHtml(marker)}</div>
              <strong>${escapeHtml(step.label)}</strong>
              <span>${escapeHtml(step.detail || "")}</span>
            </div>
          `;
        }).join("");
      }
      const summary = document.getElementById("wizardSummary");
      if (summary) summary.textContent = wizardSummary(report);
      const badge = document.getElementById("wizardModeBadge");
      if (badge) {
        badge.className = `badge ${statusClass(report.state)}`;
        badge.textContent = translatedState(report.state);
      }
    }
    function imageCountFromSource(source) {
      const mediaExtensions = source?.mediaExtensions || {};
      return Object.values(mediaExtensions).reduce((total, value) => total + Number(value || 0), 0);
    }
    function flowSummaryItem(value, label) {
      return `<div class="summary-item"><strong>${escapeHtml(formatNumber(value))}</strong><span>${escapeHtml(label)}</span></div>`;
    }
    function renderMigrationRecovery(report) {
      const node = document.getElementById("migrationRecoveryPanel");
      if (!node) return;
      const recovery = report.recovery || {};
      const actions = recovery.actions || [];
      if (!actions.length && !recovery.latestJobError) {
        node.innerHTML = "";
        return;
      }
      node.innerHTML = `
        <label class="option-row">
          <input type="checkbox" checked disabled>
          <span>
            <strong>${escapeHtml(tNext("migration.recoveryTitle", "Recovery plan"))}</strong>
            <span>${escapeHtml(recovery.latestJobError || tNext("migration.recoveryHelp", "Use these steps if migration cannot continue."))}</span>
          </span>
        </label>
        ${actions.slice(0, 5).map((action) => `
          <label class="option-row">
            <input type="checkbox" disabled>
            <span><strong>${escapeHtml(tNext("migration.requiredAction", "Action"))}</strong><span>${escapeHtml(action)}</span></span>
          </label>
        `).join("")}
      `;
    }
    function renderMigrationDryRunDiff(report) {
      const node = document.getElementById("migrationDryRunDiffPanel");
      if (!node) return;
      const rows = (report.dryRunDiff || {}).rows || [];
      if (!rows.length) {
        node.innerHTML = "";
        return;
      }
      node.innerHTML = `
        <label class="option-row">
          <input type="checkbox" checked disabled>
          <span>
            <strong>${escapeHtml(tNext("migration.dryRunDiffTitle", "Source and target comparison"))}</strong>
            <span>${escapeHtml(tNext("migration.dryRunDiffHelp", "Legacy counts compared with what is currently stored in PostgreSQL."))}</span>
          </span>
        </label>
        <div class="source-stats">
          ${rows.slice(0, 8).map((row) => `
            <span class="source-stat">
              <strong>${escapeHtml(formatNumber(row.target))}/${escapeHtml(formatNumber(row.source))}</strong>
              ${escapeHtml(row.label || row.key || "-")}
            </span>
          `).join("")}
        </div>
      `;
    }
    function selectedImportSource(report) {
      const sources = Array.isArray(report.importSources) ? report.importSources : [];
      return sources.find((source) => source.pluginId && source.pluginId === selectedImportSourceId) || report.source || {};
    }
    function setMigrationFlowText(titleKey, titleFallback, descriptionKey, descriptionFallback) {
      const title = document.getElementById("migrationFlowTitle");
      const description = document.getElementById("migrationFlowDescription");
      if (title) title.textContent = tNext(titleKey, titleFallback);
      if (description) description.textContent = tNext(descriptionKey, descriptionFallback);
    }
    function renderMigrationFlow(report) {
      const source = selectedImportSource(report);
      const counts = importSourceCounts(source);
      const state = report.state || "unknown";
      const isComplete = state === "already_completed";
      const isRunning = state === "running";
      const isBlocked = String(state).startsWith("blocked") || state === "not_required";
      const canPrepare = !!report.canStart && !isRunning && !isComplete && !isBlocked;
      const flowBadge = document.getElementById("migrationFlowBadge");
      if (flowBadge) {
        flowBadge.className = `badge ${statusClass(state)}`;
        flowBadge.textContent = translatedState(state);
      }
      if (isComplete) {
        setMigrationFlowText(
          "migration.flowCompleteTitle",
          "Migration complete",
          "migration.flowCompleteDescription",
          "The migration is complete. You can open DiscVault Next and continue with the new database."
        );
      } else if (isRunning) {
        setMigrationFlowText(
          "migration.flowRunningTitle",
          "Migration is running",
          "migration.flowRunningDescription",
          "DiscVault Next is importing the selected data. This page refreshes automatically."
        );
      } else if (isBlocked) {
        setMigrationFlowText(
          "migration.flowBlockedTitle",
          "Migration cannot start yet",
          "migration.flowBlockedDescription",
          "Resolve the action shown below, then refresh this page."
        );
      } else {
        setMigrationFlowText(
          "migration.flowTitle",
          "Review migration",
          "migration.flowDescription",
          "Check the detected data and confirm when you are ready to start."
        );
      }
      const summary = document.getElementById("migrationFlowSummary");
      if (summary) {
        summary.innerHTML = [
          flowSummaryItem(counts.movies || report.target?.counts?.movies || 0, tNext("collection.movies", "Movies")),
          flowSummaryItem(counts.collections || 0, tNext("migration.collections", "Collections")),
          flowSummaryItem(counts.box_sets || report.target?.counts?.containers || 0, tNext("migration.boxSets", "Box-sets")),
          flowSummaryItem(counts.users || 0, tNext("migration.users", "Users")),
          flowSummaryItem(counts.groups || 0, tNext("migration.groups", "Groups")),
          flowSummaryItem(counts.credentials || 0, tNext("migration.passkeys", "Passkeys")),
          flowSummaryItem(imageCountFromSource(source), tNext("migration.images", "Images"))
        ].join("");
      }
      renderMigrationRecovery(report);
      renderMigrationDryRunDiff(report);
      const options = document.getElementById("migrationFlowOptions");
      if (options) {
        options.innerHTML = [
          {
            title: tNext("migration.scopeCollectionTitle", "Collection data"),
            text: tNext("migration.scopeCollectionText", "Movies, people, credits, collections and box-sets will be imported."),
            checked: true
          },
          {
            title: tNext("migration.scopeSecurityTitle", "Users, groups and passkeys"),
            text: tNext("migration.scopeSecurityText", "Legacy users, media groups, group membership and passkeys will be imported when available."),
            checked: true
          },
          {
            title: tNext("migration.scopeMediaTitle", "Existing media files"),
            text: tNext("migration.scopeMediaText", "Posters, backdrops and profile images remain on the filesystem; DiscVault Next stores references to the current files."),
            checked: true
          }
        ].map((item) => `
          <label class="option-row">
            <input type="checkbox" ${item.checked ? "checked" : ""} disabled>
            <span><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.text)}</span></span>
          </label>
        `).join("");
      }
      const confirmBox = document.getElementById("migrationConfirmBox");
      const checkbox = document.getElementById("migrationConfirmCheckbox");
      const startButtons = [
        document.getElementById("startButton"),
        document.getElementById("migrationFlowStartButton")
      ].filter(Boolean);
      const appButton = document.getElementById("migrationFlowAppButton");
      if (confirmBox) confirmBox.classList.toggle("hidden", !canPrepare);
      if (checkbox) checkbox.checked = !!migrationConfirmed;
      startButtons.forEach((button) => {
        button.classList.toggle("hidden", !canPrepare);
        button.disabled = !(canPrepare && migrationConfirmed);
      });
      if (appButton) appButton.classList.toggle("hidden", !isComplete);
      if (isRunning) {
        window.setTimeout(() => loadReport().catch((error) => {
          document.getElementById("message").textContent = error.message;
        }), 3000);
      }
    }
    function renderTestReset(report) {
      const button = document.getElementById("migrationTestResetButton");
      if (!button) return;
      const enabled = !!report?.testReset?.enabled;
      button.classList.toggle("hidden", !enabled);
      button.disabled = !enabled;
    }
    function translatedResetPrompt(key, fallback, confirmation) {
      return tNext(key, fallback).replace("{confirmation}", confirmation);
    }
    async function resetTestDatabase() {
      const reset = lastMigrationReport?.testReset || {};
      const confirmation = reset.confirmation || "RESET TEST DATABASE";
      const confirmed = window.confirm(
        tNext("migration.testResetConfirm", "Reset the PostgreSQL test database so you can test migration again?")
      );
      if (!confirmed) return;
      const typed = window.prompt(
        translatedResetPrompt("migration.testResetPrompt", "Type {confirmation} to reset the test database.", confirmation),
        ""
      );
      if (typed !== confirmation) return;
      const button = document.getElementById("migrationTestResetButton");
      if (button) button.disabled = true;
      const message = document.getElementById("message");
      if (message) message.textContent = tNext("migration.testResetting", "Resetting test database...");
      const response = await fetch("/api/next/migration/test-reset", {
        method: "POST",
        headers: migrationHeaders({"Content-Type": "application/json"}),
        body: JSON.stringify({confirmation})
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.error) {
        throw new Error(payload.error || `Test database reset failed: HTTP ${response.status}`);
      }
      localStorage.removeItem("dv_next_token");
      migrationConfirmed = false;
      migrationIntroAccepted = false;
      selectedImportSourceId = null;
      if (message) message.textContent = tNext("migration.testResetDone", "Test database reset. Reloading migration status...");
      await loadReport();
    }
    function migrationNeedsIntro(report) {
      const state = report?.state || "unknown";
      return !["already_completed", "not_required", "running"].includes(state);
    }
    function renderMigrationIntro(report) {
      const needsIntro = migrationNeedsIntro(report);
      const showIntro = needsIntro && !migrationIntroAccepted;
      const intro = document.getElementById("migrationIntro");
      const workspace = document.getElementById("migrationWorkspace");
      const startButton = document.getElementById("startButton");
      if (intro) intro.classList.toggle("hidden", !showIntro);
      if (workspace) workspace.classList.toggle("hidden", showIntro);
      if (startButton) startButton.classList.toggle("hidden", showIntro);
      const badge = document.getElementById("migrationIntroBadge");
      if (badge && report?.state) {
        badge.textContent = translatedState(report.state);
        badge.className = `badge ${statusClass(report.state)}`;
      }
    }
    function importSourceCounts(source) {
      return source?.counts || source?.sourceCounts || {};
    }
    function importSourceBadge(source) {
      if (source?.readable && source?.found) {
        return {tone: "ok", text: tNext("migration.sourceReady", "Ready")};
      }
      if (source?.found) {
        return {tone: "warn", text: tNext("migration.sourceUnreadable", "Found, not readable")};
      }
      return {tone: "error", text: tNext("migration.sourceMissing", "Not found")};
    }
    function renderImportSources(report) {
      const node = document.getElementById("importSourceList");
      if (!node) return;
      const sources = Array.isArray(report.importSources) ? report.importSources : [];
      const selectedSource = report.source || {};
      if (!selectedImportSourceId) {
        selectedImportSourceId = selectedSource.pluginId
          || (sources.find((source) => source.readable && source.found) || {}).pluginId
          || (sources[0] || {}).pluginId
          || null;
      }
      if (!sources.length) {
        node.innerHTML = row(tNext("common.none", "None"), "-");
        return;
      }
      node.innerHTML = sources.map((source) => {
        const pluginId = source.pluginId || "";
        const counts = importSourceCounts(source);
        const mediaExtensions = source.mediaExtensions || {};
        const imageCount = Object.values(mediaExtensions).reduce((total, value) => total + Number(value || 0), 0);
        const badge = importSourceBadge(source);
        const selected = pluginId && pluginId === selectedImportSourceId;
        const warnings = Array.isArray(source.warnings) && source.warnings.length
          ? `<div class="label">${escapeHtml(source.warnings.join(" / "))}</div>`
          : "";
        return `
          <button type="button" class="source-option ${selected ? "selected" : ""}" data-import-source-id="${escapeHtml(pluginId)}">
            <div class="source-option-main">
              <div class="source-option-title">
                <strong>${escapeHtml(pluginDisplayName(pluginId, source.pluginName || source.name || pluginId || tNext("migration.importSource", "Import Source")))}</strong>
                <span class="label">${escapeHtml(source.readable ? tNext("migration.sourceDetected", "Legacy source detected") : badge.text)}</span>
              </div>
              <span class="badge ${badge.tone}">${escapeHtml(selected ? tNext("migration.selectedSource", "Selected") : badge.text)}</span>
            </div>
            <div class="source-stats">
              <span class="source-stat"><strong>${formatNumber(counts.movies)}</strong>${escapeHtml(tNext("collection.movies", "Movies"))}</span>
              <span class="source-stat"><strong>${formatNumber(counts.people)}</strong>${escapeHtml(tNext("migration.people", "People"))}</span>
              <span class="source-stat"><strong>${formatNumber(counts.users)}</strong>${escapeHtml(tNext("migration.users", "Users"))}</span>
              <span class="source-stat"><strong>${formatNumber(counts.groups)}</strong>${escapeHtml(tNext("migration.groups", "Groups"))}</span>
              <span class="source-stat"><strong>${formatNumber(counts.credentials)}</strong>${escapeHtml(tNext("migration.passkeys", "Passkeys"))}</span>
              <span class="source-stat"><strong>${formatNumber(imageCount)}</strong>${escapeHtml(tNext("migration.images", "Images"))}</span>
            </div>
            ${warnings}
          </button>
        `;
      }).join("");
      node.querySelectorAll("[data-import-source-id]").forEach((button) => {
        button.addEventListener("click", () => {
          selectedImportSourceId = button.dataset.importSourceId || null;
          migrationConfirmed = false;
          renderImportSources(lastMigrationReport || report);
          renderMigrationFlow(lastMigrationReport || report);
        });
      });
    }
    async function loadReport() {
      const message = document.getElementById("message");
      message.textContent = tNext("migration.loadingReport", "Loading report...");
      const response = await fetch("/api/next/migration/report", {
        cache: "no-store",
        headers: migrationHeaders()
      });
      if (response.status === 401) {
        localStorage.removeItem("dv_next_token");
        showMigrationAuthGate(true);
        setMigrationAuthMessage(tNext("auth.loginDescription", "Log in with your Passkey"), "bad");
        return;
      }
      if (!response.ok) throw new Error(`Report failed: HTTP ${response.status}`);
      const payload = await response.json();
      const report = payload.report || {};
      lastMigrationReport = report;
      const legacyAuth = report.legacyAuth || {};
      migrationLegacyAuthRequired = !!legacyAuth.required && !sessionStorage.getItem("dv_next_legacy_migration_token");
      if (migrationLegacyAuthRequired) {
        showMigrationAuthGate(true);
        setMigrationAuthMessage(tNext("migration.legacyAuthRequired", "Sign in with a legacy owner/admin passkey before starting migration."), "info");
        const loginButton = document.getElementById("migrationLoginButton");
        if (loginButton) loginButton.disabled = !!webauthnUnavailableReason();
        return;
      }
      showMigrationAuthGate(false);
      const imported = report.summary?.imported || {};
      const target = report.target?.counts || {};
      const source = report.source || {};
      const plugins = report.metadataPlugins || {};
      const state = report.state || "unknown";

      const stateBadge = document.getElementById("stateBadge");
      stateBadge.className = `badge ${statusClass(state)}`;
      stateBadge.textContent = translatedState(state);
      message.textContent = report.latestRun?.status
        ? `${tNext("migration.latestRun", "Latest run")}: ${report.latestRun.status}`
        : tNext("migration.noRun", "No migration run has been recorded yet.");
      renderWizard(report);
      renderMigrationIntro(report);

      document.getElementById("moviesCount").textContent = formatNumber(imported.movies ?? target.movies);
      document.getElementById("peopleCount").textContent = formatNumber(imported.people ?? target.people);
      document.getElementById("mediaCount").textContent = formatNumber(imported.media_assets ?? target.media_assets);
      document.getElementById("containerCount").textContent = formatNumber(target.containers);
      document.getElementById("pluginCount").textContent = formatNumber(plugins.enabled);

      renderImportSources(report);
      renderMigrationFlow(report);
      renderTestReset(report);
      document.getElementById("sourceList").innerHTML = [
        row(tNext("migration.importSource", "Import source"), pluginDisplayName(source.pluginId, source.pluginName || "-")),
        row(tNext("migration.sourceKind", "Source kind"), source.sourceKind || "-"),
        row(tNext("migration.sourceStatus", "Source status"), source.status || "-"),
        row(tNext("migration.legacyData", "Legacy data"), source.found ? tNext("migration.sourceFound", "found") : tNext("migration.sourceMissing", "not found")),
        row(tNext("migration.dataDirectory", "Data directory"), source.dataDir || "-"),
        row(tNext("migration.sqliteDatabase", "SQLite database"), source.sqliteDb || "-"),
        row(tNext("migration.databaseHash", "Database hash"), source.sourceDatabaseHash || "-"),
        row(tNext("migration.mediaMode", "Media mode"), source.mediaMigrationMode || "-"),
        row(tNext("migration.sourceCounts", "Source counts"), JSON.stringify(importSourceCounts(source))),
        row(tNext("migration.mediaExtensions", "Media extensions"), JSON.stringify(source.mediaExtensions || {}))
      ].join("");
      document.getElementById("skippedList").innerHTML = rowsFromObject(report.summary?.skipped || {});
      document.getElementById("pluginsList").innerHTML = (plugins.items || []).map((plugin) => `
        <div class="plugin">
          <strong>${escapeHtml(pluginDisplayName(plugin))}</strong>
          <span class="badge ${plugin.enabled ? "ok" : "warn"}">${plugin.enabled ? escapeHtml(tNext("common.enabled", "enabled")) : escapeHtml(tNext("common.disabled", "disabled"))}</span>
          <div class="label mono">${escapeHtml(tNext("appAdmin.pluginKey", "Plugin key"))}: ${escapeHtml(plugin.id)} / order ${escapeHtml(plugin.orderIndex)}</div>
        </div>
      `).join("") || row(tNext("migration.plugins", "Plugins"), "-");
      renderList("actionsList", report.requiredActions || []);

      const warnings = report.summary?.warnings || [];
      document.getElementById("warningsPanel").hidden = warnings.length === 0;
      renderList("warningsList", warnings);
      const details = document.getElementById("technicalDetails");
      if (details) details.classList.toggle("hidden", !showTechnicalDetails);
      updateTechnicalDetailsButton();
    }
    async function startMigration() {
      const button = document.getElementById("startButton");
      const flowButton = document.getElementById("migrationFlowStartButton");
      const message = document.getElementById("message");
      migrationIntroAccepted = true;
      renderMigrationIntro(lastMigrationReport || {});
      if (!migrationConfirmed) {
        message.textContent = tNext("migration.confirmRequired", "Confirm that you are ready to start migration.");
        return;
      }
      button.disabled = true;
      if (flowButton) flowButton.disabled = true;
      message.textContent = tNext("migration.starting", "Starting migration...");
      const response = await fetch("/api/next/migration/start", {
        method: "POST",
        headers: migrationHeaders({"Content-Type": "application/json"}),
        body: JSON.stringify(selectedImportSourceId ? {importSourceId: selectedImportSourceId} : {})
      });
      if (!response.ok) {
        if (response.status === 401) {
          sessionStorage.removeItem("dv_next_legacy_migration_token");
          migrationLegacyAuthRequired = true;
          showMigrationAuthGate(true);
        }
        const text = await response.text();
        throw new Error(`Migration start failed: HTTP ${response.status} ${text}`);
      }
      message.textContent = tNext("migration.queued", "Migration job queued.");
      await loadReport();
    }
    window.addEventListener("load", async () => {
      await initNextI18n();
      const loginButton = document.getElementById("migrationLoginButton");
      if (loginButton && loginButton.dataset.bound !== "true") {
        loginButton.dataset.bound = "true";
        loginButton.addEventListener("click", () => loginMigrationPasskey());
      }
      const confirmCheckbox = document.getElementById("migrationConfirmCheckbox");
      if (confirmCheckbox && confirmCheckbox.dataset.bound !== "true") {
        confirmCheckbox.dataset.bound = "true";
        confirmCheckbox.addEventListener("change", () => {
          migrationConfirmed = !!confirmCheckbox.checked;
          renderMigrationFlow(lastMigrationReport || {});
        });
      }
      const flowStartButton = document.getElementById("migrationFlowStartButton");
      if (flowStartButton && flowStartButton.dataset.bound !== "true") {
        flowStartButton.dataset.bound = "true";
        flowStartButton.addEventListener("click", () => startMigration().catch((error) => {
          document.getElementById("message").textContent = error.message;
        }));
      }
      const flowRefreshButton = document.getElementById("migrationFlowRefreshButton");
      if (flowRefreshButton && flowRefreshButton.dataset.bound !== "true") {
        flowRefreshButton.dataset.bound = "true";
        flowRefreshButton.addEventListener("click", () => loadReport().catch((error) => {
          document.getElementById("message").textContent = error.message;
        }));
      }
      const testResetButton = document.getElementById("migrationTestResetButton");
      if (testResetButton && testResetButton.dataset.bound !== "true") {
        testResetButton.dataset.bound = "true";
        testResetButton.addEventListener("click", () => resetTestDatabase().catch((error) => {
          const message = document.getElementById("message");
          if (message) message.textContent = error.message;
          renderTestReset(lastMigrationReport || {});
        }));
      }
      const introButton = document.getElementById("migrationIntroContinueButton");
      if (introButton && introButton.dataset.bound !== "true") {
        introButton.dataset.bound = "true";
        introButton.addEventListener("click", () => {
          migrationIntroAccepted = true;
          renderMigrationIntro(lastMigrationReport || {});
        });
      }
      const authenticated = await ensureMigrationAuth();
      if (!authenticated) return;
      loadReport().catch((error) => {
        showMigrationAuthGate(false);
        document.getElementById("migrationIntro").classList.add("hidden");
        document.getElementById("migrationWorkspace").classList.remove("hidden");
        document.getElementById("startButton").classList.add("hidden");
        document.getElementById("message").textContent = error.message;
        document.getElementById("stateBadge").className = "badge error";
        document.getElementById("stateBadge").textContent = tNext("common.error", "error");
      });
    });
  </script>
</body>
</html>
"""
