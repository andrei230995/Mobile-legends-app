/* Rankwise: deterministic, budget-aware next-purchase suggestions.
   This is a transparent heuristic, not a win-probability or DPS optimizer. */
'use strict';
function recommend(input) {
  const {snapshot:s,settings,catalogue,now}=input;
  const wait=(title,reason)=>({status:'waiting',title,reason});
  if(!settings.pricesConfirmed) return wait('Verify item prices first','The bundled catalogue is a dated reference. Confirm it matches your in-game shop before enabling price-specific advice.');
  if(!s.captureActive || now-s.capturedAt>5) return wait('Waiting for live capture','Start or resume the screen broadcast.');
  if(!s.ownInventoryKnown || now-s.ownItemsAt>45) return wait('Open the equipment scoreboard','Your complete inventory must be recognized recently before calculating a purchase.');
  if(!Number.isInteger(s.gold)||s.gold<0||now-s.goldAt>4) return wait('Waiting for your current gold','Return to the normal game view so the exact spendable gold can be read.');
  const items=new Map(catalogue.items.map(i=>[i.id,i]));
  const own=s.ownItems||[];
  if(own.length>6||own.some(id=>!items.has(id)))return wait('Inventory needs another reading','An unknown item or invalid slot count was detected.');
  const hero=catalogue.heroes[settings.hero];
  if(!hero)return wait('Choose a supported hero','This edition needs a curated item path for your hero.');
  const enemyFresh=now-s.enemyItemsAt<=45;
  const enemy=enemyFresh?(s.enemyItems||[]).flat().map(id=>items.get(id)).filter(Boolean):[];
  const tags=enemy.flatMap(i=>i.tags||[]);
  const count=t=>tags.filter(x=>x===t).length;
  const ownTags=own.flatMap(id=>items.get(id).tags||[]);
  const magical=hero.kind==='magic';
  const tank=hero.kind==='tank';
  const targets=[];
  function validRecipe(id,path=new Set()){const item=items.get(id);if(!item||item.recipeIssue||path.has(id))return false;const next=new Set(path);next.add(id);return item.recipe.every(part=>validRecipe(part,next))&&item.cost>=item.recipe.reduce((sum,part)=>sum+items.get(part).cost,0);}
  function target(id,priority,reason){if(!items.has(id)||own.includes(id)||!validRecipe(id))return;if(items.get(id).tags.includes('advancedBoots')&&ownTags.includes('advancedBoots'))return;const prev=targets.find(t=>t.id===id);if(prev){if(priority>prev.priority)Object.assign(prev,{priority,reason});}else targets.push({id,priority,reason});}
  hero.core.forEach((id,index)=>target(id,60-index*4,'Continue the suggested '+settings.hero+' item path.'));
  const urgency=settings.purchasePreference==='survive'?20:0;
  if(count('healing')&&!ownTags.includes('antiHeal')) {
    if(magical) target('glowing-wand',87,'Enemy equipment includes healing or lifesteal. Glowing Wand supplies anti-heal when you damage them.');
    else if(!tank)target('sea-halberd',87,'Enemy equipment includes healing or lifesteal. Sea Halberd applies anti-heal through damage.');
  }
  const enemyRows=enemyFresh?(s.enemyItems||[]).map(row=>row.map(id=>items.get(id)).filter(Boolean)):[];
  const equipmentDefense=stat=>Math.max(0,...enemyRows.map(row=>row.reduce((sum,item)=>sum+(item.stats?.[stat]||0),0)));
  if(magical&&equipmentDefense('magicdefense')>=70&&!ownTags.includes('magicPenPercent'))target('divine-glaive',83,'A recognized enemy build has at least 70 magic defense from equipment. Consider percentage magic penetration against that target.');
  if(!magical&&!tank&&equipmentDefense('physdefense')>=70&&!ownTags.includes('physicalPenPercent'))target('malefic-roar',83,'A recognized enemy build has at least 70 physical defense from equipment. Consider percentage physical penetration against that target.');
  if(count('magicDamage')>=3&&!ownTags.includes('magicDefense'))target('athenas-shield',68+urgency,'Enemy equipment leans toward magic damage. Consider protection against magic burst; equipment alone cannot establish their actual damage pattern.');
  if(count('attackSpeed')>=3&&tank&&!own.includes('blade-armor'))target('blade-armor',75+urgency,'Enemy attack-speed items suggest basic-attack pressure. Blade Armor is a defensive option for your frontline role.');
  if(count('physicalDamage')>=3&&!magical&&!tank&&hero.kind==='marksman'&&!own.includes('wind-of-nature'))target('wind-of-nature',70+urgency,'Enemy equipment suggests physical pressure. Wind of Nature is an active defense; you must trigger it in time.');
  // Walk a recipe tree, consuming each existing inventory item at most once.
  function progress(id,inventory,visiting=new Set()) {
    const item=items.get(id); if(!item||visiting.has(id))throw Error('Invalid item recipe');
    const index=inventory.indexOf(id);
    if(index>=0){inventory.splice(index,1);return {credit:item.cost,consumes:[id],uncovered:[]};}
    const next=new Set(visiting);next.add(id);
    let credit=0,consumes=[],uncovered=[id];
    for(const part of item.recipe){const p=progress(part,inventory,next);credit+=p.credit;consumes.push(...p.consumes);uncovered.push(...p.uncovered);}
    return {credit,consumes,uncovered};
  }
  const choices=[];
  for(const t of targets){
    const p=progress(t.id,[...own]);
    for(const id of [...new Set(p.uncovered)]){
      const item=items.get(id); const purchase=progress(id,[...own]);
      const cost=item.cost-purchase.credit;
      if(cost<0||own.length-purchase.consumes.length+1>6)continue;
      // Buying a component is progress toward a priority goal, not equivalent to its completed passive.
      const isComplete=id===t.id;
      choices.push({itemID:id,targetID:t.id,cost,consumes:purchase.consumes,priority:t.priority+(isComplete?12:0)+(purchase.consumes.length?3:0),reason:t.reason,targetRemaining:Math.max(0,items.get(t.id).cost-p.credit-cost),isComplete});
    }
  }
  const affordable=choices.filter(c=>c.cost<=s.gold).sort((a,b)=>b.priority-a.priority||b.cost-a.cost||a.itemID.localeCompare(b.itemID));
  if(!affordable.length){
    if(!choices.length)return wait('Keep your current items','No supported upgrade fits your six slots. This edition does not recommend selling items automatically.');
    const next=[...choices].sort((a,b)=>a.cost-b.cost||b.priority-a.priority)[0];
    return {status:'save',title:'Save '+(next.cost-s.gold)+' more gold',reason:'The cheapest useful next step is '+items.get(next.itemID).name+'. Avoid buying an unrelated item just to spend gold.',itemID:next.itemID,targetID:next.targetID,cost:next.cost,remainingGold:s.gold,targetRemaining:next.targetRemaining,consumes:next.consumes};
  }
  const best=affordable[0];
  const componentNote=best.isComplete?'':' This is a component toward '+items.get(best.targetID).name+'; it does not grant that finished item’s passive.';
  return {status:'buy',title:'Consider '+items.get(best.itemID).name,reason:best.reason+componentNote,itemID:best.itemID,targetID:best.targetID,cost:best.cost,remainingGold:s.gold-best.cost,targetRemaining:best.targetRemaining,consumes:best.consumes,
    alternatives:affordable.filter(c=>c.itemID!==best.itemID).filter((c,i,all)=>all.findIndex(x=>x.itemID===c.itemID)===i).slice(0,2).map(c=>({itemID:c.itemID,targetID:c.targetID,cost:c.cost,reason:c.reason})),
    caveat:enemyFresh?'Based on recognized equipment; unknown slots, enemy levels, damage taken and skill timing are not modeled.':'Enemy equipment is stale or missing. This suggestion uses your own build only.'};
}
function recommendJSON(json){try{return JSON.stringify(recommend(JSON.parse(json)));}catch(e){return JSON.stringify({status:'error',title:'Advice paused',reason:'The catalogue or captured state could not be evaluated safely.'});}}
if(typeof module!=='undefined')module.exports={recommend,recommendJSON};
