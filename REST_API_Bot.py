import logging
import sqlite3
from datetime import datetime, timedelta
from time import sleep
import requests
import random

from telegram import Update
from telegram.ext import Dispatcher, CommandHandler, CallbackContext, Updater

import okx.Account as Account
import okx.Trade as Trade
import okx.MarketData as MarketData

from config import (
    API_KEY,
    SECRET_KEY,
    PASSPHRASE,
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    ALLOWED_USER_IDS,
    TA_API_KEY
    )

api_key = API_KEY
secret_key = SECRET_KEY
passphrase = PASSPHRASE

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка sqlite
conn = sqlite3.connect('trading_bot.db')
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    profit REAL NOT NULL,
                    open_time TIMESTAMP NOT NULL,
                    close_time TIMESTAMP
                )''')
conn.commit()

# Получить профит за период
def get_profit_by_period(period):
    query = f"""
    SELECT SUM(profit) 
    FROM trades 
    WHERE close_time >= datetime('now', '-{period} days')
    """
    cursor.execute(query)
    result = cursor.fetchone()
    return result[0] if result[0] else 0

# Настройка телеграм-бота
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# Функция для отправки сообщений в Telegram
def send_telegram_message(message):
    updater.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

# Функция декоратор для запрета пользования бота с неавториованных ID
def restricted(func):
    def wrapped(update, context):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USER_IDS:
            update.message.reply_text("Доступ запрещен.")
            return
        return func(update, context)
    return wrapped

def start_tg_bot(update, context):
    chat = update.effective_chat
    user_id = update.effective_user.id
    if user_id in ALLOWED_USER_IDS:
        context.bot.send_message(chat_id=chat.id, text="OK. You're authrized")
    else:
        context.bot.send_message(chat_id=chat.id, text="403 Forbidden")

@restricted
def day_profit(update: Update, context: CallbackContext):
    profit = get_profit_by_period(1)
    update.message.reply_text(f"Профит за день: {profit:.2f}")

@restricted
def week_profit(update: Update, context: CallbackContext):
    profit = get_profit_by_period(7)
    update.message.reply_text(f"Профит за неделю: {profit:.2f}")

@restricted
def month_profit(update: Update, context: CallbackContext):
    profit = get_profit_by_period(30)
    update.message.reply_text(f"Профит за месяц: {profit:.2f}")

# Регистрируем обработчики команд tg бота
dispatcher.add_handler(CommandHandler('day_profit', day_profit))
dispatcher.add_handler(CommandHandler('week_profit', week_profit))
dispatcher.add_handler(CommandHandler('month_profit', month_profit))
dispatcher.add_handler(CommandHandler('start', start_tg_bot))


flag = "1"  # live trading: 0, demo trading: 1

accountAPI = Account.AccountAPI(api_key, secret_key, passphrase, False, flag)
tradeAPI = Trade.TradeAPI(api_key, secret_key, passphrase, False, flag)
marketDataAPI = MarketData.MarketAPI(flag = flag)
logger.info('Настройка прошла успешно')

# Генерируем OrderID(у OKX очень странно работает этот параметр, по этому используем свой)
def generate_clOrdId():
    clOrdId = ''
    for x in range(16):
        clOrdId = clOrdId + random.choice(list('1234567890abcdefghigklmnopqrstuvyxwzABCDEFGHIGKLMNOPQRSTUVYXWZ'))
    return clOrdId


# Получение текущего тренда с использованием индикатора DMI
def get_trend():
    url = 'https://api.taapi.io/dmi'
    params = {
        'secret': TA_API_KEY,
        'exchange': 'binance',
        'symbol': 'BTC/USDT',
        'interval': '1h',
    }
    response = requests.get(url, params=params)
    trend_data = response.json()

    if abs(trend_data['plusdi'] - trend_data['minusdi']) >= 5:
        if trend_data['plusdi'] > trend_data['minusdi']:
            return 'long'
        else:
            return 'short'
    else:
        return 'neutral'
    

#Получить баланс
def get_balance():
    result = accountAPI.get_account_balance()
    logger.info('Получение данных баланса прошло успешно')
    return(float(result['data'][0]['details'][0]['availBal']))

#Установить плечо
def set_leverage(leverage, posSide):
    result = accountAPI.set_leverage(
    instId = "BTC-USDT-SWAP",
    lever = leverage,
    mgnMode = "isolated",
    posSide = posSide
    )
    logger.info('Размер плеча установлен')
    return result

def set_position_mode():
    accountAPI.set_position_mode(posMode="long_short_mode")
    logger.info('Позиция установлена')

#Получить информацию по ордеру
def get_positions():
    result = accountAPI.get_positions()
    print(result)

# Разместить ордер
def place_order(side, posSide, sz):
    balance_before = get_balance()
    order = tradeAPI.place_order(
        clOrdId = generate_clOrdId(),
        instId = "BTC-USDT-SWAP",
        tdMode = "isolated",
        side = side,
        posSide = posSide,
        ordType = "market",
        sz = sz,
        tag = posSide
    )
    trade_id = order["data"][0]["clOrdId"]
    #trade_detail = get_order_detail(trade_id)
    
    if order["code"] == "0":
        balance_afer = get_balance()
        amount = balance_before - balance_afer
        logger.info(f"Successful order request，order_id = {trade_id}")
        send_telegram_message(f"Открыта сделка {order['data'][0]['tag']} на сумму {amount}, текущий баланс {get_balance()}")
        cursor.execute("INSERT INTO trades (trade_type, amount, profit, open_time) VALUES (?, ?, 0, ?)",
               (str(order['data'][0]['tag']), amount, datetime.now()))
        conn.commit()
    else:
        logger.warning("Unsuccessful order request，error_code = ",order["data"][0]["sCode"], ", Error_message = ", order["data"][0]["sMsg"])
        send_telegram_message('Ошибка открытия сделки')
    
    return trade_id

def main():
    set_leverage('50', 'short')
    set_position_mode()
    trade_id = place_order('sell', 'short', '2')


if __name__ == "__main__":
    main()
