# Copyright 2016 Enzo Busseti, Stephen Boyd, Steven Diamond, BlackRock Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""This module implements realistic constraints to be used with SinglePeriodOptimization
and MultiPeriodOptimization policies, or other Cvxpy-based policies.
"""


import cvxpy as cvx
import numpy as np

from .estimator import CvxpyExpressionEstimator, ParameterEstimator
from .forecast import HistoricalFactorizedCovariance

__all__ = [
    "LongOnly",
    "LeverageLimit",
    "LongCash",
    "DollarNeutral",
    "ParticipationRateLimit",
    "MaxWeights",
    "MinWeights",
    "FactorMaxLimit",
    "FactorMinLimit",
    "FixedFactorLoading",
    "MarketNeutral",
    "MinWeightsAtTimes",
    "MaxWeightsAtTimes",
]


class BaseConstraint(CvxpyExpressionEstimator):
    """Base cvxpy constraint class."""


class BaseTradeConstraint(BaseConstraint):
    """Base class for constraints that operate on trades."""

    pass


class BaseWeightConstraint(BaseConstraint):
    """Base class for constraints that operate on weights.

    Here we can implement a method to pass benchmark weights
    and make the constraint relative to it rather than to the null
    portfolio.
    """

    pass

class MarketNeutral(BaseWeightConstraint):
    
    def __init__(self):
        self.covarianceforecaster = HistoricalFactorizedCovariance()
    
    def pre_evaluation(self, universe, backtest_times):
        super().pre_evaluation(universe=universe, backtest_times=backtest_times)
        self.market_vector = cvx.Parameter(len(universe)-1)
    
    def values_in_time(self, t, past_volumes, past_returns, **kwargs):
        super().values_in_time(past_volumes=past_volumes, past_returns=past_returns, t=t, **kwargs)
        tmp = past_volumes.iloc[-250:].mean()
        tmp /= sum(tmp)
        
        tmp2 = self.covarianceforecaster.current_value @ (self.covarianceforecaster.current_value.T @ tmp)
        # print(tmp2)
        self.market_vector.value = np.array(tmp2)
        
    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        return w_plus[:-1].T @ self.market_vector == 0
        

class ParticipationRateLimit(BaseTradeConstraint):
    """A limit on maximum trades size as a fraction of market volumes.
    

    :param volumes: per-stock and per-day market volume estimates, or constant in time
    :type volumes: pd.Series or pd.DataFrame
    :param max_fraction_of_volumes: max fraction of market volumes that we're allowed to trade
    :type max_fraction_of_volumes: float, pd.Series, pd.DataFrame
    """

    def __init__(self, volumes, max_fraction_of_volumes=0.05):
        self.volumes = ParameterEstimator(volumes)
        self.max_participation_rate = ParameterEstimator(
            max_fraction_of_volumes)
        self.portfolio_value = cvx.Parameter(nonneg=True)

    def values_in_time(self, current_portfolio_value, **kwargs):
        self.portfolio_value.value = current_portfolio_value
        super().values_in_time(current_portfolio_value=current_portfolio_value, **kwargs)
        
    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return cvx.multiply(cvx.abs(z[:-1]), self.portfolio_value) <= cvx.multiply(
            self.volumes, self.max_participation_rate
        )


class LongOnly(BaseWeightConstraint):
    """A long only constraint.
    
    Imposes that at each point in time the post-trade
    weights are non-negative.
    """

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return w_plus[:-1] >= 0


class LeverageLimit(BaseWeightConstraint):
    """A limit on leverage.
    
    Leverage is defined as the :math:`\ell_1` norm of non-cash
    post-trade weights. Here we require that it is smaller than
    a given value

    :param limit: constant or varying in time leverage limit
    :type limit: float or pd.Series
    """

    def __init__(self, limit):
        self.limit = ParameterEstimator(limit)

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return cvx.norm(w_plus[:-1], 1) <= self.limit


class LongCash(BaseWeightConstraint):
    """Requires that cash be non-negative."""

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        # TODO clarify this
        realcash = (w_plus[-1] - 2 * cvx.sum(cvx.neg(w_plus[:-1])))
        return realcash >= 0


class DollarNeutral(BaseWeightConstraint):
    """Long-short dollar neutral strategy."""

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return w_plus[-1] == 1


class MaxWeights(BaseWeightConstraint):
    """A max limit on weights.

    Attributes:
      limit: A series or number giving the weights limit.
    """

    def __init__(self, limit):
        self.limit = ParameterEstimator(limit)

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return w_plus[:-1] <= self.limit


class MinWeights(BaseWeightConstraint):
    """A min limit on weights.

    Attributes:
      limit: A series or number giving the weights limit.
    """

    def __init__(self, limit):
        self.limit = ParameterEstimator(limit)

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return w_plus[:-1] >= self.limit

class MinMaxWeightsAtTimes(BaseWeightConstraint):

    def __init__(self, limit, times):
        self.base_limit = limit
        self.times = times
    
    def pre_evaluation(self, universe, backtest_times):
        super().pre_evaluation(universe=universe, backtest_times = backtest_times)
        self.backtest_times = backtest_times
        self.limit = cvx.Parameter()
        
    def values_in_time(self, t, mpo_step, **kwargs):
        super().values_in_time(t=t, mpo_step=mpo_step, **kwargs)
        tidx = self.backtest_times.get_loc(t)
        nowtidx = tidx + mpo_step
        if (nowtidx < len(self.backtest_times)) and self.backtest_times[nowtidx] in self.times:
            self.limit.value = self.base_limit
        else:
            self.limit.value = 100 * self.sign


class MinWeightsAtTimes(MinMaxWeightsAtTimes):
    
    sign = -1.

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return w_plus[:-1] >= self.limit
        
class MaxWeightsAtTimes(MinMaxWeightsAtTimes):

    sign = 1.
    
    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return w_plus[:-1] <= self.limit
        

class FactorMaxLimit(BaseWeightConstraint):
    """A max limit on portfolio-wide factor (e.g. beta) exposure.

    Args:
        factor_exposure: An (n * r) matrix giving the factor exposure per asset
        per factor, where n represents # of assets and r represents # of factors
        limit: A series of list or a single list giving the factor limits
    """

    def __init__(self, factor_exposure, limit):
        self.factor_exposure = ParameterEstimator(factor_exposure)
        self.limit = ParameterEstimator(limit)

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return self.factor_exposure.T @ w_plus[:-1] <= self.limit


class FactorMinLimit(BaseWeightConstraint):
    """A min limit on portfolio-wide factor (e.g. beta) exposure.

    Args:
        factor_exposure: An (n * r) matrix giving the factor exposure per asset
        per factor, where n represents # of assets and r represents # of factors
        limit: A series of list or a single list giving the factor limits
    """

    def __init__(self, factor_exposure, limit):
        self.factor_exposure = ParameterEstimator(factor_exposure)
        self.limit = ParameterEstimator(limit)

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return self.factor_exposure.T @ w_plus[:-1] >= self.limit


class FixedFactorLoading(BaseWeightConstraint):
    """A constraint to fix portfolio loadings to a set of factors.

    This can be used to impose market neutrality, a certain portfolio-wide alpha, ....

    Attributes:
        factor_exposure: An (n * r) matrix giving the factor exposure on each
        factor
        target: A series or number giving the targeted factor loading
    """

    def __init__(self, factor_exposure, target):
        self.factor_exposure = ParameterEstimator(factor_exposure)
        self.target = ParameterEstimator(target)

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Return a Cvxpy constraint."""
        return self.factor_exposure.T @ w_plus[:-1] == self.target
