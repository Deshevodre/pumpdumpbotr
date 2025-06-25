# ┌─────────────────────────────────────────────┐
# │ stats_manager.py                            │
# └─────────────────────────────────────────────┘

# ┌─────────────────────────────────────────────┐
# │ БЛОК 1: Импорты                              │
# └─────────────────────────────────────────────┘
import json
import os
import pandas as pd
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import CallbackContext

from mexc_api import get_klines, PUMP_THRESHOLD


# ┌─────────────────────────────────────────────┐
# │ БЛОК S1: Логирование сигналов               │
# └─────────────────────────────────────────────┘
LOG_FILE = 'signals_log.json'

def log_signal(symbol, change, prev, curr, reasons, time: datetime = None):
    """Добавляем запись о сигнале в JSON-файл."""
    entry = {
        'time': (time or datetime.utcnow()).isoformat(),
        'symbol': symbol,
        'change': change,
        'prev': prev,
        'curr': curr,
        'reasons': reasons
    }
    if os.path.exists(LOG_FILE):
        arr = json.load(open(LOG_FILE))
    else:
        arr = []
    arr.append(entry)
    json.dump(arr, open(LOG_FILE, 'w'), indent=2)


# ┌─────────────────────────────────────────────┐
# │ БЛОК S2: Команда /stats                     │
# └─────────────────────────────────────────────┘
def stats_command(update: Update, context: CallbackContext):
    """
    Вывод последних 20 сигналов и базовых метрик:
    Winrate и средний P&L.
    """
    if not os.path.exists(LOG_FILE):
        update.message.reply_text("Нет данных по сигналам.")
        return

    arr = json.load(open(LOG_FILE))
    last = arr[-20:]
    wins = sum(1 for x in last if x['curr'] < x['prev'])
    total = len(last)
    avg_return = sum((x['curr'] - x['prev'])/x['prev']*100 for x in last) / total if total else 0.0

    text = (
        f"Последние {total} сигналов:\n"
        f"Winrate: {wins}/{total} = {wins/total:.1%}\n"
        f"Средний P&L: {avg_return:.2f}%"
    )
    update.message.reply_text(text)


# ┌─────────────────────────────────────────────┐
# │ БЛОК S3: Еженедельный отчёт (/weekly_report) │
# └─────────────────────────────────────────────┘
def weekly_report(context: CallbackContext):
    """
    Формирует отчёт за последние 7 дней на основе in-memory списка сигналов.
    (Если нужна статистика из файла, можно расширить аналогично stats_command.)
    """
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    # Читаем лог, фильтруем по дате
    if not os.path.exists(LOG_FILE):
        report = ["Нет данных для еженедельного отчёта."]
    else:
        arr = json.load(open(LOG_FILE))
        report = [f"Отчёт за неделю ({week_ago.date()}–{now.date()}):"]
        for entry in arr:
            t = datetime.fromisoformat(entry['time'])
            if t >= week_ago:
                report.append(f"{t.strftime('%Y-%m-%d %H:%M')} {entry['symbol']} +{entry['change']:.1f}%")
    # Отправляем в Telegram
    chat_id = context.job.context  # при запуске run_daily мы передаём CHAT_ID как context
    context.bot.send_message(chat_id=chat_id, text="\n".join(report))

