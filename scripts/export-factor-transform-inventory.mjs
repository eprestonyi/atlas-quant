/** Read-only source inventory; no market data request or fitting. Output is evidence, not coverage. */
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const path=fileURLToPath(new URL('../engine/atlas_quant/catalog.json',import.meta.url));
const bytes=fs.readFileSync(path), catalog=JSON.parse(bytes), fields=new Map(catalog.fieldRegistry.map(x=>[x.id,x]));
const items=catalog.factors.map(factor=>({
  id:factor.id,expression:factor.expression,direction:factor.direction,family:factor.family,category:factor.category,
  catalogStatus:factor.status,automaticProcessing:factor.automaticProcessing || null,historyStatus:factor.historyStatus || null,
  requiredFields:(factor.requiredFields || []).map(id=>{
    const field=fields.get(id);
    return field ? {id,unit:field.unit,provider:field.source,dataset:field.dataset,availabilityStatus:field.availabilityStatus,availability:field.availability,revisionHistoryVerified:field.revisionHistoryVerified} : {id,registered:false};
  })
}));
const result={schema:'factor-transform-inventory/1',catalogSha256:createHash('sha256').update(bytes).digest('hex'),factorCount:items.length,dataObserved:false,numericalParityVerified:false,items};
const outputIndex=process.argv.indexOf('--output');
if(outputIndex>=0){
  const output=process.argv[outputIndex+1];if(!output)throw Error('--output needs a path');
  fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify({output,factorCount:items.length,catalogSha256:result.catalogSha256,dataObserved:false}));
}else process.stdout.write(JSON.stringify(result,null,2)+'\n');
