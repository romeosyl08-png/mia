"""Team orchestration with usage retained separately for every provider call."""

from . import services
from .billing import credits_for_usage
from .team_protocol import (
    estimated_packet, handoff_messages, normalize_packet, role_budgets, role_prompt,
)


def _configured_models(models):
    configured = []
    for model in models:
        key = model.provider.keys.filter(is_active=True).order_by('id').first()
        if model.provider.base_url and (key or services.is_local_provider(model.provider)):
            configured.append((model, key.get_key() if key else ''))
    if not configured:
        raise services.ProviderError("Aucun modèle de l'équipe n'est disponible.")
    return configured


def _record_usage(records, model, messages, system_prompt, reply, prompt_tokens, completion_tokens, role, max_tokens):
    if records is not None:
        estimated_input = services.estimate_cost('', model, system_prompt, messages)[1]
        records.append({
            'model_id': model.model_id,
            'role': role,
            'max_tokens': max_tokens,
            'prompt_tokens': prompt_tokens or estimated_input,
            'completion_tokens': completion_tokens or round(len(reply) / 4),
            'credits': credits_for_usage(model, prompt_tokens or estimated_input, completion_tokens or round(len(reply) / 4)),
        })


def _call(model, key, messages, prompt, temperature, max_tokens, top_p, records, role='final'):
    reply, prompt_tokens, completion_tokens = services.call_llm_provider(
        model.provider, key, model.model_id, messages, prompt, temperature, max_tokens, top_p,
    )
    _record_usage(records, model, messages, prompt, reply, prompt_tokens, completion_tokens, role, max_tokens)
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
            _record_usage(records, model, messages, prompt, ''.join(parts), prompt_tokens, completion_tokens, 'final', max_tokens)


def _plan(configured, messages, system_prompt, temperature, max_tokens, top_p, records):
    if len(configured) < 2:
        raise services.ProviderError('La stratégie Planifier/Réaliser/Vérifier nécessite au moins deux modèles configurés.')
    planner, planner_key = configured[0]
    executor, executor_key = configured[1]
    verifier, verifier_key = configured[2] if len(configured) > 2 else configured[0]
    budgets = role_budgets(max_tokens)
    plan, plan_input, plan_output = _call(
        planner, planner_key, messages, role_prompt(system_prompt, 'plan'), temperature,
        budgets['plan'], top_p, records, 'plan',
    )
    plan = normalize_packet(plan, 'plan', budgets['plan'])
    proposal, work_input, work_output = _call(
        executor, executor_key, handoff_messages(messages, {'plan': plan}),
        role_prompt(system_prompt, 'work'), temperature, budgets['work'], top_p, records, 'work',
    )
    proposal = normalize_packet(proposal, 'work', budgets['work'])
    review, review_input, review_output = _call(
        verifier, verifier_key, handoff_messages(messages, {'plan': plan, 'proposal': proposal}),
        role_prompt(system_prompt, 'review'), temperature, budgets['review'], top_p, records, 'review',
    )
    review = normalize_packet(review, 'review', budgets['review'])
    # The executor applies the review once; the plan has already served its role.
    final_messages = handoff_messages(messages, {'proposal': proposal, 'reviews': [review]})
    return (executor, executor_key, final_messages, role_prompt(system_prompt, 'final'),
            plan_input + work_input + review_input, plan_output + work_output + review_output)


def _consensus(configured, messages, system_prompt, temperature, max_tokens, top_p, records):
    budgets = role_budgets(max_tokens)
    proposal = None
    coordinator = None
    reviews = []
    input_tokens = output_tokens = 0
    for model, key in configured:
        role = 'work' if proposal is None else 'review'
        request_messages = handoff_messages(messages, {'proposal': proposal}) if proposal is not None else messages
        try:
            reply, used_input, used_output = _call(
                model, key, request_messages, role_prompt(system_prompt, role),
                temperature, budgets[role], top_p, records, role,
            )
        except services.ProviderError:
            continue
        packet = normalize_packet(reply, role, budgets[role])
        if proposal is None:
            proposal = packet
            coordinator = (model, key)
        else:
            # Each reviewer sees the proposal and original request, independently.
            reviews.append(packet)
        input_tokens += used_input
        output_tokens += used_output
    if coordinator is None:
        raise services.ProviderError("Aucun modèle de l'équipe n'est disponible.")
    model, key = coordinator
    return (model, key, handoff_messages(messages, {'proposal': proposal, 'reviews': reviews}),
            role_prompt(system_prompt, 'final'), input_tokens, output_tokens)


def call_llm_team(models, messages, system_prompt='', temperature=0.7, max_tokens=500, top_p=1.0, strategy='consensus', usage_records=None):
    configured = _configured_models(models)
    if len(configured) == 1 and strategy == 'consensus':
        model, key = configured[0]
        return _call(model, key, messages, system_prompt, temperature, max_tokens, top_p, usage_records)
    prepare = _plan if strategy == 'plan_execute_verify' else _consensus
    result = prepare(configured, messages, system_prompt, temperature, max_tokens, top_p, usage_records)
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
    model, key, final_messages, prompt, input_tokens, output_tokens = result
    for event in _stream(model, key, final_messages, prompt, temperature, max_tokens, top_p, usage_records):
        if event['type'] == 'usage':
            yield {'type': 'usage', 'input_tokens': input_tokens + event.get('input_tokens', 0), 'output_tokens': output_tokens + event.get('output_tokens', 0)}
        else:
            yield event


def estimate_team_cost(models, content, system_prompt, messages, max_tokens, strategy):
    if not models:
        return 0
    budgets = role_budgets(max_tokens)

    def reserve(model, role, payload=None):
        return services.estimate_cost(
            '', model, role_prompt(system_prompt, role), handoff_messages(messages, payload), budgets[role],
        )[0]

    proposal = estimated_packet(budgets['work'])
    review = estimated_packet(budgets['review'])
    if strategy == 'plan_execute_verify':
        if len(models) < 2:
            raise services.ProviderError('La stratégie Planifier/Réaliser/Vérifier nécessite au moins deux modèles configurés.')
        planner, executor = models[:2]
        verifier = models[2] if len(models) > 2 else planner
        plan = estimated_packet(budgets['plan'])
        return (
            reserve(planner, 'plan')
            + reserve(executor, 'work', {'plan': plan})
            + reserve(verifier, 'review', {'plan': plan, 'proposal': proposal})
            + reserve(executor, 'final', {'proposal': proposal, 'reviews': [review]})
        )
    if len(models) == 1:
        return services.estimate_cost('', models[0], system_prompt, messages, max_tokens)[0]
    # A later member can become proposer/coordinator when an earlier call fails.
    intermediates = reserve(models[0], 'work') + sum(
        max(reserve(model, 'work'), reserve(model, 'review', {'proposal': proposal})) for model in models[1:]
    )
    final = max(reserve(model, 'final', {'proposal': proposal, 'reviews': [review] * (len(models) - 1)})
                for model in models)
    return intermediates + final
