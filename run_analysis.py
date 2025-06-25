# run_analysis.py

# ┌─────────────────────────────────────────────┐
# │ Импорты для анализа и оптимизации           │
# └─────────────────────────────────────────────┘
from mexc_api import get_klines, detect_pumps, PUMP_THRESHOLD
import pandas as pd
from datetime import datetime, timedelta

# ┌─────────────────────────────────────────────┐
# │ БЛОК BT1: Функция backtest_signals          │
# └─────────────────────────────────────────────┘
def backtest_signals(days=7, pump_thresh=PUMP_THRESHOLD):
    logs = pd.read_json('signals_log.json')
    logs['time'] = pd.to_datetime(logs['time'])
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = logs[logs['time'] >= cutoff].copy()
    results = []
    for row in recent.itertuples():
        sym = row.symbol
        price_then = row.curr
        df1h = get_klines(sym, '1h', 2)
        if df1h.shape[0] < 2:
            continue
        price_later = df1h['close'].iloc[-1]
        ret = (price_later - price_then) / price_then * 100
        results.append({'symbol': sym, 'time': row.time, 'return_%': ret, 'success': ret < 0})
    df_res = pd.DataFrame(results)
    precision = df_res['success'].mean() if not df_res.empty else 0.0
    return df_res, precision

# ┌─────────────────────────────────────────────┐
# │ БЛОК BT2: Grid Search для параметров        │
# └─────────────────────────────────────────────┘
def optimize_thresholds(symbols, volbreak_range, vol1h_range, kcluster_range):
    best = {'precision': 0.0}
    for k in volbreak_range:
        for v in vol1h_range:
            for cl in kcluster_range:
                globals()['VOL_BREAK_K'] = k
                globals()['HOUR_VOL_FACTOR'] = v
                globals()['CLUSTER_DEPTH'] = cl
                df_res, prec = backtest_signals(days=3)
                if prec > best['precision']:
                    best = {'k': k, 'v': v, 'cl': cl, 'precision': prec}
    return best

# ┌─────────────────────────────────────────────┐
# │ Точка входа для анализа                   │
# └─────────────────────────────────────────────┘
def main():
    df, prec = backtest_signals(days=7)
    print(f"Precision за 7 дней: {prec:.2%}")
    syms = [s for s, _ in detect_pumps()]
    best = optimize_thresholds(syms, [1.2, 1.5], [1.2, 1.5], [10, 20])
    print("Лучшие параметры:", best)

if __name__ == '__main__':
    main()
