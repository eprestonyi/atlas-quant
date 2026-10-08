import {ApiError} from '../errors.mjs';
import {body,json,NOW,random,rate,sha} from '../runtime.mjs';
import {ownedStageForRun,parsedStage} from '../bundles/storage.mjs';
import {detailRecord} from '../bundles/pages.mjs';
import {validateFunction,evaluateFunction,deriveFunction,ModelFunctionError,FUNCTION_LIMITS} from '../../web/model-function-runtime.js';

const HASH = /^[a-f0-9]{64}$/, ID = /^[a-f0-9-]{36}$/;
const fail = (message,code='INVALID_MODEL_FUNCTION',status=400) => {throw new ApiError(code,message,status);};
const obj = x => x && typeof x === 'object' && !Array.isArray(x);
const exact = (x,allowed) => obj(x) && Object.keys(x).sort().join(',') === [...allowed].sort().join(',');
function reference(value) {
  if (exact(value,['functionId','artifactId']) && ID.test(value.functionId) && HASH.test(value.artifactId))
    return {functionId:value.functionId,artifactId:value.artifactId};
  if (exact(value,['runId','bundleId','modelFitId']) && ID.test(value.runId) && HASH.test(value.bundleId) && typeof value.modelFitId === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(value.modelFitId))
    return {runId:value.runId,bundleId:value.bundleId,modelFitId:value.modelFitId};
  fail('请指定完整且固定版本的私有函数来源');
}
function item(row) {
  return {id:row.id,name:row.name,artifactId:row.artifact_id,parentArtifactId:row.parent_artifact_id,
    source:JSON.parse(row.source_ref),createdAt:row.created_at,evidenceStatus:'UNVALIDATED_USER_EDIT',
    ref:{functionId:row.id,artifactId:row.artifact_id}};
}
async function readSaved(env,row) {
  const object = await env.ARTIFACTS.get(row.object_key);
  if (!object || object.size !== row.byte_length || object.size > FUNCTION_LIMITS.bytes) fail('函数文件缺失或大小不一致','FUNCTION_INTEGRITY',503);
  const text = await object.text();
  if (await sha(text) !== row.object_sha256) fail('函数文件校验失败','FUNCTION_INTEGRITY',503);
  let a; try {a=JSON.parse(text);await validateFunction(a);} catch {fail('已保存函数格式校验失败','FUNCTION_INTEGRITY',503);}
  if (a.artifactId !== row.artifact_id || a.lineage.parentArtifactId !== row.parent_artifact_id || a.lineage.status !== 'UNVALIDATED_USER_EDIT') fail('函数索引与内容不一致','FUNCTION_INTEGRITY',503);
  return a;
}
export async function resolveFunction(env,owner,source) {
  const ref = reference(source);
  if (ref.functionId) {
    const row = await env.DB.prepare('SELECT * FROM quant_model_functions WHERE id=? AND owner=?').bind(ref.functionId,owner).first();
    if (!row) fail('函数不存在','NOT_FOUND',404);
    if (row.artifact_id !== ref.artifactId) fail('函数版本不匹配','FUNCTION_VERSION_CHANGED',409);
    return {ref,item:item(row),artifact:await readSaved(env,row)};
  }
  const stage = await ownedStageForRun(env,owner,ref.runId);
  if (!stage) fail('冻结研究函数不存在','NOT_FOUND',404);
  if (stage.bundle_id !== ref.bundleId) fail('报告版本不匹配，请重新打开报告','BUNDLE_VERSION_CHANGED',409);
  const fit = (await detailRecord(env,stage,await parsedStage(stage),new URLSearchParams({collection:'modelFits',id:ref.modelFitId}))).item;
  if (!fit.functionArtifact) fail('该历史拟合没有保存可移植函数，请保留原报告','FUNCTION_NOT_EXPORTED',409);
  const artifact = await validateFunction(fit.functionArtifact);
  if (artifact.lineage.status !== 'fitted') fail('研究拟合函数的来源状态无效','FUNCTION_INTEGRITY',503);
  return {ref,artifact};
}
async function derive(env,owner,input) {
  if (!exact(input,['source','edits','name','requestId']) || !ID.test(input.requestId) || typeof input.name !== 'string' || !input.name.trim() || input.name.length > 160)
    fail('保存需要函数来源、修改、名称与本次请求标识');
  const source=reference(input.source),name=input.name.trim();
  const requestHash=await sha(JSON.stringify({source,edits:input.edits,name}));
  const prior=()=>env.DB.prepare('SELECT * FROM quant_model_functions WHERE owner=? AND request_id=?').bind(owner,input.requestId).first();
  const reply=async(row,idempotent)=>{
    if (row.request_hash !== requestHash) fail('同一保存请求不能更改内容','IDEMPOTENCY_CONFLICT',409);
    return {item:item(row),artifact:await readSaved(env,row),idempotent};
  };
  const existing=await prior(); if(existing)return reply(existing,true);
  await rate(env,'model-function-save:'+owner,30,3600);
  const parent=(await resolveFunction(env,owner,source)).artifact;
  const artifact=await deriveFunction(parent,input.edits),text=JSON.stringify(artifact);
  const bytes=new TextEncoder().encode(text).length,hash=await sha(text);
  const id=random(),key=`model-functions/${owner}/${artifact.artifactId}/${hash}.json`;
  // An immutable content-addressed put may precede a failed pointer commit. A retry
  // checks the existing receipt first and reuses the same bytes; no blind deletion.
  await env.ARTIFACTS.put(key,text,{httpMetadata:{contentType:'application/json'}});
  const args=[id,owner,input.requestId,requestHash,name,artifact.artifactId,parent.artifactId,JSON.stringify(source),key,hash,bytes,NOW()];
  try {
    await env.DB.prepare('INSERT OR IGNORE INTO quant_model_functions(id,owner,request_id,request_hash,name,artifact_id,parent_artifact_id,source_ref,object_key,object_sha256,byte_length,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)').bind(...args).run();
  } catch (error) {const row=await prior();if(row)return reply(row,true);throw error;}
  const saved=await prior();
  if (!saved) fail('保存尚未确认；请使用同一请求标识重试','FUNCTION_SAVE_UNCONFIRMED',503);
  return reply(saved,saved.id!==id);
}
export async function modelFunctionsApi(req,env,path,owner) {
  if (!path.startsWith('/model-functions')) return null;
  try {
    if (path==='/model-functions/resolve' && req.method==='POST') {
      const input=await body(req,4096);if(!exact(input,['source']))fail('只接受函数来源');
      return json(await resolveFunction(env,owner,input.source));
    }
    if (path==='/model-functions/evaluate' && req.method==='POST') {
      const input=await body(req,1024*1024);
      if (!exact(input,['source','input',...(Object.hasOwn(input,'edits')?['edits']:[])]) || !obj(input.input) || Object.keys(input.input).some(k=>!['rows','currentState','scale'].includes(k)))fail('函数试算输入无效');
      await rate(env,'model-function-eval:'+owner,30,60);
      const {artifact}=await resolveFunction(env,owner,input.source);
      const fn=Object.hasOwn(input,'edits')?await deriveFunction(artifact,input.edits):artifact;
      return json({result:await evaluateFunction(fn,input.input),scope:fn.scope,inferenceOnly:true,newValidationPerformed:false});
    }
    if (path==='/model-functions/derive' && req.method==='POST')return json(await derive(env,owner,await body(req,65536)));
    if (path==='/model-functions' && req.method==='GET') {
      const p=new URL(req.url).searchParams,offset=Number(p.get('offset')??0);
      if(!Number.isInteger(offset)||offset<0||offset>100000)fail('分页位置无效');
      const rows=(await env.DB.prepare('SELECT * FROM quant_model_functions WHERE owner=? ORDER BY created_at DESC,id DESC LIMIT 51 OFFSET ?').bind(owner,offset).all()).results;
      return json({items:rows.slice(0,50).map(item),nextOffset:rows.length>50?offset+50:null});
    }
    const match=/^\/model-functions\/([a-f0-9-]{36})$/.exec(path);
    if(match && req.method==='GET')return json(await resolveFunction(env,owner,{functionId:match[1],artifactId:new URL(req.url).searchParams.get('artifactId')}));
    fail('函数接口不存在','NOT_FOUND',404);
  } catch(error) {if(error instanceof ModelFunctionError)throw new ApiError(error.code,error.message);throw error;}
}
