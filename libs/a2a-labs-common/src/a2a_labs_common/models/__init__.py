"""Role-based model access (spec 04). Lab code asks for a role, never a model name."""

from __future__ import annotations

from a2a_labs_common.models.adk import get_adk_model
from a2a_labs_common.models.decision import DecisionModel, Question, get_decision_model
from a2a_labs_common.models.langchain import get_langchain_model
from a2a_labs_common.models.settings import ModelRole, describe_models
from a2a_labs_common.models.strands import get_strands_model

__all__ = [
    "DecisionModel",
    "ModelRole",
    "Question",
    "describe_models",
    "get_adk_model",
    "get_decision_model",
    "get_langchain_model",
    "get_strands_model",
]
