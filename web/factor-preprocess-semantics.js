import fieldRegistry from '../engine/atlas_quant/factor_quantity_fields.json' with {type:'json'};
import {parseExpression} from '../edge/factor-language.mjs';
import {ECONOMIC_TRANSFORMS, validEconomicTransform} from './factor-preprocess-contract.js';
const quantity=(kind,unit)=>({kind,unit});
const RATIO=quantity('ratio','1'), DERIVED=quantity('derived','1'), UNKNOWN=quantity('custom_numeric','declared_numeric'), INVALID=quantity('invalid','incompatible_units');
const same=(x,y)=>x.kind===y.kind&&x.unit===y.unit;
const unknown=x=>same(x,UNKNOWN), invalid=x=>same(x,INVALID);
const dimensionless=x=>['ratio','derived'].includes(x.kind)&&['1','ratio'].includes(x.unit);
const sameUnits=(x,y)=>x.unit===y.unit&&!unknown(x)&&!unknown(y);
const literal=(node,value)=>node.kind==='number'&&node.literal===value;
const signedKinds=['price','positive_size','nonnegative_flow'];
const signed=value=>signedKinds.includes(value.kind)?quantity('derived',value.unit):value;
const numericLiteral=node=>{if(node.kind==='number')return node.literal;if(node.kind==='unary'){const value=numericLiteral(node.operand);return value===null?null:node.operator==='-'?-value:value;}return null;};
const scaled=(q,value)=>value!==null&&value<=0?signed(q):q;
const fieldQuantity=name=>{const entry=fieldRegistry.fields[name];return entry?quantity(entry.kind,entry.unit):UNKNOWN;};
export function expressionQuantity(node) {
  if(node.kind==='field')return fieldQuantity(node.name);
  if(node.kind==='number')return RATIO;
  if(node.kind==='unary'){const value=expressionQuantity(node.operand);return node.operator==='-'?signed(value):value;}
  if(node.kind==='binary'){
    const left=expressionQuantity(node.left),right=expressionQuantity(node.right);
    if(invalid(left)||invalid(right))return INVALID;
    if(['+','-'].includes(node.operator)){
      if(literal(node.left,0))return node.operator==='-'?signed(right):right;
      if(literal(node.right,0))return left;
      if(dimensionless(left)&&dimensionless(right))return RATIO;
      if(sameUnits(left,right)){
        if(node.operator==='-'&&left.kind==='log_price'&&right.kind==='log_price')return RATIO;
        if(node.operator==='-'&&!['percent','currency_per_share','currency_amount'].includes(left.kind))return quantity('derived',left.unit);
        return left.kind===right.kind?left:quantity('derived',left.unit);
      }
      return unknown(left)||unknown(right)?UNKNOWN:INVALID;
    }
    if(node.operator==='/'){
      if(numericLiteral(node.right)===0)return INVALID;
      if(sameUnits(left,right)||dimensionless(left)&&dimensionless(right))return RATIO;
      if(dimensionless(left)&&right.kind==='valuation_multiple')return RATIO;
      if(left.kind==='percent'&&literal(node.right,100))return RATIO;
      if(dimensionless(right))return numericLiteral(node.right)===0?INVALID:scaled(left,numericLiteral(node.right));
      return unknown(left)||unknown(right)?UNKNOWN:dimensionless(left)?quantity('derived','1/'+right.unit):INVALID;
    }
    if(node.operator==='*'){
      if(left.kind==='percent'&&literal(node.right,.01)||right.kind==='percent'&&literal(node.left,.01))return RATIO;
      if(dimensionless(left))return scaled(right,numericLiteral(node.left));
      if(dimensionless(right))return scaled(left,numericLiteral(node.right));
      return unknown(left)||unknown(right)?UNKNOWN:INVALID;
    }
  }
  if(node.kind==='call'){
    const values=node.args.map(expressionQuantity),value=values[0],name=node.name;
    if(values.some(invalid))return INVALID;
    if(name==='log'&&value.kind==='price')return quantity('log_price','log_'+value.unit);
    if(name==='log'&&value.kind==='log_price')return INVALID;
    if(['returns','rank','zscore','ts_rank','sign','log'].includes(name))return name==='log'?DERIVED:RATIO;
    if(name==='sqrt')return dimensionless(value)?DERIVED:unknown(value)?UNKNOWN:INVALID;
    if(['min','max'].includes(name)){
      const other=values[1];
      if(node.args[1].kind==='number')return value;
      if(node.args[0].kind==='number')return other;
      return sameUnits(value,other)?value:dimensionless(value)&&dimensionless(other)?RATIO:unknown(value)||unknown(other)?UNKNOWN:INVALID;
    }
    if(['delta','ts_std'].includes(name)){
      if(value.kind==='log_price')return RATIO;
      if(['percent','currency_per_share','currency_amount'].includes(value.kind))return value;
      if(dimensionless(value))return RATIO;
      return quantity('derived',value.unit);
    }
    if(['lag','ts_mean','ts_min','ts_max','ts_sum','abs','clip'].includes(name))return value;
  }
  return UNKNOWN;
}
function defaultTransform(q){
  if(q.kind==='derived'&&!dimensionless(q)&&!q.unit.startsWith('1/'))return null;
  const kind={price:'simple_return',log_price:'first_difference',positive_size:'log_positive',nonnegative_flow:'log1p_nonnegative',valuation_multiple:'reciprocal_nonzero',percent:'percent_to_fraction',currency_per_share:'signed_log1p',currency_amount:'signed_log1p',ratio:'identity',derived:'identity'}[q.kind];
  return kind?ECONOMIC_TRANSFORMS[kind]:null;
}
function visit(node,fn,parent=null){
  fn(node,parent);
  const children=node.kind==='binary'?[node.left,node.right]:node.kind==='unary'?[node.operand]:node.kind==='call'?node.args:[];
  for(const child of children)visit(child,fn,node);
}
export function typedFactorDescriptor(factor,override){
  const parsed=parseExpression(factor.expression),sources=parsed.fields.map(name=>fieldRegistry.fields[name]);
  const global=sources.length>0&&sources.every(x=>x?.sourceIdentity);
  const identities=new Set(sources.map(x=>x?.sourceIdentity));
  const native=global&&identities.size===1&&sources[0].foreign===true;
  const fail=(code,message)=>{const error=Error(message);error.code=code;throw error;};
  if(global)visit(parsed.tree,node=>{if(node.kind==='call'&&['rank','zscore'].includes(node.name))fail('GLOBAL_FACTOR_CROSS_SECTION','全局因子不能在同一天股票之间排名或标准化');});
  const q=expressionQuantity(parsed.tree);
  if(invalid(q))fail('FACTOR_UNIT_MISMATCH','因子表达式混合了不相容的经济单位');
  const transform=override?.transform || defaultTransform(q);
  if(!transform)fail('FACTOR_TRANSFORM_REQUIRED',`因子 ${factor.id} 的经济单位未确定，请在 Studio 选择处理方法`);
  if(!validEconomicTransform(transform))fail('INVALID_AUTOMATIC_PREPROCESSING','因子处理方法无效');
  if(q.kind==='price'&&!['simple_return','log_return','return_over_trailing_volatility'].includes(transform.kind))fail('PRICE_RETURN_REQUIRED','价格输入须转换为收益率');
  if(q.kind==='log_price'&&transform.kind!=='first_difference')fail('PRICE_RETURN_REQUIRED','对数价格须取差分得到对数收益率');
  visit(parsed.tree,(node,parent)=>{
    if(node.kind==='field'&&node.name==='raw_close'&&!(parent?.kind==='binary'&&parent.operator==='/'&&parent.right===node&&expressionQuantity(parent.left).kind==='currency_per_share'))fail('AUTO_FACTOR_REQUIRES_ADJUSTED_PRICE','收益率须使用复权价格；raw_close 只用于显式每股财务估值分母');
  });
  if(factor.role==='event'&&!['identity','percent_to_fraction','signed_log1p','log1p_nonnegative'].includes(transform.kind))fail('EVENT_TRANSFORM_ZERO','事件因子的处理须保留零事件');
  return {feature:'factor:'+factor.id,expression:factor.expression,direction:factor.direction,scope:global?'global':'asset',transform:structuredClone(transform),aggregation:global?'global_once':'signed_origin_dollar_over_gross',economicType:q.kind,sourceUnit:q.unit,clock:native?'observed_source_sessions_asof':'research_sessions'};
}
