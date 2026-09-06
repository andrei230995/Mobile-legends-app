'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {recommend,recommendJSON}=require('../CoachAssets/Advisor.js');
const catalogue=require('../CoachAssets/catalogue.json');
const NOW=1800000000;
function input(overrides={}){return {now:NOW,catalogue,settings:{hero:'Alice',pricesConfirmed:true,purchasePreference:'balanced'},snapshot:{captureActive:true,capturedAt:NOW,gold:1000,goldAt:NOW,ownInventoryKnown:true,ownItems:[],ownItemsAt:NOW,enemyItems:[],enemyItemsAt:NOW,unknownEnemySlots:30},...overrides};}
function state(patch={},settings={}){const x=input();Object.assign(x.snapshot,patch);Object.assign(x.settings,settings);return x;}
function singleTarget(id,patch={}){const x=state(patch);x.catalogue={...catalogue,heroes:{Alice:{kind:'magic',core:[id]}}};return x;}
test('paused capture cannot produce a purchase',()=>assert.equal(recommend(state({captureActive:false})).status,'waiting'));
test('stale gold cannot produce a purchase',()=>assert.equal(recommend(state({goldAt:NOW-5})).status,'waiting'));
test('stale and uncertain inventories cannot produce a purchase',()=>{assert.equal(recommend(state({ownItemsAt:NOW-46})).status,'waiting');assert.equal(recommend(state({ownInventoryKnown:false})).status,'waiting');});
test('price acknowledgement is required',()=>assert.equal(recommend(state({}, {pricesConfirmed:false})).status,'waiting'));
test('negative and noninteger gold are rejected',()=>{for(const gold of [-1,NaN,1.2])assert.equal(recommend(state({gold})).status,'waiting');});
test('unknown owned items and seven slots are rejected',()=>{assert.equal(recommend(state({ownItems:['missing']})).status,'waiting');assert.equal(recommend(state({ownItems:Array(7).fill('boots')})).status,'waiting');});
test('Tough Boots deduct both owned recipe components',()=>{const r=recommend(singleTarget('tough-boots',{ownItems:['boots','magic-resist-cloak'],gold:230}));assert.equal(r.itemID,'tough-boots');assert.equal(r.cost,230);assert.equal(r.remainingGold,0);assert.deepEqual(r.consumes,['boots','magic-resist-cloak']);});
test('exact budget completes an item',()=>{const r=recommend(singleTarget('glowing-wand',{gold:2050}));assert.equal(r.itemID,'glowing-wand');assert.equal(r.cost,2050);});
test('one gold short recommends a component, not an unaffordable completed item',()=>{const r=recommend(singleTarget('glowing-wand',{gold:2049}));assert.equal(r.status,'buy');assert.notEqual(r.itemID,'glowing-wand');assert.ok(r.cost<=2049);assert.match(r.reason,/does not grant/);});
test('duplicate recipe nodes consume distinct owned items',()=>{const r=recommend(singleTarget('blade-armor',{ownItems:['steel-legplates','leather-jerkin','leather-jerkin'],gold:840}));assert.equal(r.itemID,'blade-armor');assert.equal(r.cost,840);assert.deepEqual(r.consumes,['steel-legplates','leather-jerkin','leather-jerkin']);});
test('one owned component is not credited twice',()=>{const r=recommend(singleTarget('blade-armor',{ownItems:['steel-legplates','leather-jerkin'],gold:1060}));assert.equal(r.itemID,'blade-armor');assert.equal(r.cost,1060);assert.equal(r.consumes.length,2);});
test('recursive components are credited without double counting',()=>{const r=recommend(singleTarget('glowing-wand',{ownItems:['mystery-codex','mystery-codex','vitality-crystal'],gold:1150}));assert.equal(r.itemID,'glowing-wand');assert.equal(r.cost,1150);assert.equal(r.consumes.length,3);});
test('a six-slot inventory can complete an upgrade that merges components',()=>{const r=recommend(singleTarget('tough-boots',{ownItems:['boots','magic-resist-cloak','holy-crystal','blood-wings','winter-crown','glowing-wand'],gold:230}));assert.equal(r.status,'buy');assert.ok(6-r.consumes.length+1<=6);});
test('six finished items cannot accept unrelated purchases',()=>{const r=recommend(singleTarget('glowing-wand',{ownItems:['tough-boots','holy-crystal','blood-wings','winter-crown','genius-wand','immortality'],gold:10000}));assert.equal(r.status,'waiting');});
test('a different completed boot prevents duplicate boots',()=>{const r=recommend(singleTarget('tough-boots',{ownItems:['swift-boots'],gold:1000}));assert.equal(r.status,'waiting');});
test('zero budget gives an honest saving target',()=>{const r=recommend(singleTarget('tough-boots',{gold:0}));assert.equal(r.status,'save');assert.equal(r.itemID,'magic-resist-cloak');assert.equal(r.cost,220);});
test('recognized healing equipment prioritizes an appropriate anti-heal route',()=>{const r=recommend(state({gold:2050,enemyItems:[['haass-claws']]}));assert.equal(r.targetID,'glowing-wand');});
test('stale enemy data is excluded',()=>{const r=recommend(state({gold:2050,enemyItems:[['haass-claws']],enemyItemsAt:NOW-46}));assert.notEqual(r.targetID,'glowing-wand');assert.match(r.caveat,/stale/);});
test('high equipment magic defense selects magic penetration',()=>{const r=recommend(state({gold:1970,enemyItems:[['athenas-shield','radiant-armor']]}));assert.equal(r.targetID,'divine-glaive');});
test('an inconsistent source recipe is excluded',()=>assert.equal(recommend(singleTarget('cursed-helmet',{gold:10000})).status,'waiting'));
test('malformed bridge input returns an explicit error',()=>assert.equal(JSON.parse(recommendJSON('{broken')).status,'error'));
test('catalogue references are closed and every recommended purchase respects budget and slots',()=>{
const map=new Map(catalogue.items.map(x=>[x.id,x]));
for(const item of catalogue.items)for(const part of item.recipe)assert.ok(map.has(part));
for(const hero of Object.keys(catalogue.heroes))for(const gold of [0,120,220,250,500,700,1500,3000,10000])for(const own of [[],['boots'],['mystery-codex','mystery-codex'],['tough-boots','genius-wand','holy-crystal','blood-wings','winter-crown','immortality']]){
 const r=recommend(state({gold,ownItems:own},{hero}));
 if(r.status==='buy'){assert.ok(r.cost>=0&&r.cost<=gold);assert.equal(r.remainingGold,gold-r.cost);assert.ok(own.length-r.consumes.length+1<=6);const remaining=[...own];for(const id of r.consumes){assert.ok(remaining.includes(id));remaining.splice(remaining.indexOf(id),1);}assert.ok(map.has(r.itemID));}
}
});
