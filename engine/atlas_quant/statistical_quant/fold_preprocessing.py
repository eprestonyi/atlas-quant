"""Frozen automatic train-fold statistics with scope-aware populations."""
from __future__ import annotations
import re
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from .schema import fail


class AutomaticFoldTransform(TransformerMixin, BaseEstimator):
    """Fit global columns once per date; asset columns retain pooled rows.

    Dates are supplied from the already-purged training mask, never inferred
    from a feature or label. transform() uses only the resulting fixed vectors.
    """
    def __init__(self, *, columns, global_columns, dates, winsorize, standardize):
        self.columns = columns
        self.global_columns = global_columns
        self.dates = dates
        self.winsorize = winsorize
        self.standardize = standardize

    def fit(self, X, y=None):
        values = np.asarray(X, dtype=float)
        if values.ndim != 2 or values.shape[1] != len(self.columns) or np.isinf(values).any():
            fail("INVALID_AUTOMATIC_PREPROCESSING", "训练特征结构或数值无效")
        grouped = {}
        if self.global_columns:
            if self.dates is None or len(self.dates) != len(values) or any(not isinstance(date, str) or not re.fullmatch(r"[0-9]{8}",date) for date in self.dates):
                fail("MISSING_GLOBAL_TRAIN_DATES", "全局因子标准化需要本训练折的真实观察日期")
            for index, date in enumerate(self.dates):
                grouped.setdefault(date, []).append(index)
        self.lower_ = np.zeros(len(self.columns)) if self.winsorize else None
        self.upper_ = np.zeros(len(self.columns)) if self.winsorize else None
        self.statistics_ = np.zeros(len(self.columns))
        self.center_ = np.zeros(len(self.columns)) if self.standardize else None
        self.scale_ = np.ones(len(self.columns)) if self.standardize else None
        self.fit_rows_, self.observed_rows_ = [], []
        for column_i, column in enumerate(self.columns):
            population = values[:, column_i]
            if column in self.global_columns:
                for indices in grouped.values():
                    duplicates = population[indices]
                    if not np.all((duplicates == duplicates[0]) | (np.isnan(duplicates) & np.isnan(duplicates[0]))):
                        fail("CONFLICTING_GLOBAL_FACTOR", "同一训练日的全局因子值不一致："+column)
                population = population[[indices[0] for indices in grouped.values()]]
            count = int(np.isfinite(population).sum())
            self.fit_rows_.append(len(population)); self.observed_rows_.append(count)
            if count < 10:
                fail("MISSING_MODEL_DATA", "每个预测特征至少需要10个真实训练观测；全局因子按日期计数")
            if self.winsorize:
                self.lower_[column_i], self.upper_[column_i] = np.nanquantile(population,[.01,.99])
                population = np.clip(population,self.lower_[column_i],self.upper_[column_i])
            self.statistics_[column_i] = np.nanmedian(population)
            population = np.where(np.isnan(population),self.statistics_[column_i],population)
            if self.standardize:
                self.center_[column_i] = np.median(population)
                low, high = np.quantile(population,[.25,.75])
                spread = high-low
                # Same degenerate-IQR rule as sklearn's RobustScaler, explicit
                # here so the archive does not depend on future library code.
                self.scale_[column_i] = 1. if spread < 10*np.finfo(float).eps else spread
        return self

    def transform(self, X):
        values = np.asarray(X,dtype=float)
        if values.ndim != 2 or values.shape[1] != len(self.columns):
            fail("INVALID_AUTOMATIC_PREPROCESSING", "推理特征与冻结变换不匹配")
        if self.lower_ is not None:
            values = np.clip(values,self.lower_,self.upper_)
        values = np.where(np.isnan(values),self.statistics_,values)
        if self.center_ is not None:
            values = (values-self.center_)/self.scale_
        if not np.isfinite(values).all():
            fail("INVALID_AUTOMATIC_PREPROCESSING", "自动预处理产生非有限模型输入")
        return values
