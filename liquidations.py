import websocket
import json
import time
from datetime import datetime, time as dtime
import threading
import requests
import os
import sys
from colorama import init, Fore, Back, Style

# Initialize colorama
init()

BINANCE_FUTURES_WEBSOCKET_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
BINANCE_FUTURES_REST_API = "https://fapi.binance.com"

# Discord notification settings
DISCORD_WEBHOOK_URL = ""
NOTIFICATION_THRESHOLD = 50000  # $50,000, adjust as needed
NOTIFICATION_INTERVAL = 300  # 5 minutes in seconds

# Global variables
last_notification_time = 0
coin_liquidations = {}
coin_data = []
MIN_LIQUIDATION_VALUE = 0
TOTAL_PNL = 0
MAX_DISPLAY_ROWS = 15
STARTING_BALANCE = 10000  # Starting balance in USD
current_balance = STARTING_BALANCE
max_balance = STARTING_BALANCE
max_drawdown = 0

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def move_cursor(x, y):
    print(f"\033[{y};{x}H", end='')

def get_minimum_liquidation_value():
    while True:
        try:
            value = float(input("Enter the minimum liquidation value to scan for (in USD): "))
            if value <= 0:
                print("Please enter a positive number.")
            else:
                return value
        except ValueError:
            print("Invalid input. Please enter a number.")

def is_notification_time():
    now = datetime.now().time()
    start = dtime(7, 0)  # 7:00 AM
    end = dtime(23, 59, 59)  # 11:59:59 PM
    return start <= now <= end

def send_discord_notification(message):
    if is_notification_time():
        data = {
            "content": message,
            "username": "Liquidation Bot"
        }
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=data)
            response.raise_for_status()
            print(f"Notification sent to Discord: {message}")
        except requests.exceptions.RequestException as e:
            print(f"Failed to send Discord notification: {e}")
    else:
        print(f"Notification suppressed during quiet hours: {message}")

def send_startup_notification():
    message = "🚀 Liquidation Bot has started monitoring!"
    send_discord_notification(message)

def check_and_send_notification(symbol, liquidation_value):
    global last_notification_time, coin_liquidations
    
    current_time = time.time()
    
    if symbol not in coin_liquidations:
        coin_liquidations[symbol] = 0
    coin_liquidations[symbol] += liquidation_value
    
    if current_time - last_notification_time >= NOTIFICATION_INTERVAL:
        notifications_sent = False
        for coin, total_liq in coin_liquidations.items():
            if total_liq >= NOTIFICATION_THRESHOLD:
                message = f"🚨 Large liquidations for {coin} in the last 5 minutes: ${total_liq:,.2f}"
                send_discord_notification(message)
                notifications_sent = True
        
        if notifications_sent:
            summary_message = "📊 Liquidation Summary (last 5 minutes):\n"
            for coin, total_liq in sorted(coin_liquidations.items(), key=lambda x: x[1], reverse=True):
                if total_liq > 0:
                    summary_message += f"{coin}: ${total_liq:,.2f}\n"
            send_discord_notification(summary_message)
        
        coin_liquidations.clear()
        last_notification_time = current_time

def on_message(ws, message):
    global TOTAL_PNL, current_balance, max_balance, max_drawdown, coin_data
    data = json.loads(message)
    symbol = data['o']['s']
    side = data['o']['S']
    price = float(data['o']['p'])
    amount = float(data['o']['q'])
    
    value = price * amount
    
    if value >= MIN_LIQUIDATION_VALUE:
        existing_entry = next((item for item in coin_data if item['symbol'] == symbol), None)
        
        if existing_entry:
            existing_entry['last_liquidation'] = value
            existing_entry['total_liquidations'] += value
        else:
            new_entry = {
                'symbol': symbol,
                'side': 'N/A',
                'last_liquidation': value,
                'total_liquidations': value,
                'price_change_24h': 0,
                'position_size': 0,
                'entry_price': 0,
                'current_pnl': 0,
                'last_position_result': 'N/A',
                'stop_loss_price': 0,
                'take_profit_price': 0
            }
            coin_data.append(new_entry)
            
            if len(coin_data) > 15:
                coin_data.pop(0)
        
        check_and_send_notification(symbol, value)
        
        trade_side = 'SHORT' if side == 'BUY' else 'LONG'
        enter_trade(symbol, trade_side, price)

    if current_balance > max_balance:
        max_balance = current_balance
    
    drawdown = (max_balance - current_balance) / max_balance * 100
    if drawdown > max_drawdown:
        max_drawdown = drawdown

def enter_trade(symbol, side, price):
    global current_balance
    position = next((item for item in coin_data if item['symbol'] == symbol), None)
    if not position:
        return
    trade_amount = 100  # $100 per trade

    if current_balance >= trade_amount:
        current_balance -= trade_amount
        if position['position_size'] == 0:
            position['position_size'] = trade_amount
            position['entry_price'] = price
            position['side'] = side
        else:
            total_value = position['position_size'] * position['entry_price'] + trade_amount * price
            new_size = position['position_size'] + trade_amount
            position['entry_price'] = total_value / new_size
            position['position_size'] = new_size

        set_stop_loss_take_profit(symbol, side, position['entry_price'])
        update_position_pnl(symbol, price)
    else:
        print(f"Insufficient balance to enter trade for {symbol}")

def set_stop_loss_take_profit(symbol, side, entry_price):
    position = next((item for item in coin_data if item['symbol'] == symbol), None)
    if not position:
        return
    if side == 'LONG':
        position['stop_loss_price'] = entry_price * 0.90
        position['take_profit_price'] = entry_price * 1.05
    else:  # SHORT
        position['stop_loss_price'] = entry_price * 1.10
        position['take_profit_price'] = entry_price * 0.95

def update_position_pnl(symbol, current_price):
    global current_balance
    position = next((item for item in coin_data if item['symbol'] == symbol), None)
    if not position or position['position_size'] == 0:
        return
    if position['side'] == 'LONG':
        pnl_percentage = (current_price - position['entry_price']) / position['entry_price'] * 100
    else:  # SHORT
        pnl_percentage = (position['entry_price'] - current_price) / position['entry_price'] * 100
    
    new_pnl = position['position_size'] * pnl_percentage / 100
    pnl_change = new_pnl - position['current_pnl']
    current_balance += pnl_change
    position['current_pnl'] = new_pnl
    
    if (position['side'] == 'LONG' and current_price <= position['stop_loss_price']) or \
       (position['side'] == 'SHORT' and current_price >= position['stop_loss_price']):
        close_position(symbol, current_price, 'Stop Loss')
    elif (position['side'] == 'LONG' and current_price >= position['take_profit_price']) or \
         (position['side'] == 'SHORT' and current_price <= position['take_profit_price']):
        close_position(symbol, current_price, 'Take Profit')

def close_position(symbol, current_price, reason):
    global TOTAL_PNL, current_balance
    position = next((item for item in coin_data if item['symbol'] == symbol), None)
    if not position:
        return
    pnl = position['current_pnl']
    TOTAL_PNL += pnl
    current_balance += position['position_size']
    position['last_position_result'] = f"{reason}: {'Profit' if pnl > 0 else 'Loss'} ${abs(pnl):.2f}"
    position['position_size'] = 0
    position['current_pnl'] = 0
    position['entry_price'] = 0
    position['stop_loss_price'] = 0
    position['take_profit_price'] = 0

def on_error(ws, error):
    print(f"Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("Websocket connection closed")

def on_open(ws):
    print("Websocket connection opened")

def run_websocket():
    ws = websocket.WebSocketApp(BINANCE_FUTURES_WEBSOCKET_URL,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close,
                                on_open=on_open)
    
    while True:
        try:
            ws.run_forever()
        except Exception:
            time.sleep(5)

def update_price_data():
    while True:
        url = f"{BINANCE_FUTURES_REST_API}/fapi/v1/ticker/24hr"
        response = requests.get(url)
        data = response.json()
        
        for item in data:
            symbol = item['symbol']
            position = next((p for p in coin_data if p['symbol'] == symbol), None)
            if position:
                position['price_change_24h'] = float(item['priceChangePercent'])
                update_position_pnl(symbol, float(item['lastPrice']))
        
        time.sleep(60)  # Update every minute

def print_table_header():
    clear_screen()
    print(f"Minimum Liquidation Value: ${MIN_LIQUIDATION_VALUE:,.2f}")
    print(f"Starting Balance: ${STARTING_BALANCE:,.2f}")
    print(f"Current Balance: ${current_balance:,.2f}")
    print(f"Max Drawdown: {max_drawdown:.2f}%")
    print("Coin           Side  Last Liq($)    Total Liq($)    24hr Change(%)   Position($)   PNL($)    Entry      SL        TP        Last Result")
    print("-" * 160)

def print_table_row(symbol, data):
    side_color = Fore.RED if data['side'] == 'SHORT' else Fore.GREEN if data['side'] == 'LONG' else ''
    price_change_color = Fore.GREEN if data.get('price_change_24h', 0) >= 0 else Fore.RED
    pnl_color = Fore.GREEN if data['current_pnl'] >= 0 else Fore.RED
    
    return (f"{side_color}{symbol:<15}{data['side']:<6}{Style.RESET_ALL}"
            f"{data['last_liquidation']:>13,.0f}{data['total_liquidations']:>15,.0f}"
            f"{price_change_color}{data.get('price_change_24h', 0):>18.2f}%{Style.RESET_ALL}"
            f"{data['position_size']:>13,.2f}"
            f"{pnl_color}{data['current_pnl']:>10,.2f}{Style.RESET_ALL}"
            f"{data['entry_price']:>12.5f}{data['stop_loss_price']:>12.5f}{data['take_profit_price']:>12.5f}"
            f"{data['last_position_result']:>20}")

def update_table():
    global TOTAL_PNL
    
    move_cursor(0, 7)  # Move to the start of the table body
    
    for i, data in enumerate(reversed(coin_data)):
        print(print_table_row(data['symbol'], data))
        
    # Fill remaining rows with empty lines if less than 15 entries
    for i in range(15 - len(coin_data)):
        print(" " * 160)
        
    # Print the total PNL line
    print("-" * 160)
    print(f"Total PNL: {'$' + f'{TOTAL_PNL:.2f}':>148}")
    
    move_cursor(0, 7)  # Move cursor back to the top of the table
    sys.stdout.flush()

def main():
    global MIN_LIQUIDATION_VALUE
    MIN_LIQUIDATION_VALUE = get_minimum_liquidation_value()

    print("Initializing... This may take a moment.")
    
    # Send startup notification
    send_startup_notification()
    
    # Start WebSocket connection in a separate thread
    threading.Thread(target=run_websocket, daemon=True).start()
    
    # Start price update thread
    threading.Thread(target=update_price_data, daemon=True).start()

    # Print initial table
    print_table_header()
    for _ in range(MAX_DISPLAY_ROWS):
        print(" " * 160)  # Empty rows
    print("-" * 160)
    print(f"Total PNL: $0.00")

    try:
        while True:
            update_table()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)

if __name__ == "__main__":
    main()
