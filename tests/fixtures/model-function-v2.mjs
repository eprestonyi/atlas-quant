// Hand-authored protocol fixture, not a fitted model or research result.
import fs from 'node:fs/promises';
import { functionDigest } from '../../web/model-function-runtime.js';
const old = JSON.parse(await fs.readFile(new URL('../../engine/tests/fixtures/model-function-golden-v1.json', import.meta.url), 'utf8'));
export async function sealFunction(artifact) {
  delete artifact.artifactId;
  artifact.artifactId = await functionDigest(artifact);
  return artifact;
}
export async function v2Fixture() {
  const a = structuredClone(old.cases.find(item => item.name === 'ridge').artifact);
  a.schema = 'atlas-model-function/2';
  a.inputSchema = [{name:'factor:size',type:'finite_number_or_null'}];
  a.estimator = {kind:'linear',coefficients:[[2],[3]],intercepts:[.1,.2]};
  a.transforms = {imputeMedian:[4],winsorLower:[1],winsorUpper:[9],scaleMean:[4],scaleScale:[2]};
  a.featureConstruction.schema = 'origin-state-features/2';
  a.featureConstruction.factors = [{id:'size',expression:'total_mv',direction:-1,role:'predictor'}];
  a.featureConstruction.preprocess.automatic = {schema:'auto-factor-preprocess/1'};
  a.featureConstruction.automatic = {schema:'auto-factor-preprocess/1',inputStage:'after_per_leg_semantic_transform_and_origin_aggregation',scaling:'train_fold_median_iqr',fitPopulation:'asset_rows_global_dates',factors:[{
    feature:'factor:size',expression:'total_mv',direction:-1,scope:'asset',transform:{kind:'log_positive',invalid:'missing'},aggregation:'signed_origin_dollar_over_gross'
  }]};
  return sealFunction(a);
}
