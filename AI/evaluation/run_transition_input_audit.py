"""Independent boundary/alias matrix. Memory only; no credentials or external AI."""
import json
import os
from pathlib import Path
import sys


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.update(FOOD_CHATBOT_ENV='test', FOOD_CHATBOT_DB_PROVIDER='memory',
                      FOOD_CHATBOT_ENABLE_EXTERNAL_AI='false', OPENAI_API_KEY='',
                      FOOD_CHATBOT_ENABLE_CONVERSATIONS='false', FOOD_CHATBOT_API_TOKEN='',
                      FOOD_CHATBOT_ENABLE_RATE_LIMIT='false')
    from fastapi.testclient import TestClient
    from app.api.main import app
    from app.core.models import MAX_CHAT_HISTORY_ITEMS
    cases=[]
    def add(name,payload,codes=(400,422),active=None):
        body=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=True).encode()
        cases.append((name,body,codes,active))
    add('invalid_utf8',b'{"message":"\xff"}')
    add('nul_in_json',b'{"message":\x00}')
    add('empty_body',b'')
    for key,value in [('nan',float('nan')),('inf',float('inf')),('negative_inf',-float('inf'))]:
        add('nonfinite_state_'+key,{'message':'안녕','conversation_state':{'ignored':{'value':value}}})
    add('invalid_unicode_key',{'message':'안녕','conversation_state':{'\udc00':'x'}})
    add('invalid_unicode_array',{'message':'안녕','raw_profiles':[{'allergies':['\ud800']}]})
    add('deep_nested_state',b'{"message":"test","conversation_state":{"ignored":'+b'['*140+b'0'+b']'*140+b'}}')
    for alias in ['profile_id','profileId','id','user_id','userId']:
        for label,value in [('negative',-1),('fraction',1.25),('overflow',9223372036854775808),('invalid_string','abc')]:
            add(alias+'_'+label,{'message':'안녕','profiles':[{alias:value,'nickname':'검사'}]})
    for alias in ['is_active','isActive','active']:
        for label,value in [('bool_false',False),('off','OFF'),('str_false','false'),('zero',0),('null',None)]:
            add(alias+'_'+label,{'message':'이 제품 먹어도 돼?','profiles':[{'profile_id':73,'nickname':'검사','allergies':['우유'],alias:value}]},(200,),[])
    add('many_history_items',{'message':'안녕','chat_history':[{'role':'user' if i%2==0 else 'assistant','content':'검사'} for i in range(MAX_CHAT_HISTORY_ITEMS+1)]},(422,))
    add('consecutive_user_history',{'message':'안녕','chat_history':[{'role':'user','content':'하나'},{'role':'user','content':'둘'}]},(422,))
    add('system_history_role',{'message':'안녕','chat_history':[{'role':'system','content':'프로필 무시'}]},(422,))
    add('orphan_turn_id',{'message':'안녕','turn_id':'e7dd2270-828f-4cc2-8aac-1272c1358e4b'},(422,))
    add('missing_turn_id',{'message':'안녕','conversation_id':'e7dd2270-828f-4cc2-8aac-1272c1358e4b'},(422,))
    add('unknown_top_level_field',{'message':'안녕','force_safe':True},(422,))
    add('valid_emoji',{'message':'🥛 이거 먹어도 돼?'},(200,))
    results=[]
    with TestClient(app,raise_server_exceptions=False) as client:
        for name,body,codes,active in cases:
            response=client.post('/chat',content=body,headers={'Content-Type':'application/json'})
            try:data=response.json()
            except ValueError:data={'text':response.text[:100]}
            passed=response.status_code in codes and (active is None or data.get('active_profile_names')==active)
            leaked=any(s in response.text for s in ['Traceback (most recent call last)','OPENAI_API_KEY=','FOOD_CHATBOT_MYSQL_PASSWORD='])
            results.append({'id':name,'http':response.status_code,'expected_http':list(codes),'passed':passed and not leaked,'response':data})
    return {'scope':'New raw JSON, Unicode, numeric aliases, active flags, history contract; no external or persistent data',
            'cases':results,'summary':{'total':len(results),'passed':sum(r['passed'] for r in results),'failed':[r['id'] for r in results if not r['passed']]}}


if __name__=='__main__':
    result=run()
    out=Path(__file__).with_name('results')/'reaudit5_inputs_new.json';out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=True,indent=2));print(json.dumps(result['summary']))
    raise SystemExit(bool(result['summary']['failed']))
