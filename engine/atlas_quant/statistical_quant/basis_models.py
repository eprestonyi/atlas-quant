"""Bounded, auditable nonlinear bases fitted only inside a training fold.

The dictionary is fixed before any outcome is read. Route one fits it jointly;
route two selects a basis per factor with training-only penalized regression,
then removes redundant terms and jointly regularizes the retained dictionary.
All these choices are repeated in each chronological validation fold.
"""
from __future__ import annotations
import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.linear_model import ElasticNet, Ridge
from .schema import fail

MAX_TERMS = 1024
NONLINEAR_GRIDS = {
    "polynomial_ridge": [{"alpha": 10., "degree": 2}, {"alpha": 100., "degree": 2}],
    "polynomial_elastic_net": [{"alpha": .001, "l1_ratio": .5, "degree": 2},
                               {"alpha": .01, "l1_ratio": .5, "degree": 2}],
    "transformed_ridge": [{"alpha": 10.}, {"alpha": 100.}],
    "factorwise_basis": [{"alpha": .001, "l1_ratio": .5}, {"alpha": .01, "l1_ratio": .5}],
}


def dictionary(n, family):
    terms = [{"kind": "power", "feature": j, "degree": 1} for j in range(n)]
    if family.startswith("polynomial"):
        terms += [{"kind": "power", "feature": j, "degree": 2} for j in range(n)]
        terms += [{"kind": "interaction", "features": [i, j]} for i in range(n) for j in range(i+1, n)]
    else:
        for j in range(n):
            terms += [{"kind": "power", "feature": j, "degree": 2},
                      {"kind": "power", "feature": j, "degree": 3},
                      {"kind": "signed_log1p", "feature": j},
                      {"kind": "signed_expm1", "feature": j}]
    if len(terms) > MAX_TERMS:
        fail("MODEL_BASIS_BUDGET", "完整基函数集合超过预算；请减少输入因子")
    return terms


def expand(X, terms):
    """X already has the frozen input transform; no fitting or implicit clipping."""
    X = np.asarray(X, dtype=float)
    out = []
    with np.errstate(over="ignore", invalid="ignore"):
        for term in terms:
            kind = term["kind"]
            if kind == "interaction":
                a, b = term["features"]
                value = X[:, a]*X[:, b]
            else:
                value = X[:, term["feature"]]
                if kind == "power":
                    value = value**term["degree"]
                elif kind == "signed_log1p":
                    value = np.sign(value)*np.log1p(np.abs(value))
                elif kind == "signed_expm1":
                    # A bounded shape basis, not a claim that prices are exponential.
                    value = np.sign(value)*np.expm1(np.minimum(np.abs(value), 3.))
                else:
                    fail("INVALID_MODEL_BASIS", "未注册的基函数")
            out.append(value)
    result = np.column_stack(out)
    if not np.isfinite(result).all():
        fail("INVALID_FORECAST", "基函数超出有限数值范围")
    return result


class BasisRegressor(RegressorMixin, BaseEstimator):
    def __init__(self, family, alpha, l1_ratio=.5, degree=2):
        self.family, self.alpha, self.l1_ratio, self.degree = family, alpha, l1_ratio, degree

    def fit(self, X, y):
        X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
        terms = dictionary(X.shape[1], self.family)
        values = expand(X, terms)
        center, scale = values.mean(axis=0), values.std(axis=0)
        scale = np.where(scale < 1e-12, 1., scale)
        values = (values-center)/scale
        chosen = list(range(len(terms)))
        self.factor_selection_ = []
        self.dropped_terms_ = []
        if self.family == "factorwise_basis":
            chosen = []
            for feature in range(X.shape[1]):
                indices = [i for i, term in enumerate(terms) if term.get("feature") == feature]
                # A jointly penalized univariate basis expansion. It is not
                # scored as an independent success; outer time folds judge it.
                local = ElasticNet(alpha=self.alpha, l1_ratio=self.l1_ratio,
                                   max_iter=20000, tol=1e-6, selection="cyclic")
                local.fit(values[:, indices], y)
                local_coef = np.atleast_2d(local.coef_)
                active = [indices[i] for i in range(len(indices)) if np.max(np.abs(local_coef[:, i])) > 1e-12]
                chosen.extend(active)
                self.factor_selection_.append({"feature": feature, "terms": active,
                                               "coefficients": (local_coef if y.shape[1] == 1 else np.asarray(local.coef_)).tolist(),
                                               "intercepts": (np.atleast_1d(local.intercept_) if y.shape[1] == 1 else np.asarray(local.intercept_)).tolist()})
            # Retain a constant-capable representation even if all bases shrink
            # to zero; explicit coefficients will record the lack of an effect.
            if not chosen:
                chosen = list(range(X.shape[1]))
            kept = []
            for index in sorted(chosen):
                duplicate = None
                for previous in kept:
                    a, b = values[:, index], values[:, previous]
                    if np.std(a) > 1e-12 and np.std(b) > 1e-12 and abs(np.corrcoef(a, b)[0, 1]) >= .995:
                        duplicate = previous
                        break
                if duplicate is None:
                    kept.append(index)
                else:
                    self.dropped_terms_.append({"term": index, "retained": duplicate,
                                                "reason": "training_absolute_correlation_ge_0.995"})
            chosen = kept
        self.terms_ = [terms[i] for i in chosen]
        self.term_center_, self.term_scale_ = center[chosen], scale[chosen]
        self.dictionary_size_ = len(terms)
        estimator = (ElasticNet(alpha=self.alpha, l1_ratio=self.l1_ratio,
                               max_iter=20000, tol=1e-6, selection="cyclic")
                     if self.family in ("polynomial_elastic_net", "factorwise_basis")
                     else Ridge(alpha=self.alpha))
        estimator.fit(values[:, chosen], y)
        self.coef_ = np.atleast_2d(estimator.coef_) if y.shape[1] == 1 else np.asarray(estimator.coef_)
        self.intercept_ = np.atleast_1d(estimator.intercept_) if y.shape[1] == 1 else np.asarray(estimator.intercept_)
        self.n_features_in_ = X.shape[1]
        return self

    def predict(self, X):
        values = (expand(X, self.terms_)-self.term_center_)/self.term_scale_
        return values @ self.coef_.T + self.intercept_

    def audit(self):
        return {"schema": "factor-basis-fit/1", "route": "factorwise_then_joint" if self.family == "factorwise_basis" else "joint_dictionary",
                "terms": self.terms_, "termCenter": self.term_center_.tolist(), "termScale": self.term_scale_.tolist(),
                "dictionarySize": self.dictionary_size_, "retainedTerms": len(self.terms_),
                "factorSelections": self.factor_selection_, "redundantTerms": self.dropped_terms_,
                "termSelectionUsesTrainingOnly": True, "signedExpm1AbsoluteInputCap": 3.}
