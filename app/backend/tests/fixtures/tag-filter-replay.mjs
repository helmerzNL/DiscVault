// Run the shipped advanced-filter functions, including their save/load path.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../../next_views_ui.py', import.meta.url), 'utf8');
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing function ${name}`);
  let depth = 0;
  for (let i = source.indexOf('{', start); i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`Unclosed function ${name}`);
}
const names = [
  'advancedSearchDefaults', 'normalizeAdvancedSearch', 'normalizeOriginLanguageValue',
  'normalizeCustomFilters', 'normalizeScoreBound', 'scoreBoundNumber',
  'normalizeVoteFloor', 'voteFloorNumber', 'advancedSearchActiveCount',
  'readAdvancedSearchControls', 'readCustomFilterControls', 'persistAdvancedSearch',
  'saveSmartFilter', 'applySmartFilter', 'resetAdvancedSearch',
  'movieMatchesAdvancedSearch', 'containerMatchesAdvancedSearch',
  'movieMatchesCustomFilters', 'movieCustomValueMap', 'customValueMatches',
  'syncTagFilterControl',
];
const setup = `
const CUSTOM_FILTER_OPS = ['is', 'contains', 'gte', 'lte', 'set', 'unset'];
let advancedSearch = advancedSearchDefaults(), smartFilters = [], activeSmartFilterId = '';
let members = [], customRows = {}, libraryTags = [], movies = [];
const tagSelect = {value: 'any', innerHTML: ''};
const controls = {advancedTagFilter: tagSelect};
const document = {
  getElementById: id => controls[id] || null,
  querySelectorAll: () => Object.entries(customRows).map(([key, c]) => ({dataset: {customFilterOp: key}, value: c.op})),
  querySelector: selector => ({value: customRows[selector.split('"')[1]]?.value})
};
const localStorage = {data: {}, setItem(k, v) {this.data[k] = v;}};
const window = {prompt: () => 'Weekend films'};
const tNext = (k, fallback) => fallback;
const renderCollectionSurface = () => {};
const containerGroupingEnabled = () => true;
const containerMemberMovies = () => members;
const movieYearNumber = () => null;
const localeState = {locale: 'en-US'};
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
`;
const context = vm.createContext({assert, console});
vm.runInContext(names.map(extract).join('\n') + setup, context);
const cases = {
  'a tag alone is enough to save a smart filter': `
    tagSelect.value = 'tag-only';
    saveSmartFilter();
    assert.equal(smartFilters.length, 1);
    assert.equal(smartFilters[0].filters.tag, 'tag-only');
    smartFilters = [];
  `,
  'save and reload retains the selected tag and every custom field type': `
    tagSelect.value = 'tag-one';
    customRows = {shelf:{op:'contains',value:'Cabinet'},price:{op:'gte',value:'0'},date:{op:'lte',value:'2026-09-07'},rip:{op:'is',value:'false'},edition:{op:'is',value:'limited'},present:{op:'set'},absent:{op:'unset'}};
    saveSmartFilter();
    smartFilters = JSON.parse(localStorage.data.dv_next_smart_filters);
    resetAdvancedSearch();
    applySmartFilter(smartFilters[0].id);
    assert.equal(advancedSearch.tag, 'tag-one');
    assert.deepEqual(advancedSearch.custom, customRows);
    assert.equal(advancedSearchActiveCount(), 8);
  `,
  'a specific tag matches its ID, not any tag or another tag with the same name': `
    const filters = {...advancedSearchDefaults(), tag: 'tag-one'};
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'tag-one', name:'Weekend'}]}, filters), true);
    assert.equal(movieMatchesAdvancedSearch({has_tags:true, tags:[{id:'tag-two', name:'Weekend'}]}, filters), false);
    assert.equal(movieMatchesAdvancedSearch({}, filters), false);
  `,
  'a tag-only filter excludes containers without a matching member': `
    const filters = {...advancedSearchDefaults(), tag: 'tag-one'};
    members = [{tags:[{id:'tag-two'}]}];
    assert.equal(containerMatchesAdvancedSearch({id:'box'}, filters), false);
    members.push({tags:[{id:'tag-one'}]});
    assert.equal(containerMatchesAdvancedSearch({id:'box'}, filters), true);
    members = [];
    assert.equal(containerMatchesAdvancedSearch({id:'box'}, filters), false);
  `,
  'old saved filters still match untagged films': `
    const filters = normalizeAdvancedSearch({yearFrom:''});
    assert.equal(filters.tag, 'any');
    assert.equal(movieMatchesAdvancedSearch({}, filters), true);
    assert.equal(advancedSearchActiveCount(filters), 0);
  `,
  'tag options combine loaded movies and the full catalogue, escaping user labels': `
    movies = [{tags:[{id:'tag-one', name:'Old name'}, {id:'tag-two', name:'Other'}]}];
    libraryTags = [{id:'tag-one', name:'<Renamed & tag>'}, {id:'tag-three', name:'Not loaded yet'}];
    syncTagFilterControl('tag-one');
    assert.equal(tagSelect.value, 'tag-one');
    assert.equal((tagSelect.innerHTML.match(/value="tag-one"/g) || []).length, 1);
    assert.ok(tagSelect.innerHTML.includes('&lt;Renamed &amp; tag>'));
    assert.ok(tagSelect.innerHTML.includes('value="tag-three"'));
    assert.ok(!tagSelect.innerHTML.includes('Old name'));
  `,
  'a missing saved tag remains selected while pages are loading and when deleted': `
    movies = []; libraryTags = [];
    syncTagFilterControl('missing-tag');
    assert.equal(tagSelect.value, 'missing-tag');
    assert.ok(tagSelect.innerHTML.includes('value="missing-tag"'));
    const filters = normalizeAdvancedSearch({tag:tagSelect.value});
    assert.equal(movieMatchesAdvancedSearch({}, filters), false);
    assert.equal(advancedSearchActiveCount(filters), 1);
  `,
  'tag and custom constraints must both match': `
    const filters = normalizeAdvancedSearch({tag:'tag-one', custom:{rip:{op:'is',value:'false'}}});
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'tag-one'}],custom_values:[{key:'rip',type:'boolean',value:false}]}, filters), true);
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'tag-one'}],custom_values:[{key:'rip',type:'boolean',value:true}]}, filters), false);
  `,
};
let failed = 0;
for (const [name, code] of Object.entries(cases)) {
  try {vm.runInContext(`{${code}}`, context); console.log('PASS: ' + name);}
  catch (error) {failed++; console.error('FAIL: ' + name + '\n' + error.message);}
}
process.exitCode = failed ? 1 : 0;
