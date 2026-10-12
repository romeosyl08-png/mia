"""Team orchestration with usage retained separately for every provider call."""

from . import services
from .billing import credits_for_usage


def _configured_models(models):
    configured = []
    for model in models:
        key = model.provider.keys.filter(is_active=True).order_by('id').first()
        if model.provider.base_url and (key or services.is_local_provider(model.provider)):
            configured.append((model, key.get_key() if key else ''))
    if not configured:
        raise services.ProviderError("Aucun modèle de l'équipe n'est disponible.")
    return configured


def _record_usage(records, model, messages, system_prompt, reply, prompt_tokens, completion_tokens):
    if records is not None:
        estimated_input = services.estimate_cost('', model, system_prompt, messages)[1]
        records.append({
            'model_id': model.model_id,
            'prompt_tokens': prompt_tokens or estimated_input,
            'completion_tokens': completion_tokens or round(len(reply) / 4),
            'credits': credits_for_usage(model, prompt_tokens or estimated_input, completion_tokens or round(len(reply) / 4)),
        })


def _call(model, key, messages, prompt, temperature, max_tokens, top_p, records):
    reply, prompt_tokens, completion_tokens = services.call_llm_provider(
        model.provider, key, model.model_id, messages, prompt, temperature, max_tokens, top_p,
    )
    _record_usage(records, model, messages, prompt, reply, prompt_tokens, completion_tokens)
    return reply, prompt_tokens or 0, completion_tokens or 0


def _stream(model, key, messages, prompt, temperature, max_tokens, top_p, records):
    parts = []
    prompt_tokens = completion_tokens = 0
    try:
        for event in services.stream_llm_provider(model.provider, key, model.model_id, messages, prompt, temperature, max_tokens, top_p):
            if event['type'] == 'delta':
                parts.append(event['content'])
            elif event['type'] == 'usage':
                prompt_tokens = event.get('input_tokens', 0)
                completion_tokens = event.get('output_tokens', 0)
            yield event
    finally:
        if parts or prompt_tokens or completion_tokens:
            _record_usage(records, model, messages, prompt, ''.join(parts), prompt_tokens, completion_tokens)


def _plan(configured, messages, system_prompt, temperature, max_tokens, top_p, records):
    if len(configured) < 2:
        raise services.ProviderError('La stratégie Planifier/Réaliser/Vérifier nécessite au moins deux modèles configurés.')
    planner, planner_key = configured[0]
    executor, executor_key = configured[1]
    verifier, verifier_key = configured[2] if len(configured) > 2 else configured[0]
    plan_prompt = (system_prompt + "\n\nPrépare un plan d'exécution bref, avec des étapes vérifiables. Ne fournis pas la réponse finale et ne révèle aucun raisonnement privé.").strip()
    plan, plan_input, plan_output = _call(planner, planner_key, messages, plan_prompt, temperature, max_tokens, top_p, records)
    execute_prompt = (system_prompt + "\n\nRéalise la demande en suivant le plan ci-dessous. Donne une proposition complète mais n'affirme pas qu'elle a été vérifiée.\n\nPlan:\n" + plan).strip()
    draft, execute_input, execute_output = _call(executor, executor_key, messages, execute_prompt, temperature, max_tokens, top_p, records)
    verify_prompt = (system_prompt + "\n\nVérifie la proposition par rapport à la demande et au plan. Corrige les erreurs puis rends uniquement la réponse finale, sans détailler de raisonnement privé.\n\nPlan:\n" + plan + "\n\nProposition à vérifier:\n" + draft).strip()
    return verifier, verifier_key, messages, verify_prompt, plan_input + execute_input, plan_output + execute_output


def _consensus(configured, messages, system_prompt, temperature, max_tokens, top_p, records):
    opinions = []
    responding = []
    input_tokens = output_tokens = 0
    for model, key in configured:
        try:
            reply, used_input, used_output = _call(model, key, messages, system_prompt, temperature, max_tokens, top_p, records)
        except services.ProviderError:
            continue
        opinions.append(f'{model.name}:\n{reply}')
        responding.append((model, key))
        input_tokens += used_input
        output_tokens += used_output
    if not opinions:
        raise services.ProviderError("Aucun modèle de l'équipe n'est disponible.")
    if len(opinions) == 1:
        return opinions[0].split('\n', 1)[1], input_tokens, output_tokens
    model, key = responding[0]
    prompt = (
        "Tu es le coordinateur d'une équipe de modèles IA. Compare leurs avis ci-dessous, "
        "corrige leurs erreurs et réponds directement à la question de l'utilisateur. "
        "Ne mentionne pas la coordination ni les modèles.\n\n"
        + '\n\n--- AVIS SUIVANT ---\n\n'.join(opinions)
    )
    return model, key, [{'role': 'user', 'content': prompt}], '', input_tokens, output_tokens


def call_llm_team(models, messages, system_prompt='', temperature=0.7, max_tokens=500, top_p=1.0, strategy='consensus', usage_records=None):
    configured = _configured_models(models)
    prepare = _plan if strategy == 'plan_execute_verify' else _consensus
    result = prepare(configured, messages, system_prompt, temperature, max_tokens, top_p, usage_records)
    if len(result) == 3:
        return result
    model, key, final_messages, prompt, input_tokens, output_tokens = result
    reply, used_input, used_output = _call(model, key, final_messages, prompt, temperature, max_tokens, top_p, usage_records)
    return reply, input_tokens + used_input, output_tokens + used_output


def stream_llm_team(models, messages, system_prompt='', temperature=0.7, max_tokens=500, top_p=1.0, strategy='consensus', usage_records=None):
    configured = _configured_models(models)
    if len(configured) == 1 and strategy == 'consensus':
        model, key = configured[0]
        yield from _stream(model, key, messages, system_prompt, temperature, max_tokens, top_p, usage_records)
        return
    prepare = _plan if strategy == 'plan_execute_verify' else _consensus
    result = prepare(configured, messages, system_prompt, temperature, max_tokens, top_p, usage_records)
    if len(result) == 3:
        reply, input_tokens, output_tokens = result
        yield {'type': 'delta', 'content': reply}
        yield {'type': 'usage', 'input_tokens': input_tokens, 'output_tokens': output_tokens}
        return
    model, key, final_messages, prompt, input_tokens, output_tokens = result
    for event in _stream(model, key, final_messages, prompt, temperature, max_tokens, top_p, usage_records):
        if event['type'] == 'usage':
            yield {'type': 'usage', 'input_tokens': input_tokens + event.get('input_tokens', 0), 'output_tokens': output_tokens + event.get('output_tokens', 0)}
        else:
            yield event


def estimate_team_cost(models, content, system_prompt, messages, max_tokens, strategy):
    reserves = [services.estimate_cost(content, model, system_prompt, messages, max_tokens)[0] for model in models]
    if not reserves:
        return 0
    if strategy == 'plan_execute_verify':
        steps = models[:3] if len(models) >= 3 else [models[0], models[1], models[0]]
        return sum(services.estimate_cost(content, model, system_prompt + ' ' * (index * max_tokens * 4 + 1000), messages, max_tokens)[0] for index, model in enumerate(steps))
    if len(models) == 1:
        return reserves[0]
    synthesis = [{'role': 'user', 'content': ' ' * (max_tokens * 4 * len(models) + 1000)}]
    # Any responding team member may become coordinator if an earlier call fails.
    return sum(reserves) + max(services.estimate_cost('', model, '', synthesis, max_tokens)[0] for model in models)
