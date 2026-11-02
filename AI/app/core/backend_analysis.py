"""Closed chat-analysis-v2 contract from JJIKMUK-Backend eae67ec.

These are backend decisions, not client assertions or label verification flags.
HTTP callers must also pass the service-token and matching-context boundary.
"""

from typing import Annotated, Literal
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ANALYSIS_VERSION = "chat-analysis-v2"
CONTRACT_HEADER = "X-Jjikmuk-Analysis-Contract"
CONTEXT_HEADER = "X-Jjikmuk-Analysis-Context"
MAX_V2_PROFILES = 21
MAX_V2_HISTORY_ITEMS = 20
MAX_V2_HISTORY_CONTENT = 10000
MAX_V2_HISTORY_TOTAL = 40000
backend_analysis_active = ContextVar("backend_analysis_active", default=False)

ProfileKey = Annotated[str, StringConstraints(pattern=r"^(?:self|family|request):[0-9]{1,19}$", max_length=32)]
ContextId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Code = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
Terms = Annotated[list[Code], Field(max_length=128)]
SafetyStatus = Literal["NOT_EVALUATED", "PASS", "DANGER", "UNKNOWN"]
DietStatus = Literal["NOT_EVALUATED", "PASS", "MISMATCH", "UNKNOWN"]
ProfileSource = Literal["SERVER", "CLIENT_REQUEST"]
EvidenceLevel = Literal["VERIFIED_SOURCE", "DECLARED_LABEL", "INGREDIENT_DERIVED", "INFERRED", "UNKNOWN"]
DataOrigin = Literal["SOURCE", "NORMALIZED", "INFERRED", "IMPUTED", "CALCULATED", "UNKNOWN"]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Identifier = Annotated[int, Field(gt=0, le=9223372036854775807)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class AnalysisSelection(ContractModel):
    mode: Literal["self", "selected", "none", "provided"]
    analysisApplied: bool
    includeSelf: bool
    familyProfileIds: list[Identifier] = Field(max_length=20)

    @model_validator(mode="after")
    def consistent_selection(self):
        if len(set(self.familyProfileIds)) != len(self.familyProfileIds):
            raise ValueError("familyProfileIds must be unique")
        if self.analysisApplied != (self.mode != "none"):
            raise ValueError("analysisApplied must agree with selection.mode")
        if self.mode in {"none", "provided"} and (self.includeSelf or self.familyProfileIds):
            raise ValueError("none/provided cannot identify server profiles")
        if self.mode == "self" and (not self.includeSelf or self.familyProfileIds):
            raise ValueError("self selection must contain only self")
        if self.mode == "selected" and not (self.includeSelf or self.familyProfileIds):
            raise ValueError("selected requires a subject")
        return self


class DietaryAttribute(ContractModel):
    key: Code
    status: Literal["MET", "NOT_MET", "UNKNOWN"]
    storedValue: bool
    evidenceOrigin: DataOrigin
    confidence: Confidence | None
    reason: Literal["STORED_CLASSIFICATION", "UNRELIABLE_ORIGIN", "INSUFFICIENT_CONFIDENCE",
                    "MISSING_OR_INVALID_EVIDENCE", "EXPLICIT_INGREDIENT_CONFLICT", "AMBIGUOUS_INGREDIENT_EVIDENCE"]
    ingredientEvidence: Terms = Field(default_factory=list)


class DietDecision(ContractModel):
    status: DietStatus
    requiredFilters: Terms
    satisfiedFilters: Terms
    failedFilters: Terms
    uncertainFilters: Terms
    evidenceOrigin: DataOrigin
    confidence: Confidence | None
    message: Text
    attributes: list[DietaryAttribute] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def consistent_filters(self):
        required = set(self.requiredFilters)
        groups = [set(self.satisfiedFilters), set(self.failedFilters), set(self.uncertainFilters)]
        if any(not group <= required for group in groups):
            raise ValueError("diet results must refer to required filters")
        if any(groups[i] & groups[j] for i in range(3) for j in range(i)):
            raise ValueError("diet filter results must not overlap")
        expected = ("MISMATCH" if self.failedFilters else "UNKNOWN" if self.uncertainFilters else "PASS")
        if self.status == "NOT_EVALUATED":
            if required or any(groups) or self.attributes:
                raise ValueError("unevaluated diet cannot contain filter decisions")
        elif self.status != expected or set().union(*groups) != required:
            raise ValueError("diet status must agree with filter results")
        return self


class BackendDecision(ContractModel):
    status: SafetyStatus
    dietConstraints: DietDecision
    analysisApplied: bool
    selection: AnalysisSelection
    isDangerous: bool
    dangerousIngredients: Terms
    conflictingAllergenIds: Terms
    evidenceSources: Terms
    evidenceLevel: EvidenceLevel
    verificationRequired: bool
    message: Text

    @model_validator(mode="after")
    def consistent_verdict(self):
        if self.isDangerous != (self.status == "DANGER"):
            raise ValueError("isDangerous disagrees with status")
        if self.verificationRequired != (self.status == "UNKNOWN"):
            raise ValueError("verificationRequired disagrees with status")
        if self.analysisApplied != self.selection.analysisApplied or self.analysisApplied != (self.status != "NOT_EVALUATED"):
            raise ValueError("analysisApplied disagrees with status/selection")
        if (self.dietConstraints.status == "NOT_EVALUATED") != (not self.analysisApplied):
            raise ValueError("diet evaluation must agree with selection")
        if self.status != "DANGER" and (self.dangerousIngredients or self.conflictingAllergenIds):
            raise ValueError("non-conflict result cannot contain conflicting allergens")
        if self.status == "DANGER" and not self.conflictingAllergenIds:
            raise ValueError("DANGER requires a conflicting allergen")
        return self


class ProductDataEvidence(ContractModel):
    allergyEvidenceLevel: EvidenceLevel
    allergyConfidence: Confidence | None
    nutritionOrigin: DataOrigin
    nutritionConfidence: Confidence | None
    dietaryOrigin: DataOrigin
    dietaryConfidence: Confidence | None


class EffectiveConditions(ContractModel):
    allergyIds: Terms
    unknownAllergyTerms: Terms
    requiredDietFilterIds: Terms
    diseases: Terms
    dislikedIngredients: Terms


class ProfileAnalysis(ContractModel):
    profileKey: ProfileKey
    analysis: BackendDecision | None


class BackendAnalysisContext(ContractModel):
    contextId: ContextId
    contractVersion: Literal["chat-analysis-v2"]
    policyVersion: Code
    selection: AnalysisSelection
    effectiveConditions: EffectiveConditions | None
    productResolution: Literal["FOUND", "NOT_PROVIDED"]
    decisionScope: Literal["BARCODE_ROW"] = "BARCODE_ROW"
    analysis: BackendDecision | None
    dataEvidence: ProductDataEvidence | None
    decisionAuthority: Literal["BACKEND"] = "BACKEND"
    historyIsAuthoritative: Literal[False] = False
    storedDietFlagsAreAuthoritative: Literal[False] = False
    profileSource: Literal["SERVER", "CLIENT_REQUEST", "NONE"]
    profileAnalyses: list[ProfileAnalysis] = Field(max_length=MAX_V2_PROFILES)

    @model_validator(mode="before")
    @classmethod
    def reject_non_boolean_authority_flags(cls, value):
        if isinstance(value, dict):
            for key in ("historyIsAuthoritative", "storedDietFlagsAreAuthoritative"):
                if key in value and type(value[key]) is not bool:
                    raise ValueError(f"{key} must be a boolean")
        return value

    @model_validator(mode="after")
    def consistent_context(self):
        keys = [item.profileKey for item in self.profileAnalyses]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate profileAnalyses.profileKey")
        applied = self.selection.analysisApplied
        if applied != bool(keys) or applied != (self.effectiveConditions is not None):
            raise ValueError("selection, effectiveConditions and profileAnalyses disagree")
        expected_sources = ({"CLIENT_REQUEST"} if self.selection.mode == "provided" else
                            {"NONE", "CLIENT_REQUEST"} if not applied else {"SERVER"})
        if self.profileSource not in expected_sources:
            raise ValueError("profileSource disagrees with selection")
        if self.productResolution == "NOT_PROVIDED":
            if self.analysis is not None or self.dataEvidence is not None or any(x.analysis is not None for x in self.profileAnalyses):
                raise ValueError("absent product cannot have decisions/evidence")
        else:
            if self.analysis is None or self.dataEvidence is None or any(x.analysis is None for x in self.profileAnalyses):
                raise ValueError("FOUND requires aggregate and individual decisions/evidence")
            if self.analysis.selection != self.selection:
                raise ValueError("aggregate selection disagrees with context")
            for item in self.profileAnalyses:
                if not item.analysis.analysisApplied:
                    raise ValueError("selected profile must have an evaluated decision")
            if self.analysis.status == "PASS" and any(x.analysis.status != "PASS" for x in self.profileAnalyses):
                raise ValueError("aggregate PASS contradicts individual results")
            if any(x.analysis.status == "DANGER" for x in self.profileAnalyses) and self.analysis.status != "DANGER":
                raise ValueError("aggregate cannot erase an individual allergy conflict")
            diets = [x.analysis.dietConstraints.status for x in self.profileAnalyses]
            group_diet = self.analysis.dietConstraints.status
            if (group_diet == "PASS" and any(d != "PASS" for d in diets)) or ("MISMATCH" in diets and group_diet != "MISMATCH"):
                raise ValueError("aggregate diet contradicts individual results")
        return self


class PublicBackendAnalysis(ContractModel):
    """Small audit/UX summary; never publishes private aggregate health terms."""
    contractVersion: Literal["chat-analysis-v2"] = ANALYSIS_VERSION
    contextId: ContextId
    policyVersion: Code
    decisionAuthority: Literal["BACKEND"] = "BACKEND"
    decisionScope: Literal["BARCODE_ROW"] = "BARCODE_ROW"
    profileSource: Literal["SERVER", "CLIENT_REQUEST", "NONE"]
    selectionMode: Literal["self", "selected", "none", "provided"]
    productResolution: Literal["FOUND", "NOT_PROVIDED"]
    barcode: str | None = Field(default=None, max_length=256)
    status: SafetyStatus | None
    dietStatus: DietStatus | None
    application: Literal["product_decision", "context_only", "product_selection_required"]


def validate_request_context(context, profiles, product):
    """Bind keys and subject namespaces, never names/positions/account IDs."""
    keys = [p.profile_key for p in profiles]
    if None in keys or len(keys) != len(set(keys)):
        raise ValueError("v2 profiles require unique profileKey values")
    if set(keys) != {x.profileKey for x in context.profileAnalyses}:
        raise ValueError("profileAnalyses must match exactly the selected profiles")
    if any(not p.is_active for p in profiles):
        raise ValueError("v2 backend must send only selected profiles")
    by_key = {x.profileKey: x.analysis for x in context.profileAnalyses}
    for p in profiles:
        kind, raw_id = p.profile_key.split(":")
        number = int(raw_id)
        if p.profile_id <= 0 or raw_id != str(number) or p.profile_id != number + (1 if kind == "request" else 0):
            raise ValueError("profile_id disagrees with profileKey")
        if p.target_type != ("provided" if kind == "request" else kind):
            raise ValueError("target_type disagrees with profileKey")
        if p.profile_source != context.profileSource:
            raise ValueError("profileSource disagrees with context")
        if (kind == "request") != (context.profileSource == "CLIENT_REQUEST"):
            raise ValueError("profileKey namespace disagrees with profileSource")
        if p.user_id is not None and (kind != "self" or p.user_id != p.profile_id):
            raise ValueError("family/provided profiles cannot supply an account id")
        decision = by_key[p.profile_key]
        expected_selection = AnalysisSelection(mode="provided" if kind == "request" else "self" if kind == "self" else "selected",
            analysisApplied=True, includeSelf=kind == "self", familyProfileIds=[number] if kind == "family" else [])
        if decision is not None and decision.selection != expected_selection:
            raise ValueError("individual decision selection disagrees with profileKey")
    if context.profileSource == "SERVER":
        own = [p for p in profiles if p.target_type == "self"]
        families = {p.profile_id for p in profiles if p.target_type == "family"}
        if len(own) > 1 or bool(own) != context.selection.includeSelf or families != set(context.selection.familyProfileIds):
            raise ValueError("selected server profiles disagree with selection")
    if (product is not None) != (context.productResolution == "FOUND"):
        raise ValueError("product disagrees with productResolution")
    if product is not None and (not product.barcode or not product.product_found):
        raise ValueError("v2 FOUND requires a barcode row")
