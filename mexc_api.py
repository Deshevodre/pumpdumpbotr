# ┌─────────────────────────────────────────────┐
# │ БЛОК 1: Импорты и патч для pandas_ta        │
# └─────────────────────────────────────────────┘
import time
import hmac
import hashlib
import requests
import mplfinance as mpf  # добавьте в начало файла, рядом с другими import
import pandas as pd
import numpy as np            # ← здесь
# Патч: многие версии pandas_ta по-прежнему делают `from numpy import NaN`,
# а в numpy>=1.24 атрибут NaN отсутствует.
# Мы вручную заводим его:
np.NaN = np.nan              # ← вот это создаёт нужный атрибут  # ← чтобы optimize_thresholds видел её

import pandas_ta as ta
import matplotlib.pyplot as plt

# ┌─────────────────────────────────────────────┐
# │ БЛОК 2: Конфигурация MEXC API                │
# └─────────────────────────────────────────────┘
import requests
MEXC_BASE = 'https://api.mexc.com'

MEXC_BASE      = 'https://api.mexc.com'
# ┌─────────────────────────────────────────────┐
# │ Блок 2.1: URL фьючерсного API               │
# └─────────────────────────────────────────────┘
FUTURES_BASE = 'https://contract.mexc.com/api/v1/contract'

API_KEY        = 'mx0vgl61aC7fwxhGEL'        # ← сюда вставить ваш ключ
API_SECRET     = '3ee6be17a2644e33b810d1e4fe74acda'     # ← сюда вставить ваш секрет
PUMP_THRESHOLD = 25                # % роста для пампа
RSI_THRESHOLD  = 80                   # порог перекупленности для дампа
VOL_FACTOR     = 3                    # множитель для всплеска объёма
MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIGNAL    = 9
MIN_24H_VOLUME_USDT = 200_000
# ┌─────────────────────────────────────────────┐
# │ БЛОК 2.1: Список базовых активов для исключения │
# └─────────────────────────────────────────────┘
EXCLUDE_BASE = {
    'BTC','ETH','SOL','BNB','LTC','DOGE',
    'AVAX','TRX','ADA','MATIC','XRP'
}

# ┌─────────────────────────────────────────────┐
# │ БЛОК 3.0: Получение всех фьючерсных тикеров │
# └─────────────────────────────────────────────┘
def get_all_tickers():
    """
    Фьючерсные тикеры MEXC: возвращает список словарей,
    каждый со 'symbol' (например, 'BTC_USDT') и 'lastPriceChangePercent'.
    """
    url = f"{FUTURES_BASE}/ticker"
    resp = requests.get(url)
    data = resp.json()
    # В разных версиях API data может быть либо {'success':..., 'data':[ ... ]}
    tickers = data.get('data', data)
    return tickers


# ┌─────────────────────────────────────────────┐
# │ БЛОК 3: Универсальный get_klines с фоллбэком│
# └─────────────────────────────────────────────┘
from requests.exceptions import RequestException

def get_klines(symbol, interval='1m', limit=100):
    """
    Сначала пытаемся спотовый API (MEXC_BASE).
    Если для 1h-запроса() вернулось менее 2 строк,
    пробуем фьючерсный (FUTURES_BASE/kline).
    """
    def parse(raw):
        # raw — либо список, либо {'data': [ ... ]}
        data = raw if isinstance(raw, list) else raw.get('data', [])
        if not isinstance(data, list) or not data:
            return pd.DataFrame(columns=['ts','open','high','low','close','volume'])
        df = pd.DataFrame(data)
        df = df.iloc[:, :6]
        df.columns = ['ts','open','high','low','close','volume']
        df[['open','high','low','close','volume']] = \
            df[['open','high','low','close','volume']].astype(float)
        return df

    spot = symbol.replace('_','')
    spot_url = f"{MEXC_BASE}/api/v3/klines?symbol={spot}&interval={interval}&limit={limit}"
    try:
        raw_spot = requests.get(spot_url, timeout=5).json()
        df = parse(raw_spot)
    except RequestException:
        df = pd.DataFrame(columns=['ts','open','high','low','close','volume'])

    # Если это запрос 1h и меньше 2 строк — фоллбэк на futures
    if interval.endswith('h') and df.shape[0] < 2:
        fut_url = f"{FUTURES_BASE}/kline?symbol={symbol}&interval={interval}&limit={limit}"
        try:
            raw_fut = requests.get(fut_url, timeout=5).json()
            df_fut = parse(raw_fut)
            if df_fut.shape[0] >= 2:
                return df_fut
        except RequestException:
            pass

    return df





# ┌─────────────────────────────────────────────┐
# │ БЛОК 3.1: Фильтрация по 24h объёму          │
# └─────────────────────────────────────────────┘
def filter_by_volume(tickers):
    """
    Оставляем только пары с quoteVolume ≥ MIN_24H_VOLUME_USDT
    """
    out = []
    for t in tickers:
        if float(t.get('quoteVolume', 0)) >= MIN_24H_VOLUME_USDT:
            out.append(t)
    return out


# ┌─────────────────────────────────────────────┐
# │ БЛОК 4: Детект пампа среди фьючерсов        │
# └─────────────────────────────────────────────┘
def detect_pumps():
    all_tickers = get_all_tickers()
    print(f"[DEBUG][pump] Всего фьючерсных пар всего: {len(all_tickers)}")

    # 1) Оставляем только альткоины XXX_USDT (без крупных баз)
    tickers = [
        t for t in all_tickers
        if t['symbol'].endswith('_USDT')
        and t['symbol'].split('_')[0] not in EXCLUDE_BASE
    ]
    print(f"[DEBUG][pump] После фильтра альткоинов: {len(tickers)}")

    # (Опционально) Фильтрация по объёму (amount24) — раскомментируйте, если нужно:
    # tickers = [t for t in tickers if float(t['amount24']) >= MIN_24H_VOLUME_USDT]
    # print(f"[DEBUG][pump] После фильтрации объёма: {len(tickers)}")

    # 2) Собираем (symbol, % изменения за 24ч) из riseFallRate*100
    candidates = []
    for t in tickers:
        sym = t['symbol']
        pct = float(t.get('riseFallRate', 0)) * 100
        candidates.append((sym, pct))
    candidates.sort(key=lambda x: x[1], reverse=True)

    # 3) Отладочный топ-10
    print("[DEBUG][pump] Топ-10 альткоинов по % изменения:")
    for sym, ch in candidates[:10]:
        print(f"    {sym} → {ch:.2f}%")

    # 4) Отбор по порогу
    pumped = [(sym, ch) for sym, ch in candidates if ch >= PUMP_THRESHOLD]
    print(f"[DEBUG][pump] После порога PUMP_THRESHOLD={PUMP_THRESHOLD}%: {len(pumped)}")

    return pumped






# ┌─────────────────────────────────────────────┐
# │ БЛОК 5: Детект признаков «готового дампа»     │
# └─────────────────────────────────────────────┘
def get_rsi(symbol):
    df = get_klines(symbol, '1m', 50)
    df['rsi'] = ta.rsi(df['close'], length=14)
    return df['rsi'].iloc[-1]

def rsi_decreasing(symbol):
    df = get_klines(symbol, '1m', 15)
    df['rsi'] = ta.rsi(df['close'], length=14)
    return df['rsi'].iloc[-2] > df['rsi'].iloc[-1]

def detect_volume_spike(symbol):
    df = get_klines(symbol, '1m', 6)
    last = df['volume'].iloc[-1]
    avg = df['volume'][:-1].mean()
    return last >= avg * VOL_FACTOR

def detect_pin_bar(symbol):
    df = get_klines(symbol, '1m', 3)
    o, c, h, l = df['open'].iloc[-1], df['close'].iloc[-1], df['high'].iloc[-1], df['low'].iloc[-1]
    body = abs(c - o)
    upper = h - max(c, o)
    lower = min(c, o) - l
    return upper >= body*2 and lower <= body*0.5
# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.1: Индикаторы и признаки дампа       │
# └─────────────────────────────────────────────┘

import requests
import pandas_ta as ta

def get_rsi(symbol):
    df = get_klines(symbol, '1m', 50)
    if df.empty:
        return 0.0
    rsi_series = ta.rsi(df['close'], length=14)
    return float(rsi_series.iloc[-1]) if not rsi_series.empty else 0.0

def rsi_decreasing(symbol):
    df = get_klines(symbol, '1m', 15)
    if df.shape[0] < 2:
        return False
    rsi_series = ta.rsi(df['close'], length=14)
    return float(rsi_series.iloc[-2]) > float(rsi_series.iloc[-1])

def detect_volume_spike(symbol):
    df = get_klines(symbol, '1m', 6)
    if df.shape[0] < 2:
        return False
    last_vol = df['volume'].iloc[-1]
    avg_vol = df['volume'][:-1].mean()
    return last_vol >= avg_vol * VOL_FACTOR

def detect_pin_bar(symbol):
    df = get_klines(symbol, '1m', 3)
    if df.shape[0] < 3:
        return False
    o, c, h, l = df['open'].iloc[-1], df['close'].iloc[-1], df['high'].iloc[-1], df['low'].iloc[-1]
    body = abs(c - o)
    upper = h - max(c, o)
    lower = min(c, o) - l
    return upper >= body * 2 and lower <= body * 0.5

def get_macd(symbol):
    df = get_klines(symbol, '1m', 100)
    if df.shape[0] < MACD_SLOW:
        return False
    macd_df = ta.macd(
        df['close'],
        fast=MACD_FAST,
        slow=MACD_SLOW,
        signal=MACD_SIGNAL
    )
    hist_col = f"MACDh_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    if hist_col not in macd_df:
        return False
    return float(macd_df[hist_col].iloc[-1]) < 0

def detect_orderbook_imbalance(symbol, threshold=0.7):
    """
    Берём стакан со спотового API MEXC:
      GET {MEXC_BASE}/api/v3/depth?symbol=XXXUSDT&limit=10
    JSON гарантированно содержит 'bids' и 'asks'.
    Любые ошибки или отсутствие полей — возвращаем False.
    """
    spot = symbol.replace('_', '')
    url = f"{MEXC_BASE}/api/v3/depth?symbol={spot}&limit=10"

    try:
        resp = requests.get(url, timeout=5)
        data = resp.json()
    except Exception as e:
        print(f"[DEBUG][Orderbook] Ошибка HTTP для {symbol}: {e}")
        return False

    if not isinstance(data, dict):
        print(f"[DEBUG][Orderbook] Некорректный ответ (не dict) для {symbol}: {data}")
        return False

    bids = data.get('bids')
    asks = data.get('asks')

    if bids is None or asks is None:
        nested = data.get('data')
        if isinstance(nested, dict):
            bids = nested.get('bids')
            asks = nested.get('asks')

    if not isinstance(bids, list) or not isinstance(asks, list):
        print(f"[DEBUG][Orderbook] Нет bids/asks для {symbol}: bids={bids}, asks={asks}")
        return False

    try:
        bid_vol = sum(float(lvl[1]) for lvl in bids if len(lvl) > 1)
        ask_vol = sum(float(lvl[1]) for lvl in asks if len(lvl) > 1)
    except Exception as e:
        print(f"[DEBUG][Orderbook] Ошибка подсчёта объёмов для {symbol}: {e}")
        return False

    total = bid_vol + ask_vol
    if total <= 0:
        return False

    imbalance = (ask_vol / total) >= threshold
    print(f"[DEBUG][Orderbook] {symbol}: bid={bid_vol:.1f}, ask={ask_vol:.1f}, imbalance={imbalance}")
    return imbalance

def get_vwap(symbol, interval='1m', limit=50):
    df = get_klines(symbol, interval, limit)
    if df.empty:
        return 0.0
    return float((df['close'] * df['volume']).sum() / df['volume'].sum())

def detect_vwap_deviation(symbol, dev=0.02):
    df = get_klines(symbol, '1m', 1)
    if df.empty:
        return False
    price = df['close'].iloc[-1]
    vwap = get_vwap(symbol)
    return (price - vwap) / vwap >= dev
# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.3: Volatility Breakout               │
# └─────────────────────────────────────────────┘
def detect_volatility_breakout(symbol, interval='1h', atr_len=14, k=1.5):
    """
    Если (High − Low) текущей свечи > k * ATR(atr_len), сигнал Breakout.
    """
    df = get_klines(symbol, interval, atr_len+2)
    if df.shape[0] < atr_len+2:
        return False
    # ATR
    high_low = df['high'] - df['low']
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        high_low,
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_len).mean().iloc[-1]
    curr_range = high_low.iloc[-1]
    return curr_range > k * atr
# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.4: Объёмная фильтрация               │
# └─────────────────────────────────────────────┘
def detect_hourly_volume_spike(symbol, factor=2.0):
    """
    Сравниваем объём последнего часа с 24h средним:
      объём 1h > factor * средний 1h за сутки
    """
    df1h = get_klines(symbol, '1h', 25)  # 24+1
    if df1h.shape[0] < 25:
        return False
    last_vol = df1h['volume'].iloc[-1]
    avg_vol = df1h['volume'][:-1].mean()
    return last_vol > factor * avg_vol

# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.5: Простейшая кластер-аналитика       │
# └─────────────────────────────────────────────┘
def detect_order_flow_cluster(symbol, depth=20, imbalance_thresh=0.8):
    """
    Берём спот-стакан depth уровней и считаем bid/ask imbalance.
    Если ask/(bid+ask) > imbalance_thresh — кластерный сигнал.
    """
    spot = symbol.replace('_','')
    data = requests.get(f"{MEXC_BASE}/api/v3/depth?symbol={spot}&limit={depth}").json()
    bids, asks = data.get('bids',[]), data.get('asks',[])
    bid_v = sum(float(l[1]) for l in bids)
    ask_v = sum(float(l[1]) for l in asks)
    total = bid_v + ask_v
    return total>0 and (ask_v/total)>=imbalance_thresh


# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.2 (финальный): Комбо-сигнал          │
# └─────────────────────────────────────────────┘
def is_ready_to_dump(symbol):
    flags = {
        'RSI': get_rsi(symbol)>=RSI_THRESHOLD and rsi_decreasing(symbol),
        'VolSpike': detect_volume_spike(symbol),
        'PinBar': detect_pin_bar(symbol),
        'MACD': get_macd(symbol),
        'VolBreak': detect_volatility_breakout(symbol),
        '1hVolSpike': detect_hourly_volume_spike(symbol),
        'Cluster': detect_order_flow_cluster(symbol),
        'PA': bool(detect_price_action(symbol)),
        'Funding': get_funding_rate(symbol)>0.01,
        'ML': predict_ml(symbol)>0.8
    }
    # Собираем те, что True
    reasons = [k for k,v in flags.items() if v]
    # Требуем минимум 2–3 сигналов из списка «ключевых»:
    key = ['VolBreak','1hVolSpike','Cluster','PA','ML']
    if sum(1 for k in key if flags.get(k)) >= 2:
        return reasons
    # Иначе — очень жёсткий: лишь RSI+VolSpike combo
    if flags['RSI'] and flags['VolSpike']:
        return reasons
    return []






# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.4: VWAP и отклонение цены от VWAP    │
# └─────────────────────────────────────────────┘
def get_vwap(symbol, interval='1m', limit=50):
    """Вычисляем VWAP за последние `limit` свечей."""
    df = get_klines(symbol, interval, limit)
    vwap = (df['close'] * df['volume']).sum() / df['volume'].sum()
    return vwap

def detect_vwap_deviation(symbol, dev=0.02):
    """
    True, если текущая цена > VWAP на dev (по умолчанию +2%).
    """
    price = get_klines(symbol, '1m', 1)['close'].iloc[-1]
    vwap = get_vwap(symbol)
    return (price - vwap) / vwap >= dev

# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.6: Price Action паттерны             │
# └─────────────────────────────────────────────┘
def detect_price_action(symbol):
    """
    Ищем на 15m графике сильные разворотные свечи:
     - Медвежье поглощение
     - Падающая звезда
    """
    df = get_klines(symbol, '15m', 5)
    if df.shape[0] < 3:
        return []
    pa = []
    o,c,h,l = df['open'], df['close'], df['high'], df['low']
    # медвежье поглощение: предыдущая зелёная, текущая красная с body > body_prev
    if c.iloc[-2]>o.iloc[-2] and c.iloc[-1]<o.iloc[-1] and abs(c.iloc[-1]-o.iloc[-1])>abs(c.iloc[-2]-o.iloc[-2]):
        pa.append("Bearish Engulfing")
    # падающая звезда: длинный верхний фитиль
    body = abs(c.iloc[-1]-o.iloc[-1])
    upper = h.iloc[-1]-max(c.iloc[-1], o.iloc[-1])
    lower = min(c.iloc[-1], o.iloc[-1]) - l.iloc[-1]
    if upper > 2*body and lower < body*0.5:
        pa.append("Shooting Star")
    return pa
# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.7: ML-предиктор                      │
# └─────────────────────────────────────────────┘
import pickle

# предположим, модель сохранена в 'model.pkl'
try:
    ML_MODEL = pickle.load(open('model.pkl','rb'))
except:
    ML_MODEL = None

def predict_ml(symbol):
    """
    Собираем фичи: current change, volume spike, RSI, MACD и т.д.
    Возвращаем вероятность успеха.
    """
    if ML_MODEL is None:
        return 0.0
    # пример: [change, rsi, macd_hist, vol_spike_flag]
    change = next((c for s,c in detect_pumps() if s==symbol), 0.0)
    rsi = get_rsi(symbol)
    macd = float(ta.macd(get_klines(symbol,'1m',100)['close']).iloc[-1])
    vol = detect_volume_spike(symbol)
    X = [[change, rsi, macd, float(vol)]]
    return ML_MODEL.predict_proba(X)[0][1]
# ┌─────────────────────────────────────────────┐
# │ БЛОК 5.8 (исправленный): Интеграция внешних данных │
# └─────────────────────────────────────────────┘
def get_funding_rate(symbol):
    """
    Пытаемся взять fundingRate по фьючерсам,
    но при любой сетевой ошибке или неверном формате возвращаем 0.0.
    """
    try:
        url = f"{FUTURES_BASE}/fundingRate?symbol={symbol}&limit=1"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        # сначала пробуем в data.data
        entry = data.get('data', {})
        # может быть списком
        if isinstance(entry, list) and entry:
            entry = entry[0]
        fr = float(entry.get('fundingRate', 0))
        return fr
    except Exception as e:
        print(f"[DEBUG][get_funding_rate] Ошибка для {symbol}: {e}")
        return 0.0

def get_social_sentiment(symbol):
    # оставляем заглушку
    return 0.5



# ┌─────────────────────────────────────────────┐
# │ БЛОК 6.1: Обновлённый plot_price_hourly     │
# └─────────────────────────────────────────────┘
def plot_price_hourly(symbol):
    """
    Возвращает путь к PNG со свечным графиком 1h за последние 100 свечей.
    Если данных нет — возвращает None.
    """
    df = get_klines(symbol, '1h', 100)
    if df.empty:
        print(f"[DEBUG][plot] Нет 1h-данных для {symbol}, график не строится")
        return None

    df = df.set_index('ts')
    df.index = pd.to_datetime(df.index, unit='ms')
    path = f'{symbol}_1h.png'
    mpf.plot(
        df,
        type='candle',
        style='classic',
        title=f'{symbol} — 1h',
        figsize=(8, 5),
        savefig=path
    )
    return path

