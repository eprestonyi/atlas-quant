#!/usr/bin/env python3
"""Read retained artificial components; measure lossless repeated evidence, no F/admission."""
import json,sys,os
from pathlib import Path
import hashlib

root=Path(sys.argv[1]).resolve()
def enc(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(x):return hashlib.sha256(enc(x)).hexdigest()
all_panels={};dependencies={};calendars={};total_dep_bytes=total_calendar_bytes=0;event_bytes=compact_event_bytes=0;events=0
parts=[]
for name in ['prepared-0.json','prepared-1.json']:
 value=json.loads((root/name).read_bytes());parts.append({'name':name,'parts':{k:len(enc(v)) for k,v in value.items()}})
 for row in value['panel']:all_panels[row['ts_code'],row['trade_date']]=row
 for event in value['stateEvents']:
  events+=1;event_bytes+=len(enc(event));compact=json.loads(enc(event));r=compact['result'];refs=[]
  for dep in r['dependencies']:
   raw=enc(dep);key=hashlib.sha256(raw).hexdigest();total_dep_bytes+=len(raw);dependencies[key]=dep;refs.append(key)
  r['dependencies']=refs
  cal=r['calendar']
  if cal is not None:
   key=digest(cal);total_calendar_bytes+=len(enc(cal));calendars[key]=cal;r['calendar']=key
  compact_event_bytes+=len(enc(compact))
market=json.loads((root/'source-market-snapshot.json').read_bytes())['rows']
joined_bytes=2
for index,row in enumerate(market):
 merged={**row,**all_panels[row['ts_code'],row['trade_date']]}
 joined_bytes+=len(enc(merged))+(1 if index else 0)
result={'status':'MEASURED_NOT_ADMITTED','modelsFitted':False,'providerCalls':0,
 'preparedParts':parts,'events':events,'uniqueDependencyObjects':len(dependencies),
 'repeatedDependencyBytes':total_dep_bytes,'uniqueDependenciesDictionaryBytes':len(enc(dependencies)),
 'repeatedCalendarBytes':total_calendar_bytes,'uniqueCalendarObjects':len(calendars),'calendarDictionaryBytes':len(enc(calendars)),
 'expandedEventObjectBytes':event_bytes,'proposedReferencedEventObjectBytes':compact_event_bytes,
 'proposedLosslessEventGraphBytesLowerBound':compact_event_bytes+len(enc(dependencies))+len(enc(calendars)),
 'joinedNumericRowCount':len(market),'joinedNumericRowArrayBytes':joined_bytes,
 'unchangedJoinedDocumentBudgetBytes':24*1024**2,'unchangedClosureBudgetBytes':64*1024**2,
 'notes':['Measurements only; no serializer, decoder, admission or model run is bypassed.',
          'Referenced event sizes are a proposal lower bound; descriptors, indices, provenance and reconstruction checks remain to implement.',
          'Joined rows omit outer document and full provenance, so this is a lower bound on actual researchRows bytes.']}
with (root/'deduplication-analysis.json').open('x') as f:
 os.chmod(root/'deduplication-analysis.json',0o600);json.dump(result,f,indent=2)
print(json.dumps(result))
