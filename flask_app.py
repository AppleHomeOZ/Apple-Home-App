import os
import sqlite3
import json
import requests
import traceback
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

BOT_TOKEN = "8581552391:AAHmLrG1yWOwUaDK1HdcWkISSbFtXSTkshc"
ADMIN_IDS = ["5146462288"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_FILE = os.path.join(os.path.dirname(__file__), 'repair.db')

def now_str(): return (datetime.utcnow() + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
def date_str(days=0): return (datetime.utcnow() + timedelta(hours=3, days=days)).strftime("%d.%m.%Y")

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT, device TEXT, damage TEXT,
            desc TEXT, budget TEXT, deadline TEXT, contact TEXT, username TEXT, photos TEXT DEFAULT '[]',
            status TEXT DEFAULT 'wait_photo', created_at TEXT,
            init_est TEXT DEFAULT '', final_diag TEXT DEFAULT '', part_cost INTEGER DEFAULT 0,
            work_cost INTEGER DEFAULT 0, total_price INTEGER DEFAULT 0,
            warranty_days INTEGER DEFAULT 0, warranty_end TEXT DEFAULT '',
            master_rcv INTEGER DEFAULT 0, client_rcv INTEGER DEFAULT 0,
            rating TEXT DEFAULT '', review TEXT DEFAULT ''
        )''')
        conn.execute('CREATE TABLE IF NOT EXISTS state (uid TEXT PRIMARY KEY, action TEXT, data TEXT)')

        for col_name, col_type in [("parts_quality", "TEXT DEFAULT ''"), ("refusal_reason", "TEXT DEFAULT ''")]:
            try: conn.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}")
            except: pass
        conn.commit()

init_db()

# --- БРОНЯ ОТ ПАДЕНИЙ PYTHONANYWHERE ---
def tg_post(method, payload):
    for _ in range(3): # Пробуем отправить 3 раза, если сервер тупит
        try:
            requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=10)
            break
        except requests.exceptions.RequestException:
            time.sleep(1)

def send_msg(chat_id, text, markup=None):
    payload = {"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"}
    if markup: payload["reply_markup"] = markup
    tg_post("sendMessage", payload)

def edit_msg(chat_id, msg_id, text, markup=None):
    payload = {"chat_id": str(chat_id), "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if markup: payload["reply_markup"] = markup
    tg_post("editMessageText", payload)

def kb_client_main(): return {"keyboard": [[{"text": "📱 Мои заказы"}]], "resize_keyboard": True}

@app.route('/api/order', methods=['POST', 'OPTIONS'])
def api_order():
    if request.method == 'OPTIONS': return "OK", 200
    try:
        d = request.json or {}
        cid = str(d.get("client_id", ""))
        device = f"{d.get('brand', '')} {d.get('model', '')}".strip()

        with get_db() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO orders (client_id, device, damage, desc, budget, deadline, contact, username, created_at, parts_quality)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (cid, device, d.get('damageType', ''), d.get('diagnosticDesc', ''), d.get('budget', ''),
                          d.get('deadline', ''), d.get('contact', ''), d.get('username', ''), now_str(), d.get('partsQuality', 'Не указано')))
            conn.commit()
            oid = c.lastrowid

        if cid:
            kb = {"keyboard": [[{"text": "Пропустить фото"}]], "resize_keyboard": True, "one_time_keyboard": True}
            send_msg(cid, f"✅ Заявка №{oid} принята.\nПожалуйста, отправьте фото устройства или нажмите кнопку ниже.", kb)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        for aid in ADMIN_IDS: send_msg(aid, f"⚠️ ОШИБКА В API (Заявка с сайта):\n{e}")
        return jsonify({"status": "error"}), 500

def notify_master(oid):
    with get_db() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    txt = (f"🔥 <b>Новый заказ #{o['id']}</b>\n"
           f"📱 {o['device']}\n"
           f"💎 Качество запчастей: {o['parts_quality']}\n"
           f"🔧 Проблема: {o['damage']}\n"
           f"💰 Бюджет: {o['budget']}\n"
           f"📞 {o['contact']} | @{o['username']}")

    if o["photos"] and o["photos"] != "[]":
        try:
            photos = json.loads(o["photos"])
            media = [{"type": "photo", "media": fid} for fid in photos[:5]]
            tg_post("sendMediaGroup", {"chat_id": str(ADMIN_IDS[0]), "media": json.dumps(media)})
        except: pass

    kb = {"inline_keyboard": [[{"text": "📝 Взять в работу (Оценить)", "callback_data": f"m_accept_{oid}"}]]}
    for aid in ADMIN_IDS: send_msg(aid, txt, kb)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        upd = request.json
        if not upd: return "OK", 200

        # ОБРАБОТКА ТЕКСТА
        if "message" in upd:
            msg = upd["message"]
            cid = str(msg["chat"]["id"])
            txt = msg.get("text", "")

            with get_db() as conn:
                state = conn.execute("SELECT * FROM state WHERE uid = ?", (cid,)).fetchone()

                if txt in ["/start", "Меню"]:
                    send_msg(cid, "Главное меню", kb_client_main())
                    return "OK", 200

                if state and state["action"] == "wait_review" and txt:
                    conn.execute("UPDATE orders SET review=? WHERE id=?", (txt, state["data"]))
                    conn.execute("DELETE FROM state WHERE uid=?", (cid,))
                    conn.commit()
                    send_msg(cid, "✅ Отзыв сохранен. Спасибо за доверие!", kb_client_main())
                    for aid in ADMIN_IDS: send_msg(aid, f"💬 Отзыв по заказу #{state['data']}:\n{txt}")
                    return "OK", 200

                # Ввод смет мастером
                if state and cid in ADMIN_IDS:
                    oid = state["data"]
                    cl_id = conn.execute("SELECT client_id FROM orders WHERE id=?", (oid,)).fetchone()[0]

                    if state["action"] == "wait_init":
                        conn.execute("UPDATE orders SET init_est=?, status='wait_init_app' WHERE id=?", (txt, oid))
                        conn.execute("DELETE FROM state WHERE uid=?", (cid,))
                        conn.commit()

                        send_msg(cl_id, f"📋 <b>Предварительное согласование (Заказ #{oid})</b>\n\n{txt}",
                                 {"inline_keyboard": [[{"text": "✅ Согласен", "callback_data": f"c_init_y_{oid}"}, {"text": "❌ Отказ", "callback_data": f"c_init_n_{oid}"}]]})

                        send_msg(cid, f"✅ Согласование отправлено.\nОжидаем ответа от клиента.")
                        return "OK", 200

                    elif state["action"] == "wait_final":
                        try:
                            parts = [p.strip() for p in txt.split('-')]
                            diag = parts[0]; p_cost = int(parts[1]); w_cost = int(parts[2])
                            tot = p_cost + w_cost
                            conn.execute("UPDATE orders SET final_diag=?, part_cost=?, work_cost=?, total_price=?, status='wait_fin_app' WHERE id=?", (diag, p_cost, w_cost, tot, oid))
                            conn.execute("DELETE FROM state WHERE uid=?", (cid,))
                            conn.commit()

                            send_msg(cl_id, f"📋 <b>Финальное согласование (Заказ #{oid})</b>\n\nРезультат: {diag}\n💰 Итоговая стоимость: <b>{tot}₽</b>",
                                     {"inline_keyboard": [[{"text": "✅ Утверждаю", "callback_data": f"c_fin_y_{oid}"}, {"text": "❌ Отказ", "callback_data": f"c_fin_n_{oid}"}]]})
                            send_msg(cid, f"✅ Финальная смета отправлена. Итог: {tot}₽")
                        except Exception:
                            send_msg(cid, "⚠️ Ошибка формата. Введите строго через дефис:\nЗамена стекла - 2000 - 3000")
                        return "OK", 200

                # Фото или кнопки
                if txt == "Пропустить фото":
                    conn.execute("UPDATE orders SET status='new' WHERE client_id=? AND status='wait_photo'", (cid,))
                    conn.commit()
                    send_msg(cid, "Заявка передана мастеру. Ожидайте ответа.", kb_client_main())
                    o = conn.execute("SELECT id FROM orders WHERE client_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
                    if o: notify_master(o["id"])
                    return "OK", 200

                if "photo" in msg:
                    o = conn.execute("SELECT id, photos FROM orders WHERE client_id=? AND status='wait_photo' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
                    if o:
                        ph = json.loads(o["photos"]) if o["photos"] else []
                        ph.append(msg["photo"][-1]["file_id"])
                        conn.execute("UPDATE orders SET photos=?, status='new' WHERE id=?", (json.dumps(ph), o["id"]))
                        conn.commit()
                        send_msg(cid, "Фото получено. Заявка передана мастеру.", kb_client_main())
                        notify_master(o["id"])
                    return "OK", 200

                if txt == "📱 Мои заказы":
                    orders = conn.execute("SELECT * FROM orders WHERE client_id=? ORDER BY id DESC LIMIT 5", (cid,)).fetchall()
                    if not orders:
                        send_msg(cid, "У вас пока нет заказов.")
                    else:
                        for o in orders:
                            rem = ""
                            if o['warranty_end']:
                                try:
                                    d_left = (datetime.strptime(o['warranty_end'], "%d.%m.%Y") - datetime.utcnow()).days
                                    rem = f"\n🛡 Гарантия до {o['warranty_end']} ({d_left} дн.)" if d_left > 0 else "\n🛡 Гарантия истекла"
                                except Exception: pass

                            st_map = {"canceled": "❌ Отменен", "archived": "✅ Завершен", "in_work": "⚙️ В работе", "ready": "🚀 Готов"}
                            human_st = st_map.get(o['status'], "В процессе")

                            hist_msg = f"📦 <b>Заказ #{o['id']}</b> | {o['device']}\nСтатус: {human_st}"
                            if o['total_price'] > 0: hist_msg += f"\n💰 Стоимость: {o['total_price']}₽"
                            if o['refusal_reason']: hist_msg += f"\nℹ️ Причина отмены: {o['refusal_reason']}"
                            hist_msg += rem
                            send_msg(cid, hist_msg)
                    return "OK", 200

        # ОБРАБОТКА ИНЛАЙН КНОПОК
        if "callback_query" in upd:
            call = upd["callback_query"]
            data = call["data"]
            cid = str(call["message"]["chat"]["id"])
            mid = call["message"]["message_id"]

            with get_db() as conn:
                parts = data.split('_')
                act = parts[0]; cmd = parts[1]

                if act == "m":
                    oid = parts[-1]
                    cl_id_row = conn.execute("SELECT client_id FROM orders WHERE id=?", (oid,)).fetchone()
                    cl_id = cl_id_row[0] if cl_id_row else None

                    if cmd == "accept":
                        conn.execute("INSERT OR REPLACE INTO state VALUES (?, 'wait_init', ?)", (cid, oid))
                        conn.commit()
                        edit_msg(cid, mid, f"✅ Вы взяли заказ #{oid}.")
                        send_msg(cid, f"✍️ Напишите предварительную стоимость и сроки (Заказ #{oid}):")

                    elif cmd == "final":
                        conn.execute("INSERT OR REPLACE INTO state VALUES (?, 'wait_final', ?)", (cid, oid))
                        conn.commit()
                        edit_msg(cid, mid, f"🔍 Диагностика заказа #{oid}.")
                        send_msg(cid, f"✍️ Введите финальную смету (Диагноз - Запчасть - Работа):")

                    elif cmd in ["work", "queue"]:
                        conn.execute("UPDATE orders SET status='in_work' WHERE id=?", (oid,))
                        conn.commit()
                        if cl_id: send_msg(cl_id, "🟡 Устройство принято в работу.")
                        edit_msg(cid, mid, f"🛠 Заказ #{oid} в работе.", {"inline_keyboard": [[{"text": "✅ Ремонт завершен", "callback_data": f"m_ready_{oid}"}]]})

                    elif cmd == "ready":
                        kb = {"inline_keyboard": [
                            [{"text": "14 дней", "callback_data": f"m_war_14_{oid}"}, {"text": "1 месяц", "callback_data": f"m_war_30_{oid}"}],
                            [{"text": "6 месяцев", "callback_data": f"m_war_180_{oid}"}, {"text": "1 год", "callback_data": f"m_war_365_{oid}"}]
                        ]}
                        edit_msg(cid, mid, f"Укажите срок гарантии для #{oid}:", kb)

                    elif cmd == "war":
                        days = int(parts[2])
                        end_d = date_str(days)
                        conn.execute("UPDATE orders SET status='ready', warranty_days=?, warranty_end=? WHERE id=?", (days, end_d, oid))
                        conn.commit()

                        war_text = {14: "14 дней", 30: "1 месяц", 180: "6 месяцев", 365: "1 год"}.get(days, f"{days} дней")
                        if cl_id: send_msg(cl_id, f"🚀 <b>Устройство готово!</b>\n🛡 Гарантия: {war_text}.\nМожете забирать.")
                        edit_msg(cid, mid, f"✅ Заказ #{oid} готов.", {"inline_keyboard": [[{"text": "🤝 Клиент забрал устройство", "callback_data": f"m_handover_{oid}"}]]})

                    elif cmd == "handover":
                        conn.execute("UPDATE orders SET master_rcv=1 WHERE id=?", (oid,))
                        conn.commit()
                        edit_msg(cid, mid, f"⏳ Ожидаем подтверждения выдачи от клиента...")
                        if cl_id: send_msg(cl_id, "🤝 Вы забрали устройство?", {"inline_keyboard": [[{"text": "✅ Да", "callback_data": f"c_rcv_y_{oid}"}, {"text": "❌ Нет", "callback_data": f"c_rcv_n_{oid}"}]]})

                    elif cmd == "close":
                        conn.execute("UPDATE orders SET status='archived' WHERE id=?", (oid,))
                        conn.commit()
                        edit_msg(cid, mid, f"📦 Сделка #{oid} закрыта (Архив).")

                    elif cmd == "lost":
                        conn.execute("UPDATE orders SET status='canceled', refusal_reason='Клиент не пришел' WHERE id=?", (oid,))
                        conn.commit()
                        edit_msg(cid, mid, f"🗑 Заказ #{oid} отменен (Клиент не явился).")

                    elif cmd == "reneg":
                        ans_reneg = parts[2]
                        if ans_reneg == "y":
                            conn.execute("INSERT OR REPLACE INTO state VALUES (?, 'wait_final', ?)", (cid, oid))
                            conn.commit()
                            edit_msg(cid, mid, f"✅ Введите новые условия (Диагноз - Запчасть - Работа) для #{oid}:")
                        else:
                            edit_msg(cid, mid, f"❌ Отказ по заказу #{oid}.", {"inline_keyboard": [[{"text": "В архив", "callback_data": f"m_close_{oid}"}]]})

                elif act == "c":
                    ans = parts[2] if len(parts) > 2 else ""
                    oid = parts[-1]

                    if cmd == "init":
                        if ans == "y":
                            conn.execute("UPDATE orders SET status='device_wait' WHERE id=?", (oid,))
                            conn.commit()
                            edit_msg(cid, mid, "✅ Ожидаем вас в сервисном центре.")
                            # КНОПКИ ДЛЯ МАСТЕРА ПОСЛЕ СОГЛАСИЯ
                            for aid in ADMIN_IDS: send_msg(aid, f"✅ Заказ #{oid}: Клиент согласен с предварительной оценкой.", {"inline_keyboard": [
                                [{"text": "🔍 Принял устройство (Диагностика)", "callback_data": f"m_final_{oid}"}],
                                [{"text": "❌ Отменить (Клиент не пришел)", "callback_data": f"m_lost_{oid}"}]
                            ]})
                        else:
                            conn.execute("UPDATE orders SET status='canceled' WHERE id=?", (oid,))
                            conn.commit()
                            edit_msg(cid, mid, "Укажите причину отказа:", {"inline_keyboard": [
                                [{"text": "Не устроила цена", "callback_data": f"c_refuse_price_{oid}"}],
                                [{"text": "Отпала необходимость", "callback_data": f"c_refuse_mind_{oid}"}]
                            ]})

                    elif cmd == "fin":
                        if ans == "y":
                            conn.execute("UPDATE orders SET status='fin_appr' WHERE id=?", (oid,))
                            conn.commit()
                            edit_msg(cid, mid, "✅ Стоимость утверждена.")
                            for aid in ADMIN_IDS: send_msg(aid, f"✅ Заказ #{oid}: Клиент утвердил финальную стоимость.", {"inline_keyboard": [
                                [{"text": "▶️ Начать работу", "callback_data": f"m_work_{oid}"}, {"text": "⏳ В очередь", "callback_data": f"m_queue_{oid}"}]
                            ]})
                        else:
                            conn.execute("UPDATE orders SET status='canceled' WHERE id=?", (oid,))
                            conn.commit()
                            edit_msg(cid, mid, "Укажите причину отказа:", {"inline_keyboard": [
                                [{"text": "Слишком дорого", "callback_data": f"c_refuse_price_{oid}"}],
                                [{"text": "Передумал", "callback_data": f"c_refuse_mind_{oid}"}]
                            ]})

                    elif cmd == "refuse":
                        reason = "Не устроила цена" if ans == "price" else "Отпала необходимость"
                        conn.execute("UPDATE orders SET refusal_reason=? WHERE id=?", (reason, oid))
                        conn.commit()

                        reneg_kb = {"inline_keyboard": [
                            [{"text": "📞 Договорились на новые условия", "callback_data": f"m_reneg_y_{oid}"}],
                            [{"text": "❌ Окончательный отказ", "callback_data": f"m_reneg_n_{oid}"}]
                        ]}

                        if ans == "price":
                            edit_msg(cid, mid, "Мастер свяжется с вами для обсуждения цены.")
                            for aid in ADMIN_IDS: send_msg(aid, f"🚨 <b>ОТКАЗ (Заказ #{oid})</b> - Дорого.\nСвяжитесь с клиентом. Удалось договориться?", reneg_kb)
                        else:
                            edit_msg(cid, mid, "Поняли вас. Будем рады помочь в будущем!")
                            for aid in ADMIN_IDS: send_msg(aid, f"❌ <b>ОТКАЗ (Заказ #{oid})</b> - {reason}.\nУдалось переубедить?", reneg_kb)

                    elif cmd == "rcv":
                        if ans == "y":
                            conn.execute("UPDATE orders SET client_rcv=1 WHERE id=?", (oid,))
                            conn.commit()
                            edit_msg(cid, mid, "Оцените нашу работу:", {"inline_keyboard": [
                                [{"text": "1⭐️", "callback_data": f"c_rate_1_{oid}"}, {"text": "3⭐️", "callback_data": f"c_rate_3_{oid}"}, {"text": "5⭐️", "callback_data": f"c_rate_5_{oid}"}]
                            ]})
                            for aid in ADMIN_IDS: send_msg(aid, f"✅ Заказ #{oid}: Клиент забрал устройство.", {"inline_keyboard": [[{"text": "📥 Закрыть сделку", "callback_data": f"m_close_{oid}"}]]})
                        else:
                            edit_msg(cid, mid, "Свяжитесь с мастером.")
                            for aid in ADMIN_IDS: send_msg(aid, f"⚠️ Клиент по #{oid} не подтвердил получение.")

                    elif cmd == "rate":
                        rate = parts[2]
                        conn.execute("UPDATE orders SET rating=? WHERE id=?", (rate, oid))
                        conn.execute("INSERT OR REPLACE INTO state VALUES (?, 'wait_review', ?)", (cid, oid))
                        conn.commit()
                        edit_msg(cid, mid, f"✅ {rate}⭐️. Напишите пару слов текстом:")
                        for aid in ADMIN_IDS: send_msg(aid, f"⭐️ Заказ #{oid} получил оценку {rate}⭐️!")

            return "OK", 200

    except Exception as e:
        err_msg = traceback.format_exc()
        for aid in ADMIN_IDS: send_msg(aid, f"⚠️ КРИТИЧЕСКАЯ ОШИБКА В WEBHOOK:\n\n{err_msg[-1500:]}")
        return "OK", 200

@app.route('/fix')
def fix_webhook():
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url=https://apple-home-app.onrender.com/webhook")
    return r.text

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
