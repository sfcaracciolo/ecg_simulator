import copy
import json
import numpy as np
import scipy as sp
from typing import Callable, Literal, TypedDict, Tuple
import colorednoise as cn 

class FunFeatures(TypedDict):
    a: float # V   
    μ: float # rad
    σ: float # rad

class BeatFeatures(TypedDict):
    P: FunFeatures
    Q: FunFeatures
    R: FunFeatures
    S: FunFeatures
    T: FunFeatures
    RR: float

def v(θ, fe: FunFeatures):
    return ( θ % (2*np.pi) - fe['μ'] ) / fe['σ']

def g(v, fe: FunFeatures):
    return fe['a'] * np.exp( -.5 * v**2 )

def dgdt(p, ω, fe: FunFeatures):
    θ, _, _ = p
    vi = v(θ, fe)
    return - g(vi, fe) * vi * ω / fe['σ']

def model(θ, fe: BeatFeatures):
    return  g(v(θ, fe['P']), fe['P']) + \
            g(v(θ, fe['Q']), fe['Q']) + \
            g(v(θ, fe['R']), fe['R']) + \
            g(v(θ, fe['S']), fe['S']) + \
            g(v(θ, fe['T']), fe['T'])

def repeater(iterable: Tuple, repeat: int = 1):
    for item in iter(iterable):
        for _ in range(repeat):
            yield item

class Simulator:

    def __init__(self, fs: int, ζ: float = 0., resp: Tuple[float] = (0., 0.)) -> None:
        Ar, fr = resp 
        self.wr = 2*np.pi*fr
        self.cr = Ar*self.wr
        self.fs = fs
        self.ζ = ζ

    def system(self, t, p):
        θ, ρ, z = p

        # beat selector
        if t >= self.acc:
            self.fe = next(self.features)
            self.acc += self.fe['RR']

        ω = 2*np.pi / self.fe['RR']
        return [ 
            ω,
            (1. - ρ ) * ρ,
            dgdt(p, ω, self.fe['P']) + 
            dgdt(p, ω, self.fe['Q']) + 
            dgdt(p, ω, self.fe['R']) + 
            dgdt(p, ω, self.fe['S']) + 
            dgdt(p, ω, self.fe['T']) -
            self.ζ * z + self.cr*np.sin(self.wr*t)
        ]
    
    def solve(self, features: Tuple[BeatFeatures]):

        self.features = iter(features)
        self.fe = next(self.features)
        self.acc = self.fe['RR']

        tend = np.sum([fe['RR'] for fe in features])
        t = np.arange(start=0, stop=tend, step=1./self.fs)

        θ0, ρ0, z0 = 0., 1., 0.
        θ, ρ, z = sp.integrate.solve_ivp(
            self.system,
            t_span = (t[0], t[-1]),
            y0 = [θ0, ρ0, z0], 
            t_eval = t,
            method='RK45',
            max_step=1./self.fs
        )['y']

        return t, θ, ρ, z
    
    def get_exp_lims(self, z: np.ndarray, features: Tuple[BeatFeatures]):
        if self.ζ == 0.:
            raise ValueError('ζ would be greather than zero.')
        
        RR = features[0]['RR']
        if not all([ RR == fe['RR'] for fe in features]): 
            raise ValueError('All RRs must be equal.')
        
        z0 = 0.
        return self.lim(z0, z[int(self.fs*self.fe['RR'])], self.ζ, self.fe['RR'])

    def remove_drift(self, z: np.ndarray, in_place=False):
        if self.ζ != 0.: raise ValueError('ζ would be zero.')
        drift = np.repeat(z[::self.fs], self.fs)
        if in_place:
            z -= drift
        else:
            return z - drift
        
    @staticmethod
    def lim(z0: float, zRR: float, ζ: float, RR: float):
        return  4./ζ, (zRR-z0) / (1 - np.exp(-RR*ζ))
    
    @staticmethod
    def add_noise(z: np.ndarray, beta: float, snr: float, seed=None, in_place=False):
        noise = cn.powerlaw_psd_gaussian(beta, z.size, random_state=seed)
        power_n = np.mean(noise**2)
        power_s = np.mean(z**2)
        noise *= np.sqrt(power_s*np.power(10., -snr/10.)/power_n)
        if in_place:
            z += noise
        else:
            return z + noise
        
    @staticmethod
    def snr(s: np.ndarray, n: np.ndarray):
        power_n = np.mean(n**2)
        power_s = np.mean(s**2)
        return 10*np.log10(power_s/power_n)
    
def random_features(mfes: BeatFeatures, sfes: BeatFeatures, N: int=100, seed: int = None):
    rng = np.random.default_rng(seed=seed)
    μs = vectorize(mfes)
    σs = vectorize(sfes)
    vs = rng.normal(μs, σs, size=(N, 3 * ( len(mfes) - 1 ) + 1))
    return [ modelize(l) for l in vs ]

def vectorize(fes: BeatFeatures) -> Tuple:
    vec = [ fes['RR'] ]
    vec += fes['P'].values()
    vec += fes['Q'].values()
    vec += fes['R'].values()
    vec += fes['S'].values()
    vec += fes['T'].values()
    return vec


def modelize(l: Tuple) -> BeatFeatures:
    return BeatFeatures(
        RR=l[0],
        P=FunFeatures(a=l[1], μ=l[2], σ=l[3]),
        Q=FunFeatures(a=l[4], μ=l[5], σ=l[6]),
        R=FunFeatures(a=l[7], μ=l[8], σ=l[9]),
        S=FunFeatures(a=l[10], μ=l[11], σ=l[12]),
        T=FunFeatures(a=l[13], μ=l[14], σ=l[15])
    )

class FeatureEditor:
    def __init__(self, f: BeatFeatures) -> None:
        self.model = copy.deepcopy(f)

    def __str__(self) -> str:
        return json.dumps(self.model, indent=4, ensure_ascii=False)
    
    def scale(self, value: float, feature: Literal['a', 'μ', 'σ'] = None):
        self.set(lambda x: x*value, feature=feature)

    def abs(self, feature: Literal['a', 'μ', 'σ'] = None):
        self.set(abs, feature=feature)

    def constant(self, value: float, feature: Literal['a', 'μ', 'σ'] = None):
        self.set(lambda x: value, feature=feature)

    def set(self, fun: Callable, feature: Literal['a', 'μ', 'σ'] = None):
        for k, v in self.model.items():
            if k == 'RR':
                continue
            if feature is None:
                v['a'] = fun(v['a'])
                v['μ'] = fun(v['μ'])
                v['σ'] = fun(v['σ'])
            else:
                v[feature] = fun(v[feature])

def tachogram_features(mfes: BeatFeatures, rr: np.ndarray, fs: int):
    fes = []
    for rri in rr[::fs]:
        fe = copy.deepcopy(mfes)
        fe['RR'] = rri
        fes.append(fe)
    return fes

def tachogram(f_params: Tuple[float] = (.1, .01, .25, .01, .5), t_params: Tuple[float] = (1., .1), Nb: int = 100, fs: int = 1024, seed=None, scaling: bool = True):
    """
    f_params: f1, c1, f2, c2, ratio
    t_params: rr_mean, rr_std
    Nb: amounf of beats
    """
    rr_mean, rr_std = t_params

    rng = np.random.default_rng(seed=seed)
    N = Nb * int(fs * t_params[0]) # time samples
    M = N // 2 + 1 # freq samples

    d = BiModal()
    f = np.linspace(0, fs/2, num=M)
    psd = d.pdf(f, *f_params)

    U = rng.uniform(0, 2*np.pi, size=M)
    S = np.sqrt(M*fs*psd) * np.exp(-1j*U)
    series = np.fft.irfft(S, n=N)

    t = np.arange(0, N) / fs
    rr = rr_mean + series * rr_std/np.std(series) if scaling else series

    return (t, rr), (f, psd)

class BiModal(sp.stats.rv_continuous):
    """Bimodal Gaussian distribution"""
    def __init__(self):
        super().__init__(
            momtype=0,
            name='bimodal',
        )

    def _pdf(self, x, loc1, scale1, loc2, scale2, ratio):
        return ( ratio * sp.stats.norm.pdf(x, loc1, scale1) + sp.stats.norm.pdf(x, loc2, scale2) ) / (1. + ratio)
    
    def _cdf(self, x, loc1, scale1, loc2, scale2, ratio):
        return ( ratio * sp.stats.norm.cdf(x, loc1, scale1) + sp.stats.norm.cdf(x, loc2, scale2) ) / (1. + ratio)
