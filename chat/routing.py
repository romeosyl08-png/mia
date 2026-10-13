import logging
import math
import time

from django.db.models import Avg, Count, Q
from django.db.models.functions import Coalesce, NullIf

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


def _aggregate_observations(queryset):
	"""Agrège en SQL : échantillons, succès, durée et coût moyens par modèle.

	Remplace le chargement de `MAX_SCORING_OBSERVATIONS` lignes suivies d'un
	filtre en Python, exécuté à chaque message en mode « Auto 2AI ».
	"""
	rows = (
		queryset.values("provider_slug", "model_id")
		.annotate(
			sample_count=Count("id"),
			successes=Count("id", filter=Q(outcome="success")),
			average_duration=Avg("duration_ms"),
			average_credits=Avg(Coalesce(NullIf("charged_credits", 0), "estimated_credits")),
		)
	)
	return {
		(row["provider_slug"], row["model_id"]): {
			"sample_count": row["sample_count"],
			"successes": row["successes"],
			"average_duration": float(row["average_duration"] or 0),
			"average_credits": float(row["average_credits"] or 0),
		}
		for row in rows
	}


def _observation_stats(user, category):
	"""Renvoie (statistiques par modèle, portée retenue)."""
	category_rows = RoutingObservation.objects.filter(task_category=category)
	if user is not None:
		stats = _aggregate_observations(category_rows.filter(user=user)[:MAX_SCORING_OBSERVATIONS])
		if sum(row["sample_count"] for row in stats.values()) >= MIN_SCOPE_OBSERVATIONS:
			return stats, "user_category"
	stats = _aggregate_observations(category_rows[:MAX_SCORING_OBSERVATIONS])
	if sum(row["sample_count"] for row in stats.values()) >= MIN_SCOPE_OBSERVATIONS:
		return stats, "category"
	stats = _aggregate_observations(RoutingObservation.objects.all()[:MAX_SCORING_OBSERVATIONS])
	if sum(row["sample_count"] for row in stats.values()) >= MIN_SCOPE_OBSERVATIONS:
		return stats, "global"
	return {}, "prior"


def _aggregate_feedback(queryset):
	rows = (
		queryset.values("observation__provider_slug", "observation__model_id")
		.annotate(
			rating_count=Count("id"),
			positives=Count("id", filter=Q(rating="positive")),
		)
	)
	return {
		(row["observation__provider_slug"], row["observation__model_id"]): {
			"rating_count": row["rating_count"],
			"positives": row["positives"],
		}
		for row in rows
	}


def _feedback_stats(user, category):
	category_feedback = RoutingFeedback.objects.filter(observation__task_category=category)
	if user is not None:
		stats = _aggregate_feedback(category_feedback.filter(user=user)[:MAX_SCORING_OBSERVATIONS])
		if sum(row["rating_count"] for row in stats.values()) >= MIN_SCOPE_OBSERVATIONS:
			return stats
	stats = _aggregate_feedback(category_feedback[:MAX_SCORING_OBSERVATIONS])
	if sum(row["rating_count"] for row in stats.values()) >= MIN_SCOPE_OBSERVATIONS:
		return stats
	stats = _aggregate_feedback(RoutingFeedback.objects.all()[:MAX_SCORING_OBSERVATIONS])
	return stats if sum(row["rating_count"] for row in stats.values()) >= MIN_SCOPE_OBSERVATIONS else {}


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
		stats, basis = _observation_stats(user, category)
		feedback_stats = _feedback_stats(user, category)
	except Exception:
		logger.exception("Could not load AI routing observations")
		stats, feedback_stats, basis = {}, {}, "prior"

	costs = estimated_costs or {}
	finite_costs = [
		max(1, costs.get(model_key(model), costs.get(model.model_id, 0)))
		for model in candidates
		if costs.get(model_key(model), costs.get(model.model_id, 0)) > 0
	]
	min_cost = min(finite_costs) if finite_costs else 0
	latencies = [stats[model_key(model)]["average_duration"] for model in candidates if stats.get(model_key(model))]
	min_latency = min(latencies) if latencies else 0

	quality_sample_count = sum(row["rating_count"] for row in feedback_stats.values())
	use_quality_feedback = quality_sample_count >= MIN_SCOPE_OBSERVATIONS

	scored = []
	for model in candidates:
		key = model_key(model)
		row = stats.get(key)
		sample_count = row["sample_count"] if row else 0
		successes = row["successes"] if row else 0
		reliability = (successes + 2) / (sample_count + 4)
		ratings = feedback_stats.get(key)
		quality = ((ratings["positives"] + 2) / (ratings["rating_count"] + 4)) if ratings else 0.5
		confidence = sample_count / (sample_count + MIN_SCOPE_OBSERVATIONS) if sample_count else 0
		cost = costs.get(key, costs.get(model.model_id)) or (row["average_credits"] if row else 0)
		cost_score = min_cost / max(1, cost) if min_cost and cost else 0.5
		latency = row["average_duration"] if row else 0
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