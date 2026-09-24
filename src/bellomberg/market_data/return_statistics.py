"""Shared statistics for already date-aligned pairs of simple returns."""
import numpy as np


def paired_beta_statistics(returns):
    values = np.asarray(returns, dtype=float)
    if values.ndim != 2 or values.shape[1] != 2 or len(values) < 20 or not np.isfinite(values).all():
        raise ValueError('at least 20 finite paired asset/benchmark returns required')
    covariance = np.cov(values.T)
    beta = float(covariance[0, 1] / covariance[1, 1]) if covariance[1, 1] > 0 else None
    correlation = (float(np.corrcoef(values.T)[0, 1])
                   if covariance[0, 0] > 0 and covariance[1, 1] > 0 else None)
    return {'beta': beta, 'correlation': correlation, 'n_obs': len(values)}
