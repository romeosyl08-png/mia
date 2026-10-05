import logging
import math
import time

from api.models import APIKey, AIModel

from .models import RoutingFeedback, RoutingObservation
from .services import is_local_provider


logger = logging.getLogger(__name__)
MIN_SCOPE_OBSERVATIONS = 5
MAX_SCORING_OBSERVATIONS = 500


def model_key(model):
	return model.provider.slug, model.model_id


def task_category(surface, mode="chat", workspace=False, tools=False):
	if workspace or surface == "bara":
		return "coding"
	if tools:
		return "tool_use"
	if mode == "web":
		return "research"
	if mode == "deep":
		return "analysis"
	return "general"


def eligible_models(surface="chat", workspace=False, streamed=False, tools=False):
	providers_with_keys = set(
		APIKey.objects.filter(is_active=True, provider__is_active=True).values_list("provider_id", flat=True)
	)
	models = list(
		AIModel.objects.filter(is_active=True, provider__is_active=True)
		.select_related("provider")
		.order_by("provider__name", "name")
	)
	eligible = []
	for model in models:
		provider = model.provider
		if not provider.base_url:
			continue
		if not is_local_provider(provider) and provider.id not in providers_with_keys:
			continue
		provider_name = f"{provider.slug} {provider.name}".lower()
		if workspace and "anthropic" in provider_name:
			continue
		if streamed and surface == "api" and "anthropic" in provider_name:
			continue
		if tools and surface == "api" and (streamed or "anthropic" in provider_name):
			continue
		eligible.append(model)
	return eligible


def _observation_scope(user, category):
	category_rows = RoutingObservation.objects.filter(task_category=category)
	if user is not None:
		user_rows = category_rows.filter(user=user)
		if user_rows.count() >= MIN_SCOPE_OBSERVATIONS:
			return user_rows[:MAX_SCORING_OBSERVATIONS], "user_category"
	if category_rows.count() >= MIN_SCOPE_OBSERVATIONS:
		return category_rows[:MAX_SCORING_OBSERVATIONS], "category"
	all_rows = RoutingObservation.objects.all()
	if all_rows.count() >= MIN_SCOPE_OBSERVATIONS:
		return all_rows[:MAX_SCORING_OBSERVATIONS], "global"
	return [], "prior"


def _feedback_scope(user, category):
	category_feedback = RoutingFeedback.objects.select_related("observation").filter(
		observation__task_category=category,
	)
	if user is not None:
		user_feedback = category_feedback.filter(user=user)
		if user_feedback.count() >= MIN_SCOPE_OBSERVATIONS:
			return list(user_feedback[:MAX_SCORING_OBSERVATIONS])
	if category_feedback.count() >= MIN_SCOPE_OBSERVATIONS:
		return list(category_feedback[:MAX_SCORING_OBSERVATIONS])
	global_feedback = RoutingFeedback.objects.select_related("observation").all()
	if global_feedback.count() >= MIN_SCOPE_OBSERVATIONS:
		return list(global_feedback[:MAX_SCORING_OBSERVATIONS])
	return []


def recommend_model(user, candidates, category, estimated_costs=None, current_model=None):
	"""Return an explainable recommendation; callers in Phase 1 must not dispatch it."""
	candidates = list(candidates)
	if not candidates:
		return {
			"model_id": current_model.model_id if current_model else "",
			"provider_slug": current_model.provider.slug if current_model else "",
			"confidence": 0.0,
			"basis": "no_candidates",
			"score": 0.0,
			"estimated_credits": 0,
		}

	try:
		rows, basis = _observation_scope(user, category)
		observations = list(rows)
		feedback_rows = _feedback_scope(user, category)
	except Exception:
		logger.exception("Could not load AI routing observations")
		observations, feedback_rows, basis = [], [], "prior"

	candidate_ids = {model_key(model) for model in candidates}
	per_model = {
		key: [row for row in observations if (row.provider_slug, row.model_id) == key]
		for key in candidate_ids
	}
	feedback_by_model = {key: [] for key in candidate_ids}
	for feedback in feedback_rows:
		key = (feedback.observation.provider_slug, feedback.observation.model_id)
		if key in feedback_by_model:
			feedback_by_model[key].append(feedback.rating)
	quality_sample_count = sum(len(ratings) for ratings in feedback_by_model.values())
	use_quality_feedback = quality_sample_count >= MIN_SCOPE_OBSERVATIONS
	costs = estimated_costs or {}
	finite_costs = [
		max(1, costs.get(model_key(model), costs.get(model.model_id, 0)))
		for model in candidates
		if costs.get(model_key(model), costs.get(model.model_id, 0)) > 0
	]
	min_cost = min(finite_costs) if finite_costs else 0
	latencies = [
		(sum(row.duration_ms for row in per_model[model_key(model)]) / len(per_model[model_key(model)]))
		for model in candidates if per_model[model_key(model)]
	]
	min_latency = min(latencies) if latencies else 0

	scored = []
	for model in candidates:
		model_rows = per_model[model_key(model)]
		sample_count = len(model_rows)
		successes = sum(row.outcome == "success" for row in model_rows)
		reliability = (successes + 2) / (sample_count + 4)
		ratings = feedback_by_model[model_key(model)]
		quality_successes = sum(rating == "positive" for rating in ratings)
		quality = (quality_successes + 2) / (len(ratings) + 4)
		confidence = sample_count / (sample_count + MIN_SCOPE_OBSERVATIONS) if sample_count else 0
		cost = costs.get(model_key(model), costs.get(model.model_id)) or (
			sum(row.charged_credits or row.estimated_credits for row in model_rows) / sample_count
			if sample_count else 0
		)
		cost_score = min_cost / max(1, cost) if min_cost and cost else 0.5
		latency = sum(row.duration_ms for row in model_rows) / sample_count if sample_count else 0
		latency_score = min_latency / max(1, latency) if min_latency and latency else 0.5
		exploration_bonus = 0.1 / math.sqrt(sample_count + 1)
		if use_quality_feedback:
			score = 0.5 * reliability + 0.3 * quality + 0.15 * cost_score + 0.05 * latency_score + exploration_bonus
		else:
			score = 0.7 * reliability + 0.2 * cost_score + 0.1 * latency_score + exploration_bonus
		scored.append((score, sample_count, model, confidence))

	scored.sort(key=lambda item: (item[0], item[1], item[2].model_id), reverse=True)
	best_score, _sample_count, best_model, confidence = scored[0]
	if basis == "prior" and current_model:
		best_model = current_model
		confidence = 0.0
	return {
		"model_id": best_model.model_id,
		"provider_slug": best_model.provider.slug,
		"confidence": round(confidence, 4),
		"basis": basis,
		"score": round(best_score, 4),
		"estimated_credits": max(0, int(costs.get(model_key(best_model), costs.get(best_model.model_id, 0)) or 0)),
	}


def begin_shadow_run(user, surface, category, current_models, candidates=None, estimated_costs=None,
					 strategy="single", workspace=None, tools_used=None, reserved_credits=0):
	current_models = list(current_models)
	candidates = list(candidates if candidates is not None else current_models)
	current = current_models[0] if current_models else None
	decision = recommend_model(user, candidates, category, estimated_costs, current)
	return {
		"started": time.monotonic(),
		"surface": surface,
		"category": category,
		"strategy": strategy,
		"current_models": current_models,
		"decision": decision,
		"estimated_credits": max(0, int(reserved_credits or 0)),
		"workspace": workspace,
		"tools_used": sorted(set(tools_used or [])),
	}


def safe_shadow_run(user, surface, category, current_models, cost_estimator,
					strategy="single", workspace=None, tools_used=None, reserved_credits=0,
					streamed=False, has_tools=False, workspace_mode=False):
	try:
		candidates = eligible_models(
			surface=surface,
			workspace=workspace_mode,
			streamed=streamed,
			tools=has_tools,
		)
		if surface == "api":
			candidates = [model for model in candidates if not is_local_provider(model.provider)]
		costs = {model_key(model): max(0, int(cost_estimator(model))) for model in candidates}
		return begin_shadow_run(
			user,
			surface,
			category,
			current_models,
			candidates=candidates,
			estimated_costs=costs,
			strategy=strategy,
			workspace=workspace,
			tools_used=tools_used,
			reserved_credits=reserved_credits,
		)
	except Exception:
		logger.exception("Could not build shadow routing recommendation (surface=%s)", surface)
		return None


def record_shadow_run(user, shadow_run, outcome, charged_credits=0, prompt_tokens=0,
					  completion_tokens=0, models_used=None, tools_used=None, assistant_message_id=None):
	if not shadow_run or outcome not in {"success", "error", "interrupted"}:
		return None
	current_models = shadow_run["current_models"]
	if not current_models:
		return None
	decision = shadow_run["decision"]
	model = current_models[0]
	try:
		observation = RoutingObservation.objects.create(
			user=user,
			assistant_message_id=assistant_message_id,
			workspace=shadow_run.get("workspace"),
			surface=shadow_run["surface"],
			task_category=shadow_run["category"],
			strategy=shadow_run["strategy"],
			model_id=model.model_id,
			provider_slug=model.provider.slug,
			recommended_model_id=decision["model_id"],
			recommended_provider_slug=decision["provider_slug"],
			recommendation_confidence=decision["confidence"],
			recommendation_score=decision["score"],
			recommended_estimated_credits=decision["estimated_credits"],
			recommendation_basis=decision["basis"],
			outcome=outcome,
			estimated_credits=shadow_run["estimated_credits"],
			charged_credits=max(0, int(charged_credits or 0)),
			duration_ms=max(0, int((time.monotonic() - shadow_run["started"]) * 1000)),
			prompt_tokens=max(0, int(prompt_tokens or 0)),
			completion_tokens=max(0, int(completion_tokens or 0)),
			models_used=[item.model_id for item in (models_used or current_models)],
			tools_used=sorted(set(tools_used or shadow_run["tools_used"])),
		)
		return observation
	except Exception:
		logger.exception("Could not record AI routing observation")
		return None