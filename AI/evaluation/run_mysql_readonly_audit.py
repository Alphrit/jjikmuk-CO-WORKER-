"""Opt-in local MySQL read-only audit; run from AI/ with --read-only.

Only product/schema reads use MySQL. Chat audit writes go to memory. No real
conversation writes or schema migrations are performed. Configuration and
credential values are never printed; only readiness codes enter the report.
"""

import argparse


def extended_conversation_checks(cases):
    """Check saved responses against the queried product, not fixture constants."""
    nutrition = (cases['holdout_real_first'].get('current_product') or {}).get('nutrition', {})
    answer = cases['holdout_real_all_off_facts'].get('answer', '')
    mass_facts = all(
        isinstance(nutrition.get(key), (int, float))
        and label in answer
        and f"{nutrition[key]:g}mg" in answer
        for key, label in (('sodium', '나트륨'), ('cholesterol', '콜레스테롤'))
    )
    father = cases['holdout_real_renamed_father']
    return {
        'all_off_still_returns_mass_facts': mass_facts,
        'off_person_gets_selection_guidance': '엄마' in cases['holdout_real_polite_off_subject']['answer'] and 'ON' in cases['holdout_real_polite_off_subject']['answer'],
        'renamed_active_profile_evaluates_current_product': father['current_product'] is not None and father['intent'] == 'can_i_eat',
        'polite_reset_drops_product': cases['holdout_real_after_polite_reset']['current_product'] is None,
        'drinking_symptom_routes_to_emergency': cases['holdout_real_drinking_emergency']['intent'] == 'emergency_safety',
        'negated_symptom_not_emergency': cases['holdout_real_negated_emergency']['intent'] != 'emergency_safety',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="store_true", help="Explicitly allow real local MySQL reads")
    parser.add_argument("--output", default="evaluation/results/mysql_readonly_audit.json")
    parser.add_argument("--extended", action="store_true", help="Add new searches and real-product conversation sequences")
    args = parser.parse_args()
    if not args.read_only:
        parser.error("--read-only is required to access the configured MySQL server")
    import os,sys,json,time,logging
    from pathlib import Path
    from dotenv import load_dotenv
    sys.path.insert(0,str(Path.cwd()))
    load_dotenv('.env',override=True)
    logging.basicConfig(level=logging.ERROR)
    from app.api import main as api
    from app.core.models import ChatRequest
    from app.infrastructure.repository import MySQLProductRepository,MySQLActivityRepository,InMemoryActivityRepository,dispose_mysql_connection_pools
    from app.infrastructure.conversations import MySQLConversationRepository
    from app.services.product import set_product_repository
    from app.services.activity import set_activity_repository
    cfg=dict(host=os.getenv('FOOD_CHATBOT_MYSQL_HOST','localhost'),port=int(os.getenv('FOOD_CHATBOT_MYSQL_PORT','3306')),database=os.getenv('FOOD_CHATBOT_MYSQL_DATABASE','jjikmuk'),user=os.getenv('FOOD_CHATBOT_MYSQL_USER','root'),password=os.getenv('FOOD_CHATBOT_MYSQL_PASSWORD',''))
    p=MySQLProductRepository(**cfg,table_name=os.getenv('FOOD_CHATBOT_PRODUCT_TABLE','products'))
    a=MySQLActivityRepository(**cfg,table_name=os.getenv('FOOD_CHATBOT_HISTORY_TABLE','histories'),chat_log_table_name=os.getenv('FOOD_CHATBOT_CHAT_LOG_TABLE','chatbot_logs'),enable_legacy_history_writes=False)
    c=MySQLConversationRepository(**cfg)
    report={'scope':'Actual local MySQL product reads and schema checks; all chat audit writes redirected to memory; no external AI calls; synthetic profiles only','settings':{'db_provider':os.getenv('FOOD_CHATBOT_DB_PROVIDER'),'conversations_enabled':api._conversations_enabled()},'current_boundary':api._deployment_boundary_status(),'readiness':{'products':p.readiness_check(),'activity':a.readiness_check(),'conversations':c.readiness_check()},'cases':[]}
    with a._connect() as connection:
        report['missing_audit_columns'] = sorted(a.CHATBOT_LOG_REQUIRED_COLUMNS - a._table_columns(connection, a.quoted_chat_log_table_name))
    old=os.environ.get('FOOD_CHATBOT_ENV');os.environ['FOOD_CHATBOT_ENV']='production';report['production_boundary_with_current_values']=api._deployment_boundary_status()
    if old is None:os.environ.pop('FOOD_CHATBOT_ENV',None)
    else:os.environ['FOOD_CHATBOT_ENV']=old
    try:
        api.configure_repositories();report['current_startup']={'passed':True}
    except Exception as e:
        report['current_startup']={'passed':False,'error_type':type(e).__name__,'audit_schema_blocker':'MySQL chatbot audit schema is not ready' in str(e)}
    os.environ['FOOD_CHATBOT_ENABLE_EXTERNAL_AI']='false'
    set_product_repository(p);set_activity_repository(InMemoryActivityRepository())
    report['search_index']=p.search_index_status()

    def save():
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))

    def run(name,payload):
        start=time.perf_counter()
        try:
            response=api.chat(ChatRequest.model_validate(payload))
            d=response.model_dump(mode='json');case={'id':name,'elapsed_ms':round(1000*(time.perf_counter()-start)), 'intent':d['intent'],'decision':d['overall_decision'],'allergy_status':d['allergy_status'],'answer':d['answer'],'product_data_trust':d.get('product_data_trust'),'missing_information':d.get('missing_information'),'recommendations':d['recommended_products'],'candidates':d['product_candidates'],'current_product':d['current_product'],'state':d['conversation_state']}
        except Exception as e:case={'id':name,'error_type':type(e).__name__}
        report['cases'].append(case);save();print(json.dumps({k:case[k] for k in ('id','elapsed_ms','intent','decision','allergy_status','error_type') if k in case},ensure_ascii=False),flush=True)
        return case
    profiles={'profile_id':991,'nickname':'가상 테스트','allergies':['우유'],'special_diets':[],'conditions':[]}

    try:
        available=[]
        for term in ('매일우유','삼육두유','오레오','아몬드브리즈','포카칩','코카콜라','햇반','미등록검사식품UX991','불닭볶음면','서울우유','초코파이','삼다수'):
            started=time.perf_counter();rows=p.search_candidates(term,limit=3)
            report['cases'].append({'id':'new_search:'+term,'elapsed_ms':round(1000*(time.perf_counter()-started)),'count':len(rows),'products':[{'name':r.product_name,'barcode':r.barcode,'report_number':r.report_number} for r in rows]})
            print(json.dumps({'search':term,'count':len(rows)},ensure_ascii=False),flush=True)
            available.extend(r for r in rows if r.barcode)
        if available:
            obj=available[0];ref={'barcode':obj.barcode}
            first=run('new_real_product_assessment',{'message':'이 제품 먹어도 돼?','profiles':[profiles],**ref})
            run('new_real_multi_nutrient_request',{'message':'나트륨과 단백질은 각각 얼마야?','profiles':[profiles],**ref})
            run('new_real_compound_request',{'message':'원재료와 영양성분 둘 다 보여줘','profiles':[profiles],**ref})
            run('new_real_profile_switch',{'message':'그럼 먹어도 돼?','profiles':[{**profiles,'is_active':False},{'profile_id':992,'nickname':'다른 가상 대상','allergies':['땅콩'],'conditions':[],'special_diets':[]}],'conversation_state':first['state']})
            run('new_unknown_barcode',{'message':'이 제품 먹어도 돼?','barcode':'NONEXISTENT-UX-991','profiles':[profiles],'conversation_state':first['state']})
            run('new_conflicting_identifiers',{'message':'이 제품 먹어도 돼?','profiles':[profiles],'product':{'barcode':obj.barcode,'report_number':'NONEXISTENT-REPORT-UX991'}})
        run('new_real_off_subject_emoji',{'message':'엄마도🙂','profiles':[{'profile_id':991,'nickname':'엄마','allergies':['우유'],'is_active':False},{'profile_id':992,'nickname':'아빠','allergies':['땅콩']}],'barcode':'8801043035989'})
        run('new_real_fat_and_saturated',{'message':'지방과 포화지방을 각각 보여줘','barcode':'8801121763964'})
        run('new_real_emergency',{'message':'먹고 숨이 안 쉬어져','barcode':'8801043035989'})
        run('mass_percent_separation', {'message':'나트륨과 콜레스테롤 수치 알려줘', 'barcode':'8801121763964'})
        first=run('new_real_ambiguous_search',{'message':'아몬드브리즈 먹어도 돼?','profiles':[profiles]})
        if first.get('candidates'):
            run('new_real_candidate_correction',{'message':'아니 두 번째 걸로','profiles':[profiles],'conversation_state':first['state']})
            run('new_real_candidate_last',{'message':'마지막 거','profiles':[profiles],'conversation_state':first['state']})
            run('new_real_candidate_control',{'message':'두 번째','profiles':[profiles],'conversation_state':first['state']})
        if args.extended:
            for term in ('매일 우유','새우깡','포카칩 오리지널','무가당 두유','서울우유 흰우유','연세우유','삼다수 500ml','유기농 두유'):
                started=time.perf_counter();rows=p.search_candidates(term,limit=3)
                report['cases'].append({'id':'holdout_search:'+term,'elapsed_ms':round(1000*(time.perf_counter()-started)),
                    'count':len(rows),'products':[{'name':r.product_name,'barcode':r.barcode,'report_number':r.report_number} for r in rows]})
            mom={'profile_id':981,'nickname':'엄마','allergies':['우유'],'is_active':True}
            dad={'profile_id':982,'nickname':'아빠','allergies':['땅콩'],'is_active':True}
            real=run('holdout_real_first',{'message':'이 제품 먹어도 돼?','barcode':'8801121763964','profiles':[mom]})
            state=real.get('state',{})
            run('holdout_real_all_off_facts',{'message':'나트륨과 콜레스테롤 알려줘','profiles':[],'conversation_state':state})
            run('holdout_real_polite_off_subject',{'message':'엄마 것도 확인해 줘','profiles':[{**mom,'is_active':False},dad],'conversation_state':state})
            run('holdout_real_renamed_father',{'message':'아버지는 먹어도 될까요?','profiles':[{**mom,'is_active':False},{**dad,'nickname':'아버지'}],'conversation_state':state})
            reset=run('holdout_real_polite_reset',{'message':'처음부터 다시 시작해 주세요','profiles':[mom],'conversation_state':state})
            run('holdout_real_after_polite_reset',{'message':'그 제품 먹어도 돼?','profiles':[mom],'conversation_state':reset.get('state',{})})
            run('holdout_real_drinking_emergency',{'message':'우유를 마셨는데 숨이 잘 안 쉬어져요','profiles':[mom],'conversation_state':state})
            run('holdout_real_negated_emergency',{'message':'우유 먹고 호흡곤란이 전혀 없어요','profiles':[mom],'conversation_state':state})
    finally:
        dispose_mysql_connection_pools();save();print(json.dumps({'cases':len(report['cases']),'scope':report['scope']},ensure_ascii=False),flush=True)

    cases = {item['id']: item for item in report['cases']}
    candidates = cases['new_real_ambiguous_search']['candidates']
    percent_product = cases['mass_percent_separation']['current_product']
    report['verification_checks'] = {
        'sodium_mass_retained': percent_product['nutrition']['sodium'] == 125,
        'mass_not_percent': percent_product['nutrition_percent']['sodium'] is None and percent_product['nutrition_percent']['cholesterol'] is None,
        'second_candidate_selected': cases['new_real_candidate_correction']['current_product']['barcode'] == candidates[1]['barcode'],
        'last_candidate_selected': cases['new_real_candidate_last']['current_product']['barcode'] == candidates[-1]['barcode'],
        'compound_missing_fields_disclosed': '원재료' in cases['new_real_compound_request']['answer'] and '영양성분' in cases['new_real_compound_request']['answer'] and '질문 뜻을 아직' not in cases['new_real_compound_request']['answer'],
        'off_subject_not_replaced_by_active_profile': (
            cases['new_real_off_subject_emoji']['allergy_status'] == 'unknown'
            and '엄마' in cases['new_real_off_subject_emoji']['answer']
            and 'ON' in cases['new_real_off_subject_emoji']['answer']
        ),
        'emergency_before_product_lookup': cases['new_real_emergency']['intent'] == 'emergency_safety',
        'no_case_exceptions': all(not item.get('error_type') for item in report['cases']),
    }
    if args.extended:
        report['new_conversation_checks'] = extended_conversation_checks(cases)
    save()
    print(json.dumps(report['verification_checks'], ensure_ascii=False), flush=True)
    if args.extended:
        print(json.dumps(report['new_conversation_checks'],ensure_ascii=False),flush=True)
    raise SystemExit(0 if all(report['verification_checks'].values()) and all(report.get('new_conversation_checks',{}).values()) else 1)


if __name__ == "__main__":
    main()
