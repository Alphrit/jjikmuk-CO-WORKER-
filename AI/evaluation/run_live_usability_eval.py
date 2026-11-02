"""Opt-in real OpenAI usability journeys; run from AI/ with --live.

Uses synthetic questions and memory stores. Measures HTTP and expected sources
separately; open-ended follow-up and recipe responses require transcript review.
No API calls are made on import or without the explicit --live flag.
"""

import argparse


def source_expectation_failures(data, expected_docs, citations, mode):
    """None skips source checks; [] requires no sources and no external AI.

    An out-of-domain negative control must not be required to use RAG.
    """
    if expected_docs is None:
        return []
    failures = []
    if set(data.get('sources', [])) != {citations[d] for d in expected_docs}:
        failures.append('source_mismatch')
    if expected_docs:
        if data.get('answer_source') != 'rag':
            failures.append('rag_not_used')
        if mode == 'extractive':
            from app.services.rag_grounding import answer_matches_reviewed_sources
            if not answer_matches_reviewed_sources(data.get('answer', ''), data.get('sources', [])):
                failures.append('excerpt_mismatch')
    elif data.get('external_ai_processing', {}).get('used'):
        failures.append('unnecessary_external_call')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow billed OpenAI calls with synthetic questions")
    parser.add_argument("--output", default="evaluation/results/ux_live_journeys.json")
    parser.add_argument("--journeys", help="JSON journeys: [name, [[question, expected_docs, optional_checks], ...]]")
    parser.add_argument("--mode", choices=("extractive", "generated"), default="generated",
                        help="Verify retrieval/excerpts separately from generated answers")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this evaluator makes actual OpenAI calls")
    import json, os, sys, secrets, time, logging
    from pathlib import Path
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from dotenv import load_dotenv
    sys.path.insert(0,str(Path.cwd()))
    load_dotenv('.env',override=True)
    os.environ.update(FOOD_CHATBOT_ENV='development',FOOD_CHATBOT_DB_PROVIDER='memory',FOOD_CHATBOT_ENABLE_CONVERSATIONS='false',FOOD_CHATBOT_ENABLE_RATE_LIMIT='false')
    logging.basicConfig(level=logging.ERROR)
    from fastapi.testclient import TestClient
    from app.api import main as api
    from app.infrastructure import llm, rag
    from app.infrastructure.external_ai_consent import CONSENT_HEADER,TIMESTAMP_HEADER,NONCE_HEADER,SIGNATURE_HEADER,USER_ID_HEADER,sign_external_ai_consent
    from app.infrastructure.llm_trace import capture_llm_calls
    from app.services.knowledge_preview import preview_released_sources
    from evaluation.run_conversation_usability_eval import check_response
    report={'run_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), 'model':llm.get_llm_model(),'embedding':rag._get_index_settings()['embedding_model'],'scope':'Real OpenAI and existing Chroma index, in-process HTTP, synthetic questions, memory repositories','cases':[]}
    target=Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    original=api._run_chat_pipeline
    observed=[]
    def pipeline(*a,**kw):
        result=original(*a,**kw)
        obj=result[0]
        observed.append({'fallback_reason':obj.fallback_reason,'validation_failure_types':obj.validation_failure_types,'answer_source':obj.answer_source})
        return result
    api._run_chat_pipeline=pipeline
    citations={e['source'].split(':')[-1]:e['citation'] for e in preview_released_sources()}


    def request(client,case_id,payload,mode='extractive',expected_docs=None,expected_intent=None,consent=True,recipe=False,checks=(),first=None):
        os.environ['RAG_GENERAL_RESPONSE_MODE']=mode
        body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        headers={'Content-Type':'application/json'}
        token=os.getenv('FOOD_CHATBOT_API_TOKEN','').strip()
        if token: headers['X-Food-Chatbot-Token']=token
        if consent:
            stamp,nonce,user=int(time.time()),secrets.token_urlsafe(24),'synthetic-comprehensive-audit'
            sig=sign_external_ai_consent(secret=os.environ['FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET'],timestamp=stamp,method='POST',path='/chat',user_id=user,consent='granted',nonce=nonce,body=body)
            headers.update({CONSENT_HEADER:'granted',TIMESTAMP_HEADER:str(stamp),NONCE_HEADER:nonce,SIGNATURE_HEADER:sig,USER_ID_HEADER:user})
        start=time.perf_counter(); observed.clear()
        with capture_llm_calls() as calls:
            response=client.post('/chat',content=body,headers=headers)
        data=response.json(); internal=observed[-1] if observed else {}
        result={'id':case_id,'expected_docs':expected_docs,'request':payload,'response':data,'question':payload['message'],'mode':mode,'http':response.status_code,'elapsed_ms':round(1000*(time.perf_counter()-start)), 'intent':data.get('intent'),'answer_source':data.get('answer_source'),'answer':data.get('answer'),'sources':data.get('sources',[]),'recipe':data.get('recipe'),'external_ai_processing':data.get('external_ai_processing'),'llm_calls':calls,**internal}
        failures=[]
        if response.status_code!=200: failures.append('http_failure')
        if not data.get('answer'): failures.append('empty_answer')
        if expected_intent and data.get('intent')!=expected_intent: failures.append('intent_mismatch')
        failures.extend(source_expectation_failures(data, expected_docs, citations, mode))
        if recipe and not data.get('recipe'): failures.append('missing_recipe')
        if not consent and (calls or data.get('external_ai_processing',{}).get('used')): failures.append('consent_boundary_failure')
        for check in checks:
            if not check_response(check, data, first or data):
                failures.append(check['description'])
        result['checks'] = checks
        result['failures']=failures; result['functional_passed']=not failures
        result['generated_adopted']=internal.get('fallback_reason')=='rag_generated_answer_verified' if mode=='generated' and not recipe else None
        report['cases'].append(result)
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if failures or mode=='generated' or len(report['cases'])%10==0:
            print(json.dumps({k:result[k] for k in ['id','http','elapsed_ms','functional_passed','generated_adopted','failures','fallback_reason'] if k in result},ensure_ascii=False),flush=True)
        return data

    journeys=[
     ('sugar',[('슈가프리랑 설탕을 넣지 않았다는 건 어떻게 달라?',['sugar-zero-vs-added']),('아 그러면 칼로리도 없는 거네?',['sugar-sweeteners-calories']),('열량도 0이라는 말이 맞아?',['sugar-sweeteners-calories']),('방금 설명 한 문장으로 정리해줘',None)]),
     ('facility',[('다른 제품이랑 제조 공장이 같대. 무슨 의미야?',['allergy-facility-warning']),('그럼 원재료에 들어갔다는 건 아니지?',['allergy-facility-warning']),('어려워. 초보자도 알게 한 문장만',None),('얼마나 섞였는지 %로 알려줘',None)]),
     ('topic',[('무가당 표시는 무슨 의미야?',['sugar-no-added-reading']),('이제 나트륨하고 소금 차이만 설명해줘',['sodium-and-salt']),('그거 짧게 다시 말해줄래?',['sodium-and-salt']),('비건이면 우유 표시를 안 봐도 된다는 뜻이야?',['vegan-and-allergens'])]),
     ('units',[('나트륨이랑 소금은 같은 건가? 한 줄로',['sodium-and-salt']),('소금 얘기는 됐고, g하고 mg는 뭐가 달라?',['nutrition-mass-units'])]),
     ('recipe',[('땅콩 없이 간단한 저염 레시피 알려줘',None),('방금 레시피에서 조리 순서만 보여줘',None),('2인분으로 늘려줄 수 있어?',None),('다른 레시피 추천해줘',None)])]
    if args.journeys:
        journeys = json.loads(Path(args.journeys).read_text(encoding='utf-8'))
    try:
     with TestClient(api.app) as client:
        for name,turns in journeys:
            history=[];state={};first=None
            for i,turn in enumerate(turns):
                question,docs=turn[:2]
                checks=turn[2] if len(turn)>2 else []
                payload={'message':question,'chat_history':list(history),'conversation_state':state}
                if name=='recipe':payload['profile']={'profile_id':991,'nickname':'검증용','allergies':['땅콩'],'conditions':[],'special_diets':[]}
                result=request(client,name+':'+str(i),payload,mode=args.mode,expected_docs=docs,checks=checks,first=first)
                if first is None:first=result
                history.extend([{'role':'user','content':question},{'role':'assistant','content':result.get('answer','')}]);state=result.get('conversation_state',{})
        request(client,'consent_withdrawn',{'message':'무가당 표시는 무슨 의미야?'},mode=args.mode,consent=False)
     report['summary']={'total':len(report['cases']),'http_ok':sum(c['http']==200 for c in report['cases']),'source_checked':sum(c['expected_docs'] is not None for c in report['cases']),'source_passed':sum(c['expected_docs'] is not None and c['functional_passed'] for c in report['cases']),'failed_ids':[c['id'] for c in report['cases'] if not c['functional_passed']]}
    except Exception as e:report['error_type']=type(e).__name__
    finally:
     api._run_chat_pipeline=original
     target.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'summary':report.get('summary'),'error_type':report.get('error_type')},ensure_ascii=False),flush=True)
    return 1 if report.get("error_type") or any(not c["functional_passed"] for c in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
