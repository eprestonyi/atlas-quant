/** Presentation of a hand-authored numerical protocol fixture; no fitting or providers. */
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {v2Fixture} from '../../tests/fixtures/model-function-v2.mjs';
import {createForms} from '../quant-workspace/forms.js';
import {renderSavedModel,modelFormula} from '../quant-workspace/report-model.js';
import {createModelFunctionEditor} from '../quant-workspace/model-function-editor.js';

const dom=new JSDOM('<main></main>',{url:'http://localhost/quant/'});
globalThis.document=dom.window.document;
const main=document.querySelector('main');
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const C={state:{catalog:{factors:[{id:'size',name:'总市值',expression:'total_mv'}]}},esc,api:()=>{throw Error('NO_NETWORK');},fmt:String,icon:()=>''};
const F=createForms(C),a=await v2Fixture(),frozen=JSON.stringify(a);
main.innerHTML=renderSavedModel(C,F,{functionArtifact:a});
assert(main.querySelector('[data-saved-function]').textContent.includes('fₕ(X) = 0.2 + 3 × X₁'));
assert(main.querySelector('[data-saved-function]').textContent.includes('fₕ(X) = (V̂future − P) / scale'));
assert(main.querySelector('[data-saved-function]').textContent.includes('Fₕ(X) = V̂future = P + scale × fₕ(X)'));
assert.equal(modelFormula(a),'0.2 + 3 × X₁');
const input=main.querySelector('[data-model-input="factor:size"]');
assert.equal(input.open,false);
assert(input.querySelector('summary').textContent.includes('总市值（反向）'));
assert(input.querySelector('summary').textContent.includes('对数 → 篮子聚合'));
assert(input.querySelector('summary').textContent.includes('median / IQR'));
const construction=input.querySelector('[data-input-construction]').textContent;
assert(construction.includes('dⱼ,ₜ = total_mv'));
assert(construction.includes('gⱼ,ₜ = ln(dⱼ,ₜ)'));
assert(construction.includes('R₁ = Σⱼ(qⱼ pⱼ,ₜ / scale) × -1 × gⱼ,ₜ'));
const transform=input.querySelector('[data-input-transform]').textContent;
assert(transform.includes('R₁ = input["factor:size"]'));
assert(transform.includes('R₁ = null ? 4 : clip(R₁, 1, 9)'));
assert(transform.includes('X₁ = (u₁ − 4) / 2'));
assert(!transform.includes('ln('),'economic log is not repeated in the portable evaluation');
input.querySelector('summary').click();assert.equal(input.open,true);
assert.equal(JSON.stringify(a),frozen);

const global=structuredClone(a);global.featureConstruction.automatic.factors[0].scope='global';global.featureConstruction.automatic.factors[0].aggregation='global_once';
main.innerHTML=renderSavedModel(C,F,{functionArtifact:global});
assert(main.querySelector('[data-input-construction]').textContent.includes('R₁ = -1 × gₜ'));
assert(!main.querySelector('[data-input-construction]').textContent.includes('Σⱼ'));
assert(main.querySelector('[data-model-input]').textContent.includes('每训练日期一次'));

const price=structuredClone(a);price.featureConstruction.automatic.factors[0].transform={kind:'return_over_trailing_volatility',returnLag:1,volatilityWindow:20,volatilityLag:1,ddof:1,minVolatility:1e-8,invalid:'missing'};
main.innerHTML=renderSavedModel(C,F,{functionArtifact:price});
assert(main.querySelector('[data-input-construction]').textContent.includes('rⱼ,ₜ = dⱼ,ₜ / dⱼ,ₜ₋₁ − 1'));
assert(main.querySelector('[data-input-construction]').textContent.includes('sampleSD(rⱼ,ₜ₋20, …, rⱼ,ₜ₋1; ddof = 1)'));
assert(main.querySelector('[data-model-input]').textContent.includes('训练样本行'));

const noScale=structuredClone(a);noScale.transforms.scaleMean=noScale.transforms.scaleScale=null;
main.innerHTML=renderSavedModel(C,F,{functionArtifact:noScale});
assert(main.querySelector('[data-input-transform]').textContent.includes('X₁ = u₁'));
assert(!main.querySelector('[data-model-input] summary').textContent.includes('median / IQR'));

const escaped=structuredClone(a);escaped.featureConstruction.factors[0].expression='<img src=x onerror=alert(1)>';escaped.featureConstruction.automatic.factors[0].expression=escaped.featureConstruction.factors[0].expression;
main.innerHTML=renderSavedModel(C,F,{functionArtifact:escaped});assert(!main.querySelector('img'));

const editor=createModelFunctionEditor({...C,openModal(){},download(){},toast(){}},F);
main.innerHTML=editor.render({functionArtifact:a},{});
assert(main.querySelector('[data-mfe-input="rows"]').closest('label').textContent.includes('R 行数组'));
assert(main.textContent.includes('输入 R 已完成每腿经济变换与聚合'));
assert(main.textContent.includes('试算不会再次执行 log'));
assert(main.querySelector('[data-mfe-param="/estimator/coefficients/1/0"]').closest('label').textContent.includes('X₁'));
assert(main.querySelector('[data-input-transform]').textContent.includes('X₁ = (u₁ − 4) / 2'));
assert.equal(JSON.stringify(a),frozen);
console.log(JSON.stringify({processedFunctionFirst:true,exactFrozenParameters:true,rawConstructionSeparated:true,globalNotBasketAggregated:true,noDuplicateSemanticTransform:true,definitionsCollapsed:true,editorInputStageExplicit:true,immutable:true,fixtureOnly:true,networkCalls:0,modelFits:0}));
dom.window.close();
