import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {researchPresets} from './presets.mjs';
const root=path.resolve(import.meta.dirname,'..');
const types={'.html':'text/html; charset=utf-8','.js':'application/javascript; charset=utf-8','.mjs':'application/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.json':'application/json; charset=utf-8'};
const assets={};for(const file of await fs.readdir(path.join(root,'web'))){const type=types[path.extname(file)];if(type)assets[file]={type,body:await fs.readFile(path.join(root,'web',file),'utf8')};}
if(!assets['index.html'])throw new Error('Frontend index.html missing');
for(const file of Object.keys(assets).filter(x=>/\.(js|css)$/.test(x)))if(assets[file]){const revision=crypto.createHash('sha256').update(assets[file].body).digest('hex').slice(0,12);assets['index.html'].body=assets['index.html'].body.replace(new RegExp(file.replace('.', '\\.')+'(?:\\?[^\"\']*)?(?=[\"\'])','g'),file+'?v='+revision);}
const catalog=JSON.parse(await fs.readFile(path.join(root,'engine/atlas_quant/catalog.json'),'utf8'));
const defaultSymbols=['000001.SZ','000002.SZ','600000.SH','600036.SH','600519.SH','000333.SZ','000651.SZ','600900.SH'];
const strategies=[['balanced','多因子 · 自动模型研究','组合动量与波动，比较线性和非线性模型',['momentum_20','volatility_20']],['momentum','动量轮动','检验过去收益与未来收益的关系',['momentum_20']],['defensive','低波动研究','研究波动和风险调整后的表现',['volatility_20']]];
catalog.templates=strategies.map(([id,name,description,ids])=>({id,name,description,strategy:{schemaVersion:1,name,universe:{symbols:defaultSymbols,start:'20220101',end:'20260930'},factors:ids.map(id=>catalog.factors.find(f=>f.id===id)).filter(Boolean).map(f=>({id:f.id,expression:f.expression,direction:f.direction})),preprocess:{winsorize:true,standardize:true},model:{mode:'auto',candidates:['factor_score','ridge','elastic_net','hist_gradient_boosting'],horizon:5,metric:'rank_ic'},portfolio:{topN:3,maxWeight:.4,rebalanceDays:5,initialCapital:1000000},costs:{commissionBps:3,slippageBps:10,sellTaxBps:5},graph:{nodes:[],edges:[]}}}));
const validation=(await fs.readFile(path.join(root,'edge/validation.mjs'),'utf8')).replace(/^import .* from '\.\/universe\.mjs';\n/,'');
const worker=(await fs.readFile(path.join(root,'edge/worker.mjs'),'utf8')).replace(/^import .* from '\.\/validation\.mjs';\n/,'');
const studio=await fs.readFile(path.join(root,'edge/studio.mjs'),'utf8');
const universe=await fs.readFile(path.join(root,'edge/universe.mjs'),'utf8');
const presets=researchPresets(catalog,catalog.templates[0].strategy);catalog.templates=presets.strategies;
const id='0.3.0-'+crypto.createHash('sha256').update(JSON.stringify(assets)+JSON.stringify(catalog)+validation+worker+universe+studio+JSON.stringify(presets)).digest('hex').slice(0,12);
const code=`const WEB_ASSETS=${JSON.stringify(assets)};\nconst CATALOG=${JSON.stringify(catalog)};\nconst BUILD_ID=${JSON.stringify(id)};\nconst RESEARCH_PRESETS=${JSON.stringify(presets)};\n`+validation+'\n'+universe+'\n'+studio+'\n'+worker;
await fs.mkdir(path.join(root,'dist'),{recursive:true});await fs.writeFile(path.join(root,'dist/worker.mjs'),code);await fs.writeFile(path.join(root,'dist/build.json'),JSON.stringify({id,sha256:crypto.createHash('sha256').update(code).digest('hex'),bytes:Buffer.byteLength(code)},null,2)+'\n');console.log(JSON.stringify({build:id,bytes:Buffer.byteLength(code),factors:catalog.factors.length,templates:catalog.templates.length}));
