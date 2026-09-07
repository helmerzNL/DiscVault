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
  'movieMatchesCustomFilters', 'movieCustomValueMap', 'customValueMatches', 'syncTagFilterControl',
];
for (const name of ['normalizeTagSelection', 'readTagFilterControls', 'movieMatchesTagFilter']) {
  if (source.includes(`function ${name}(`)) names.push(name);
}
const setup = `
const CUSTOM_FILTER_OPS = ['is', 'contains', 'gte', 'lte', 'set', 'unset'];
let advancedSearch = advancedSearchDefaults(), smartFilters = [], activeSmartFilterId = '';
let members = [], customRows = {}, libraryTags = [], movies = [], selectedTags = [];
let libraryTagsLoaded = true;
const tagPicker = {innerHTML:'', querySelectorAll: () => selectedTags.map(id => ({dataset:{advancedTag:id}}))};
const matchSelect = {value:'any'};
const controls = {advancedTagFilter:tagPicker, advancedTagMatch:matchSelect};
const document = {
  getElementById: id => controls[id] || null,
  querySelectorAll: () => Object.entries(customRows).map(([key, c]) => ({dataset:{customFilterOp:key},value:c.op})),
  querySelector: selector => ({value:customRows[selector.split('"')[1]]?.value})
};
const localStorage = {data:{},setItem(k,v){this.data[k]=v;}};
const window = {prompt:()=>'Weekend films'};
const tNext = (k,fallback)=>fallback;
const renderCollectionSurface = ()=>{};
const containerGroupingEnabled = ()=>true;
const containerMemberMovies = ()=>members;
const movieYearNumber = ()=>null;
const localeState = {locale:'en-US'};
const escapeHtml = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
`;
const cases = {
  'save/reload retains multiple tags, AND mode and all custom field types': `
    selectedTags=['tag-one','tag-two']; matchSelect.value='all';
    customRows={shelf:{op:'contains',value:'Cabinet'},price:{op:'gte',value:'0'},date:{op:'lte',value:'2026-09-07'},rip:{op:'is',value:'false'},edition:{op:'is',value:'limited'},present:{op:'set'},absent:{op:'unset'}};
    saveSmartFilter(); smartFilters=JSON.parse(localStorage.data.dv_next_smart_filters);
    assert.equal(smartFilters.length,1);
    resetAdvancedSearch(); applySmartFilter(smartFilters[0].id);
    assert.deepEqual(advancedSearch.tags,['tag-one','tag-two']);
    assert.equal(advancedSearch.tagMatch,'all');
    assert.deepEqual(advancedSearch.custom,customRows);
    assert.equal(advancedSearchActiveCount(),8);
  `,
  'OR matches either selected ID, not an unrelated tag with the same name': `
    const f=normalizeAdvancedSearch({tags:['one','two'],tagMatch:'any'});
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'one'}]},f),true);
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'two'}]},f),true);
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'three',name:'one'}]},f),false);
    assert.equal(movieMatchesAdvancedSearch({},f),false);
  `,
  'AND requires every selected tag on the same movie': `
    const f=normalizeAdvancedSearch({tags:['one','two'],tagMatch:'all'});
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'one'},{id:'two'},{id:'three'}]},f),true);
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'one'}]},f),false);
    assert.equal(movieMatchesAdvancedSearch({},f),false);
  `,
  'containers require a member matching the complete tag expression': `
    const f=normalizeAdvancedSearch({tags:['one','two'],tagMatch:'all'});
    members=[{tags:[{id:'one'}]},{tags:[{id:'two'}]}];
    assert.equal(containerMatchesAdvancedSearch({id:'box'},f),false);
    assert.equal(containerMatchesAdvancedSearch({id:'box'},{...f,tagMatch:'any'}),true);
    members.push({tags:[{id:'one'},{id:'two'}]});
    assert.equal(containerMatchesAdvancedSearch({id:'box'},f),true);
    members=[]; assert.equal(containerMatchesAdvancedSearch({id:'box'},f),false);
  `,
  'legacy single-tag smart filters migrate without widening the selection': `
    smartFilters=[{id:'old',filters:{tag:'one'}}]; applySmartFilter('old');
    assert.deepEqual(advancedSearch.tags,['one']);
    assert.equal(advancedSearch.tagMatch,'any');
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'two'}]},advancedSearch),false);
    assert.deepEqual(normalizeAdvancedSearch({tag:'one',tags:[]}).tags,[]);
  `,
  'no selected tags means no constraint, including in AND mode': `
    for(const old of [{},{tag:'any'},{tags:[],tagMatch:'all'}]) {
      const f=normalizeAdvancedSearch(old);
      assert.deepEqual(f.tags,[]); assert.equal(advancedSearchActiveCount(f),0);
      assert.equal(movieMatchesAdvancedSearch({},f),true);
    }
  `,
  'normalization removes duplicates and malformed entries and defaults invalid modes to OR': `
    const f=normalizeAdvancedSearch({tags:['one','one',' two ','',null,{},'any'],tagMatch:'invalid'});
    assert.deepEqual(f.tags,['one','two']); assert.equal(f.tagMatch,'any');
  `,
  'selected missing tags remain available and escaped names use the latest catalogue': `
    movies=[{tags:[{id:'one',name:'Old'}]}]; libraryTags=[{id:'one',name:'<New & name>'}];
    syncTagFilterControl(['one','missing']);
    assert.ok(tagPicker.innerHTML.includes('data-advanced-tag="one"'));
    assert.ok(tagPicker.innerHTML.includes('data-advanced-tag="missing"'));
    assert.equal((tagPicker.innerHTML.match(/aria-pressed="true"/g)||[]).length,2);
    assert.ok(tagPicker.innerHTML.includes('&lt;New &amp; name>'));
  `,
  'tag matches still require custom constraints to match': `
    const f=normalizeAdvancedSearch({tags:['one','two'],custom:{rip:{op:'is',value:'false'}}});
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'one'}],custom_values:[{key:'rip',type:'boolean',value:false}]},f),true);
    assert.equal(movieMatchesAdvancedSearch({tags:[{id:'one'}],custom_values:[{key:'rip',type:'boolean',value:true}]},f),false);
  `,
};
let failed=0;
for(const [name,code] of Object.entries(cases)) {
  try {vm.runInNewContext(names.map(extract).join('\n')+setup+`{${code}}`,{assert,console});console.log('PASS: '+name);}
  catch(error){failed++;console.error('FAIL: '+name+'\n'+error.message);}
}
process.exitCode=failed?1:0;
