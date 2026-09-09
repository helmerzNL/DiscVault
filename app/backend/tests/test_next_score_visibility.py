import json
import shutil
import subprocess
import unittest
from pathlib import Path
try:
    from .test_next_score_filter import _function_source
except ImportError:
    from test_next_score_filter import _function_source

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'backend/next_views_ui.py'

@unittest.skipUnless(shutil.which('node'), 'node is unavailable')
class ScoreVisibilityTests(unittest.TestCase):
    def run_js(self, body):
        source = SOURCE.read_text(encoding='utf-8')
        names = ['advancedSearchDefaults', 'normalizeAdvancedSearch', 'advancedSearchActiveCount',
                 'normalizeScoreBound', 'scoreBoundNumber', 'normalizeVoteFloor', 'voteFloorNumber',
                 'movieScoreNumber', 'movieVoteCount', 'itemExternalScoreValue', 'compareExternalScore',
                 'libraryListExternalScoreHtml', 'libraryVisibleSlice', 'libraryRenderSentinelHtml',
                 'setAdvancedControlValue', 'syncScoreAvailabilityControls', 'readAdvancedSearchControls',
                 'movieMatchesAdvancedSearch', 'movieYearNumber', 'movieMatchesTagFilter',
                 'movieMatchesCustomFilters', 'movieCustomValueMap', 'customValueMatches',
                 'containerMatchesAdvancedSearch']
        script = '\n'.join(_function_source(source, name) for name in names)
        paging = (ROOT / 'frontend/js/library-paging.js').read_text(encoding='utf-8')
        script += '\n' + '\n'.join(_function_source(paging, name) for name in ['requestRender', 'renderWithProgressFocus', 'warn', 'translate'])
        script += r'''
const matches = movieMatchesAdvancedSearch;
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const normalizeTagSelection = x => Array.isArray(x) ? x : [];
const normalizeOriginLanguageValue = x => x || 'any';
const normalizeCustomFilters = x => x || {};
const escapeHtml = x => String(x);
const tNext = (key, fallback) => key === 'movieDetail.noScore' ? 'Geen score' : fallback;
const movieScoreLabel = () => 'Score';
let libraryRenderLimit = 120;
let libraryMoviesHasMore = false;
let libraryMovieTotal = 250;
let movies = [];
'''
        result = subprocess.run([shutil.which('node'), '-e', script + body], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_availability_filters_missing_zero_invalid_and_metadata_scores(self):
        self.run_js(r'''
const rows = [{}, {rating:'0'}, {rating:'n/a'}, {metadata:{rating:'7,5'}}, {rating:'10'}];
assert.deepEqual(rows.map(m => matches(m, normalizeAdvancedSearch({scoreAvailability:'without', scoreFrom:'8', scoreTo:'9', minVotes:'500'}))), [true,true,true,false,false]);
assert.deepEqual(rows.map(m => matches(m, normalizeAdvancedSearch({scoreAvailability:'with'}))), [false,false,false,true,true]);
assert.deepEqual(rows.map(m => matches(m, normalizeAdvancedSearch({}))), [true,true,true,true,true]);
assert.deepEqual(rows.map(m => matches(m, normalizeAdvancedSearch({scoreAvailability:'with', scoreFrom:'8'}))), [false,false,false,false,true]);
const saved = normalizeAdvancedSearch({scoreAvailability:'without', scoreFrom:'8', minVotes:'500'});
assert.equal(saved.scoreFrom, ''); assert.equal(saved.minVotes, '');
assert.equal(advancedSearchActiveCount(saved), 1);
assert.equal(normalizeAdvancedSearch({scoreAvailability:'bad'}).scoreAvailability, 'all');
assert.deepEqual(normalizeAdvancedSearch(JSON.parse(JSON.stringify(saved))), saved);
''')

    def test_controls_clear_conflicts_and_restore_saved_scores(self):
        self.run_js(r'''
const nodes = Object.fromEntries(['advancedScoreAvailability','advancedScoreFrom','advancedScoreTo','advancedMinVotes'].map(id => [id, {value:'',disabled:false}]));
const document = {getElementById:id => nodes[id]};
const readTagFilterControls = () => [];
const readCustomFilterControls = () => ({});
syncScoreAvailabilityControls({scoreAvailability:'with', scoreFrom:'7,5', scoreTo:'20', minVotes:'1,000'});
assert.equal(nodes.advancedScoreFrom.value, '7.5');
assert.equal(nodes.advancedScoreTo.value, '10');
assert.equal(nodes.advancedMinVotes.value, '1000');
assert.equal(advancedSearchActiveCount(readAdvancedSearchControls()), 4);
nodes.advancedScoreAvailability.value = 'without';
syncScoreAvailabilityControls(readAdvancedSearchControls());
for (const id of ['advancedScoreFrom','advancedScoreTo','advancedMinVotes']) {
  assert.equal(nodes[id].value, ''); assert.equal(nodes[id].disabled, true);
}
assert.equal(readAdvancedSearchControls().scoreAvailability, 'without');
syncScoreAvailabilityControls(advancedSearchDefaults());
for (const id of ['advancedScoreFrom','advancedScoreTo','advancedMinVotes']) assert.equal(nodes[id].disabled, false);
assert.equal(advancedSearchActiveCount(readAdvancedSearchControls()), 0);
''')

    def test_groups_match_member_availability_including_empty_groups(self):
        self.run_js(r'''
const containerGroupingEnabled = () => true;
const members = {mixed:[{rating:'8'}, {}], scored:[{rating:'9'}], unscored:[{rating:'0'}], empty:[]};
const containerMemberMovies = id => members[id];
const groups = Object.keys(members).map(id => ({id}));
assert.deepEqual(groups.map(c => containerMatchesAdvancedSearch(c, normalizeAdvancedSearch({scoreAvailability:'with'}))), [true,true,false,false]);
assert.deepEqual(groups.map(c => containerMatchesAdvancedSearch(c, normalizeAdvancedSearch({scoreAvailability:'without'}))), [true,false,true,false]);
assert.deepEqual(groups.map(c => containerMatchesAdvancedSearch(c, normalizeAdvancedSearch({}))), [true,true,true,true]);
''')

    def test_no_score_is_translated_for_movies_but_not_groups(self):
        self.run_js(r'''
assert.match(libraryListExternalScoreHtml({kind:'movie', movie:{}}), /Geen score/);
assert.match(libraryListExternalScoreHtml({rating:'0'}), /Geen score/);
assert.equal(libraryListExternalScoreHtml({kind:'container'}), '');
assert.equal(libraryListExternalScoreHtml({kind:'series'}), '');
assert.match(libraryListExternalScoreHtml({rating:'8'}), />8<\/span>/);
''')

    def test_manual_growth_preserves_focus_without_automatic_focus_theft(self):
        self.run_js(r'''
const callbacks = {}, frames = [];
let focusKind = '', observed, html = '';
const focusNode = kind => ({closest:selector => ({parentElement:surface}),focus: function(options) {assert.equal(options.preventScroll,true); focusKind=kind; document.activeElement=this;}});
const surface = {querySelector: selector => selector === '[data-library-load-more]' ? (libraryRenderLimit < 250 ? focusNode('button') : null) : focusNode('status')};
const button = {closest: selector => selector === '.library-render-progress' ? {parentElement:surface} : button};
const document = {activeElement:button, readyState:'complete', addEventListener:(type,fn)=>callbacks[type]=fn,
 querySelector:()=>({})};
const bridge = {hasMoreMovies:()=>false,getLoadedCount:()=>250,getSnapshotEpoch:()=>0,
 getRenderStep:()=>120,growRenderLimit:step=>libraryRenderLimit+=step,setHydrationComplete:()=>{},render:()=>{html=libraryRenderSentinelHtml(250);document.activeElement=null;}};
const window = {DiscVaultLibrary:bridge,addEventListener:()=>{},requestAnimationFrame:fn=>frames.push(fn),
 setTimeout:fn=>frames.push(fn),clearTimeout:()=>{},IntersectionObserver:class{constructor(fn){observed=fn;}observe(){}disconnect(){}}};
vm.runInNewContext(fs.readFileSync('frontend/js/library-paging.js','utf8'),{window,document,console});
const click = () => { document.activeElement=button; callbacks.click({target:button,preventDefault:()=>{}}); while(frames.length)frames.shift()(); };
click(); assert.equal(focusKind,'button');
click(); assert.equal(focusKind,'status');
focusKind=''; observed([{isIntersecting:true}]); while(frames.length)frames.shift()();
assert.equal(focusKind,'status');
document.activeElement = {}; focusKind='';
observed([{isIntersecting:true}]); while(frames.length)frames.shift()();
assert.equal(focusKind,'');
assert.match(html,/tabindex="-1"/);
''')

    def test_hydration_warning_preserves_focused_progress(self):
        self.run_js(r'''
let warning = '', restored = false;
const surface = {querySelector: () => ({focus: options => {assert.equal(options.preventScroll,true);restored=true;}})};
const document = {activeElement:{closest:()=>({parentElement:surface})}};
const api = {setHydrationWarning: message => {warning=message;document.activeElement=null;}};
const bridge = () => api;
warn(api, 'collection.hydrationTruncated', 'Only part of the library could be loaded.');
assert.equal(warning, 'Only part of the library could be loaded.');
assert.equal(restored, true, 'warning setter rerender must preserve focused paging controls');
''')

    def test_hydration_render_preserves_progress_focus_at_execution_time(self):
        self.run_js(r'''
const frames = [];
const state = {renderTimer:null};
const RENDER_DEBOUNCE_MS = 350;
const window = {setTimeout: fn => {frames.push(fn); return frames.length;}, clearTimeout:()=>{}};
let restored = 0;
const surface = {querySelector: () => ({focus: options => {assert.equal(options.preventScroll,true);restored++;}})};
const progress = {parentElement:surface};
const document = {activeElement:{closest:()=>progress}};
const bridge = () => ({render:()=>{document.activeElement=null;}});
requestRender(false);
assert.equal(restored,0);
frames.shift()();
assert.equal(restored,1);
document.activeElement = {closest:()=>progress};
requestRender(false);
document.activeElement = {};
frames.shift()();
assert.equal(restored,1, 'moving focus before a deferred page append must not steal it back');
''')

    def test_manual_and_automatic_growth_reach_unscored_rows_across_renders(self):
        self.run_js(r'''
const rows = Array.from({length:250}, (_, i) => ({id:i, rating:i < 245 ? String(1 + i % 9) : ''}));
const callbacks = {}, frames = [];
let observed, visible, html;
const render = () => {
  visible = libraryVisibleSlice(rows);
  html = libraryRenderSentinelHtml(rows.length);
  bridge.onRender?.();
};
const bridge = {
  hasMoreMovies: () => false, getLoadedCount: () => 250,
  getSnapshotEpoch: () => 0, getRenderStep: () => 120,
  growRenderLimit: step => libraryRenderLimit += step,
  render, setHydrationComplete: () => {},
};
const window = {DiscVaultLibrary:bridge, addEventListener:()=>{},
  requestAnimationFrame: fn => frames.push(fn),
  setTimeout: fn => frames.push(fn), clearTimeout:()=>{},
  IntersectionObserver: class { constructor(fn) { observed = fn; } observe() {} disconnect() {} }
};
const document = {readyState:'complete', addEventListener:(type, fn) => callbacks[type] = fn,
  querySelector: () => html?.includes('data-library-render-sentinel') ? {} : null};
vm.runInNewContext(fs.readFileSync('frontend/js/library-paging.js', 'utf8'), {window,document,console});
for (const direction of ['asc','desc']) {
  libraryRenderLimit = 120;
  rows.sort((a,b) => compareExternalScore(a,b,direction)); render();
  assert.equal(visible.length,120);
  assert.match(html, /120 of 250 shown/);
  assert.ok(callbacks.click, 'manual loading must handle newly rendered buttons');
  callbacks.click({target:{closest: selector => selector === '[data-library-load-more]' ? {} : null}, preventDefault:()=>{}});
  while (frames.length) frames.shift()();
  assert.equal(visible.length,240);
  observed([{isIntersecting:true}]);
  while (frames.length) frames.shift()();
  assert.equal(visible.length,250);
  assert.equal(visible.filter(m => !m.rating).length,5);
  assert.match(html, /250 of 250 shown/);
  assert.ok(!html.includes('data-library-load-more'));
}
libraryMoviesHasMore = true; movies = rows;
libraryMovieTotal = 500;
assert.match(libraryRenderSentinelHtml(250), /250 of 250 loaded rows shown/);
''')
