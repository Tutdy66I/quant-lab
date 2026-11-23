"""Chronological, synthetic-only prediction comparison; no market I/O.

Each generated row t is (time, (previous y, exogenous signal), y). Both
features are known before the generated y; the data is only a synthetic
mathematical example. Train standardization/fit occurs once on the first 120
rows, alpha is chosen on the next 60, and 60 last rows are used once for
descriptive comparison. Not a trading or Phase 3C-1 formal protocol runner.
"""
from dataclasses import dataclass
import math
import random

SEED = 20261113
# Predetermined log-spaced values spanning mild to strong regularization; they
# are an engineering comparison set, not a claim of optimal model selection.
ALPHAS = (0.01, 0.1, 1.0, 10.0)
SCENARIOS = ('signal', 'no_signal', 'high_noise', 'distribution_shift')
TRAIN_END = 120
TUNE_END = 180
TOTAL = 240


def _number(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError('finite numeric inputs required')
    try:
        x = float(x)
    except OverflowError as exc:
        raise ValueError('numeric overflow') from exc
    if not math.isfinite(x):
        raise ValueError('finite numeric inputs required')
    return x


def _sum(values):
    try:
        return _number(math.fsum(values))
    except OverflowError as exc:
        raise ValueError('nonfinite intermediate') from exc


def _product(a, b):
    return _number(a * b)


def _validate_xy(x, y, *, min_rows=3):
    if not isinstance(x, (list, tuple)) or not isinstance(y, (list, tuple)):
        raise ValueError('X and y must be sequences')
    if len(x) != len(y) or len(x) < min_rows:
        raise ValueError('insufficient or misaligned samples')
    if not isinstance(x[0], (list, tuple)) or not x[0]:
        raise ValueError('nonempty feature rows required')
    width = len(x[0])
    if width > 32:
        raise ValueError('at most 32 features supported')
    out_x = []
    for row in x:
        if not isinstance(row, (list, tuple)) or len(row) != width:
            raise ValueError('inconsistent feature width')
        out_x.append(tuple(_number(v) for v in row))
    return out_x, tuple(_number(v) for v in y), width


@dataclass(frozen=True)
class Model:
    means: tuple
    scales: tuple
    target_mean: float
    weights: tuple
    alpha: float


def fit_ridge(x, y, alpha):
    """Fit intercept + standardized Ridge with loss mean((y-pred)^2)+alpha*||w||².

    Population standard deviations and centering are estimated only from x/y.
    Strictly positive alpha, nonzero variance and finite intermediate values
    are required. Cholesky decomposition solves the regularized SPD system.
    """
    alpha = _number(alpha)
    if alpha <= 0:
        raise ValueError('alpha must be positive')
    x, y, width = _validate_xy(x, y)
    n = len(y)
    means = tuple(_number(_sum(row[j] for row in x) / n) for j in range(width))
    scales = []
    for j in range(width):
        var = _number(_sum(_product(_number(row[j]-means[j]), _number(row[j]-means[j])) for row in x) / n)
        scale = _number(math.sqrt(var))
        if scale == 0:
            raise ValueError('zero feature variance')
        scales.append(scale)
    target_mean = _number(_sum(y) / n)
    z = [tuple(_number(_number(row[j]-means[j])/scales[j]) for j in range(width)) for row in x]
    yc = [_number(v-target_mean) for v in y]
    matrix = [[_number(_sum(_product(row[j],row[k]) for row in z)/n + (alpha if j == k else 0.0))
               for k in range(width)] for j in range(width)]
    rhs = [_number(_sum(_product(row[j], target) for row, target in zip(z,yc))/n) for j in range(width)]
    lower = [[0.0]*width for _ in range(width)]
    for i in range(width):
        for j in range(i+1):
            subtract = _sum(_product(lower[i][k],lower[j][k]) for k in range(j))
            value = _number(matrix[i][j]-subtract)
            if i == j:
                if value <= 0:
                    raise ValueError('non-positive or numerically unstable ridge system')
                lower[i][j] = _number(math.sqrt(value))
            else:
                lower[i][j] = _number(value / lower[j][j])
    tmp = [0.0]*width
    for i in range(width):
        tmp[i] = _number((rhs[i]-_sum(_product(lower[i][j],tmp[j]) for j in range(i)))/lower[i][i])
    weights = [0.0]*width
    for i in range(width-1,-1,-1):
        weights[i] = _number((tmp[i]-_sum(_product(lower[j][i],weights[j]) for j in range(i+1,width)))/lower[i][i])
    return Model(means, tuple(scales), target_mean, tuple(weights), alpha)


def predict_ridge(model, row):
    if not isinstance(model, Model) or not isinstance(row, (list, tuple)):
        raise ValueError('invalid model or feature shape')
    if not all(isinstance(values, (list, tuple)) for values in (model.means, model.scales, model.weights)):
        raise ValueError('invalid model vector types')
    width = len(model.weights)
    if not 1 <= width <= 32 or any(len(values) != width for values in (model.means, model.scales, row)):
        raise ValueError('invalid model or feature shape')
    target_mean = _number(model.target_mean)
    if _number(model.alpha) <= 0:
        raise ValueError('invalid model alpha')
    means = tuple(_number(x) for x in model.means)
    scales = tuple(_number(x) for x in model.scales)
    weights = tuple(_number(x) for x in model.weights)
    if any(x <= 0 for x in scales):
        raise ValueError('invalid model scale')
    terms = [_product(w, _number((_number(v)-m)/s))
             for v,m,s,w in zip(row,means,scales,weights)]
    return _number(target_mean + _sum(terms))


def generate(scenario, seed=SEED):
    """Generate 240 samples using a private RNG, not prices or market returns.

    A standard-normal driver and independent noise are drawn each time step.
    Features are (previous target, current driver). In the signal scenario,
    y = 0.45 * previous + 0.8 * driver + noise, noise sigma 0.25. No signal
    uses noise alone. High noise keeps signal but changes sigma to 2.0.
    Distribution shift reverses the driver coefficient only in validation.
    """
    if scenario not in SCENARIOS or type(seed) is not int:
        raise ValueError('unknown synthetic scenario or invalid seed')
    rng = random.Random(seed)
    previous = 0.0
    rows = []
    for t in range(TOTAL):
        driver = rng.gauss(0.0, 1.0)
        noise = rng.gauss(0.0, 2.0 if scenario == 'high_noise' else 0.25)
        if scenario == 'no_signal':
            target = noise
        else:
            sign = -1.0 if scenario == 'distribution_shift' and t >= TUNE_END else 1.0
            target = 0.45 * previous + sign * 0.8 * driver + noise
        target = _number(target)
        rows.append({'time': t, 'features': (previous, driver), 'previous': previous, 'target': target})
        previous = target
    return rows


def _mse(pred, actual):
    if not pred or len(pred) != len(actual):
        raise ValueError('nonempty aligned predictions required')
    return _number(_sum(_product(_number(a-b), _number(a-b)) for a,b in zip(pred,actual))/len(actual))


def run_experiment(scenario, seed=SEED):
    """Choose alpha only on tune MSE; evaluate frozen training-only fit.

    Returns chronological paired losses and descriptive MAE/RMSE. No inference.
    """
    rows = generate(scenario, seed)
    train, tune, valid = rows[:TRAIN_END], rows[TRAIN_END:TUNE_END], rows[TUNE_END:]
    if any(a['time'] >= b['time'] for a,b in zip(rows,rows[1:])):
        raise ValueError('time split invalid')
    if not train[-1]['time'] < tune[0]['time'] <= tune[-1]['time'] < valid[0]['time']:
        raise ValueError('time split invalid')
    xtrain = [r['features'] for r in train]; ytrain = [r['target'] for r in train]
    ytune = [r['target'] for r in tune]
    fitted = [(alpha, fit_ridge(xtrain, ytrain, alpha)) for alpha in ALPHAS]
    scores = [(alpha,_mse([predict_ridge(model,r['features']) for r in tune],ytune)) for alpha,model in fitted]
    selected = min(scores, key=lambda pair: (pair[1],pair[0]))[0]
    model = next(m for alpha,m in fitted if alpha == selected)
    mean_y = _number(_sum(ytrain)/len(ytrain))
    actual = [r['target'] for r in valid]
    predictions = {'mean': [mean_y]*len(valid), 'persistence': [r['previous'] for r in valid],
                   'ridge': [predict_ridge(model,r['features']) for r in valid]}
    losses = {name:[_product(_number(a-b),_number(a-b)) for a,b in zip(pred,actual)]
              for name,pred in predictions.items()}
    metrics = {}
    for name,pred in predictions.items():
        mae = _number(_sum(abs(_number(a-b)) for a,b in zip(pred,actual))/len(actual))
        rmse = _number(math.sqrt(_mse(pred,actual)))
        metrics[name] = {'mae':mae,'rmse':rmse}
    return {'scenario':scenario,'seed':seed,'split':{'train':(0,119),'tune':(120,179),'validation':(180,239)},
            'alpha_candidates':ALPHAS,'selected_alpha':selected,'tune_mse':tuple(scores),
            'refit':'training_only','validation_time':tuple(r['time'] for r in valid),
            'metrics':metrics,'losses':losses,
            'loss_difference_ridge_minus_mean': tuple(_number(a-b) for a,b in zip(losses['ridge'],losses['mean'])),
            'loss_difference_ridge_minus_persistence': tuple(_number(a-b) for a,b in zip(losses['ridge'],losses['persistence'])),
            'model':model, 'conclusion_scope':'synthetic_descriptive_only'}
