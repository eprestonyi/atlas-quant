import test from 'node:test';
import assert from 'node:assert/strict';
import {validateStatisticalQuant} from '../edge/statistical-quant/validation.mjs';
import {assertFactorCapabilities} from '../edge/factor-capabilities.mjs';
const base={schemaVersion:2,name:'automatic contract',universe:{symbols:['000001.SZ'],start:'20230101',end:'20250930'},research:{mode:'statistical_quant'},factors:[],target:{kind:'asset_price'},model:{family:'trend'},execution:{enabled:false}};
const auto={automatic:{schema:'auto-factor-preprocess/1'}};
test('preprocessing is explicit, versioned and leaves old studies unchanged',()=>{
  assert.equal(validateStatisticalQuant(base).preprocess.automatic,undefined);
  assert.deepEqual(validateStatisticalQuant({...base,preprocess:auto}).preprocess.automatic,auto.automatic);
  for (const automatic of [null,true,{}, {schema:'auto-factor-preprocess/99'}, {...auto.automatic,guess:true}])
    assert.throws(()=>validateStatisticalQuant({...base,preprocess:{automatic}}));
});
test('bare raw close fails before data acquisition, declared ratios preserve their units',()=>{
  for(const expression of ['raw_close','( raw_close )']) assert.throws(()=>validateStatisticalQuant({...base,preprocess:auto,factors:[{id:'raw',expression}]}));
  assert.doesNotThrow(()=>validateStatisticalQuant({...base,preprocess:auto,factors:[{id:'bps',expression:'fd_bps/raw_close'}]}));
});
test('named index identities require scope-aware preprocessing and reject unknown sources',()=>{
  const factor={id:'index',expression:'ext_ctx_000300_sh_close'};
  assert.throws(()=>validateStatisticalQuant({...base,factors:[factor]}));
  assert.doesNotThrow(()=>validateStatisticalQuant({...base,preprocess:auto,factors:[factor]}));
  assert.throws(()=>validateStatisticalQuant({...base,preprocess:auto,factors:[{...factor,expression:'ext_ctx_999999_sh_close'}]}));
  assert.throws(()=>assertFactorCapabilities({...base,preprocess:auto},{factorPreprocessFormats:'auto-factor-preprocess/1'}));
});


test('typed processing config preserves bounded per-factor overrides and exact runtime capability',()=>{
  const factor={id:'amount',expression:'fd_ebit',direction:1,role:'predictor'};
  const typed={schema:'auto-factor-preprocess/2',overrides:{amount:{transform:{kind:'signed_log1p',referenceUnit:1,invalid:'missing'}}}};
  const study={...base,factors:[factor],preprocess:{automatic:typed}};
  assert.deepEqual(validateStatisticalQuant(study).preprocess.automatic,typed);
  assert.throws(()=>assertFactorCapabilities(study,{factorPreprocessFormats:['auto-factor-preprocess/1']}));
  assert.doesNotThrow(()=>assertFactorCapabilities(study,{factorPreprocessFormats:['auto-factor-preprocess/2']}));
  assert.throws(()=>assertFactorCapabilities({...base,preprocess:auto},{factorPreprocessFormats:['auto-factor-preprocess/2']}));
  for(const change of [
    {overrides:[]}, {overrides:{unknown:typed.overrides.amount}},
    {overrides:{amount:{transform:{kind:'signed_log1p',referenceUnit:100,invalid:'missing'}}}},
    {overrides:{amount:{transform:{kind:'identity'},guess:true}}},
    {overrides:{amount:{transform:{kind:'simple_return',lag:5,invalid:'missing'}}}}
  ]) assert.throws(()=>validateStatisticalQuant({...study,preprocess:{automatic:{...typed,...change}}}));
  assert.throws(()=>validateStatisticalQuant({...study,factors:[{...factor,role:'hedge'}]}));
  assert.throws(()=>validateStatisticalQuant({...study,preprocess:{automatic:{...typed,schema:'auto-factor-preprocess/1'}}}));
});
