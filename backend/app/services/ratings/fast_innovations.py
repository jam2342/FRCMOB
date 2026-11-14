# Optional research acceleration; serving fits keep the reference implementation.
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np


def _kernel(events, teams, local, sizes, offsets, cov_offsets, points, unit_points, times, z, rookies, previous, params, cutoff):
    prior,match,event,noise,beta,rookie,count = params
    pairs = int(sizes.sum())
    mean = np.zeros(pairs)
    covariance = np.zeros(int((sizes*sizes).sum()))
    entered = np.zeros(pairs,np.bool_)
    latest = np.full(len(z),-1,np.int64)
    diagonal = np.zeros(pairs,np.int64)
    active = np.zeros(pairs,np.int64)
    active_count = np.zeros(len(sizes),np.int64)
    ph = np.zeros(int(sizes.max()) if len(sizes) else 0)
    nus,variances = np.zeros(2*len(events)),np.zeros(2*len(events))
    observed,total,n = 0,0.,0
    for row in range(len(events)):
        if times[row] >= cutoff:
            break
        e = events[row]
        width,base,cb = sizes[e],offsets[e],cov_offsets[e]
        for k in range(6):
            j,t = local[row,k],teams[row,k]
            pair = base+j
            diagonal[pair] = cb+j*width+j
            if not entered[pair]:
                old = latest[t]
                if old < 0:
                    mean[pair] = math.exp(beta*z[t]) if math.isfinite(z[t]) else rookie if rookies[t] else 1.
                    covariance[diagonal[pair]] = prior*prior
                else:
                    mean[pair] = mean[old]
                    covariance[diagonal[pair]] = covariance[diagonal[old]]+event*event
                entered[pair] = True
                latest[t] = pair
                active[base+active_count[e]] = j
                active_count[e] += 1
        n0 = 24 if previous > 0 else 0
        denominator = n0+observed
        unit = (previous*n0+total)/denominator if denominator else 0.
        if unit <= 0:
            unit = 30.
        red_mean = mean[base+local[row,0]]+mean[base+local[row,1]]+mean[base+local[row,2]]
        blue_mean = mean[base+local[row,3]]+mean[base+local[row,4]]+mean[base+local[row,5]]
        noises = (noise*noise+count*max(red_mean,0.),noise*noise+count*max(blue_mean,0.))
        for color in range(2):
            y = points[row,color]
            if not math.isfinite(y):
                continue
            i,j,k = local[row,color*3],local[row,color*3+1],local[row,color*3+2]
            for a in range(active_count[e]):
                v = active[base+a]
                ph[v] = covariance[cb+v*width+i]+covariance[cb+v*width+j]+covariance[cb+v*width+k]
            s = ph[i]+ph[j]+ph[k]+noises[color]
            nu = y/unit-(mean[base+i]+mean[base+j]+mean[base+k])
            nus[n],variances[n] = nu,s
            n += 1
            for a in range(active_count[e]):
                v = active[base+a]
                gain = ph[v]/s
                mean[base+v] += gain*nu
                for b in range(active_count[e]):
                    w = active[base+b]
                    covariance[cb+v*width+w] -= gain*ph[w]
        for k in range(6):
            covariance[diagonal[base+local[row,k]]] += match*match
        for color in range(2):
            if math.isfinite(unit_points[row,color]):
                total += unit_points[row,color]/3.
                observed += 1
    return nus[:n],variances[:n]


@lru_cache(maxsize=1)
def compiled_kernel():
    from numba import njit
    return njit(cache=False,fastmath=False)(_kernel)


class PackedInnovations:
    def __init__(self, inputs, *, compiled=True):
        results = inputs.research.season.results
        if any(len(r.card.red) != 3 or len(r.card.blue) != 3 or len(set(r.card.red+r.card.blue)) != 6 for r in results):
            raise ValueError("packed innovations require six distinct teams")
        self.inputs = inputs
        event_keys = sorted({r.card.event_key for r in results})
        team_keys = sorted({t for r in results for t in r.card.red+r.card.blue})
        event_ids,team_ids = {e:i for i,e in enumerate(event_keys)},{t:i for i,t in enumerate(team_keys)}
        members = {e:sorted({t for r in results if r.card.event_key == e for t in r.card.red+r.card.blue}) for e in event_keys}
        positions = {e:{t:i for i,t in enumerate(ts)} for e,ts in members.items()}
        self.sizes = np.array([len(members[e]) for e in event_keys],dtype=np.int64)
        self.offsets = np.r_[0,np.cumsum(self.sizes)[:-1]].astype(np.int64)
        self.cov_offsets = np.r_[0,np.cumsum(self.sizes*self.sizes)[:-1]].astype(np.int64)
        self.events = np.array([event_ids[r.card.event_key] for r in results],dtype=np.int64)
        self.teams = np.array([[team_ids[t] for t in r.card.red+r.card.blue] for r in results],dtype=np.int64).reshape(-1,6)
        self.local = np.array([[positions[r.card.event_key][t] for t in r.card.red+r.card.blue] for r in results],dtype=np.int64).reshape(-1,6)
        self.points = np.array([[v if v is not None and "total_points" in c else np.nan for v,c in
            ((r.red_points,r.red_components),(r.blue_points,r.blue_components))] for r in results],dtype=float).reshape(-1,2)
        self.unit_points = np.array([[np.nan if v is None else v for v in (r.red_points,r.blue_points)] for r in results],dtype=float).reshape(-1,2)
        self.times = np.array(inputs.times,dtype=np.int64)
        if len(self.times) != len(results) or np.any(np.diff(self.times) < 0):
            raise ValueError("packed innovations need ordered, aligned actual times")
        self.z = np.array([inputs.z_previous.get(t,np.nan) for t in team_keys],dtype=float)
        self.rookies = np.array([t in inputs.rookies for t in team_keys],dtype=bool)
        self.kernel = _kernel
        if compiled:
            self.kernel = compiled_kernel()

    def __call__(self, params, until):
        if params.drift_level or params.prior_level or params.noise_prop:
            raise ValueError("packed innovations support counting noise only")
        p = np.array([params.prior,params.match,params.event,params.noise,params.beta,params.rookie,params.noise_count])
        return self.kernel(self.events,self.teams,self.local,self.sizes,self.offsets,self.cov_offsets,self.points,self.unit_points,
                           self.times,self.z,self.rookies,self.inputs.previous_unit or 0.,p,until if until is not None else np.iinfo(np.int64).max)

    def verify(self, params, until=None):
        from app.services.ratings.research_fit import innovations
        expected = innovations(self.inputs,params,until)
        actual = self(params,until)
        for a,b in zip(actual,expected):
            np.testing.assert_allclose(a,b,rtol=1e-10,atol=1e-10)
        return {"observations":len(actual[0]),"max_abs_error":max((float(np.max(np.abs(a-b))) if len(a) else 0.) for a,b in zip(actual,expected))}
