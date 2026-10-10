import {functionDigest} from '../../web/model-function-runtime.js';

// Deliberately small protocol fixture; actual fitted cross-runtime cases are separate.
export async function sealReturnFunction(value) {
  delete value.artifactId; value.artifactId = await functionDigest(value); return value;
}
export async function returnFunctionFixture({mode='forecast',normalized=false,kind='linear',horizon=5} = {}) {
  const value = {
    schema:'atlas-model-function/4',hashAlgorithm:'sha256-canonical-f64-json/1',
    inputSchema:[{name:'factor:market',type:'finite_number_or_null'}],
    transforms:{imputeMedian:[.01],winsorLower:[-.1],winsorUpper:[.1],scaleMean:[.01],scaleScale:[.02]},
    estimator:{kind:'linear',coefficients:[[.04]],intercepts:[.001]},
    training:{trainStart:'20230103',trainEnd:'20231220',informationCutoff:'20240102',labelEndMax:'20231229',trainRows:240,trainDates:240},
    scope:{family:'fundamental',targetKind:'asset_return',horizonSessions:horizon,symbols:['600519.SH'],observationDays:1,researchStart:'20230101',researchEnd:'20251231',generalizationOutsideScopeValidated:false,studyMode:mode},
    outputs:[normalized ? 'volatility_standardized_asset_return' : 'asset_return'],
    identity:{response:'output[0]',simpleReturn:'response * responseScale',conditionalPrice:'originPrice * (1 + simpleReturn)',responseScale:'one_or_origin_known_daily_volatility_times_sqrt_h'},
    provenance:{estimator:'ridge',parameters:{alpha:1},sklearnVersion:'1.7.2'},
    editPolicy:{allowed:['estimator_numeric_parameters'],arbitraryCode:false,editedEvidenceStatus:'UNVALIDATED_USER_EDIT'},
    lineage:{parentArtifactId:null,status:'fitted'},
    featureConstruction:{
      schema:'asset-return-features/1',
      factors:[{id:'market',expression:'ext_ctx_000300_sh_close',direction:1,role:'predictor'}],
      preprocess:{winsorize:true,standardize:true,decorrelation:'none',correlationThreshold:.95,automatic:{schema:'auto-factor-preprocess/2'}},
      targetSpecification:{kind:'asset_return',horizonSessions:horizon,normalization:normalized ? {kind:'trailing_volatility',windowSessions:20,ddof:1,horizonScale:'sqrt_h',minimum:1e-8} : {kind:'none'}},
      inputs:[{feature:'factor:market',expression:'ext_ctx_000300_sh_close',direction:1,scope:'global',transform:{kind:'simple_return',lag:mode === 'association' ? horizon : 1,invalid:'missing'},aggregation:'global_once',economicType:'price',sourceUnit:'index_points',clock:'research_sessions',timing:{kind:mode === 'association' ? 'matched_period' : 'origin_known',horizonSessions:horizon}}]
    }
  };
  if (kind === 'constant') {
    value.estimator = {kind,value:[0]}; value.provenance = {estimator:'no_change',parameters:{},sklearnVersion:'1.7.2'};
    value.transforms = Object.fromEntries(Object.keys(value.transforms).map(key => [key,null]));
  } else if (kind === 'basis_linear') {
    value.estimator = {kind,coefficients:[[.02,-.03,.04]],intercepts:[.001],terms:[{kind:'power',feature:0,degree:2},{kind:'signed_log1p',feature:0},{kind:'signed_expm1',feature:0}],termCenter:[0,0,0],termScale:[1,1,1],signedExpm1AbsoluteInputCap:3};
    value.provenance = {estimator:'transformed_ridge',parameters:{alpha:10},sklearnVersion:'1.7.2'};
  } else if (kind === 'histogram_trees') {
    value.estimator = {kind,nodeFields:['value','feature','threshold','left','right','leaf','missingLeft'],thresholdRule:'left_if_less_equal',leafValuesIncludeLearningRate:true,outputs:[{baseline:.01,trees:[[[0,0,0,1,2,0,1],[-.02,0,0,0,0,1,0],[.03,0,0,0,0,1,0]]]}]};
    value.provenance = {estimator:'hist_gradient_boosting',parameters:{max_leaf_nodes:7,l2_regularization:1},sklearnVersion:'1.7.2'};
  }
  return sealReturnFunction(value);
}
