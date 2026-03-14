import json
from openai import AsyncOpenAI

# 1. Инициализация клиента OpenAI (добавь к остальным переменным сверху)
aclient = AsyncOpenAI(api_key=OPENAI_KEY)

# 2. Новый хэндлер: ловит все текстовые сообщения в группе/канале
# Предполагается, что бот добавлен в группу и имеет права администратора (чтобы удалять)
@dp.message(F.text & ~F.text.startswith('/'))
async def process_free_text_ad(message: types.Message):
    user_id = message.from_user.id
    
    # 1. Получаем историю юзера из БД для экономии токенов
    try:
        res = supabase.table(TAXI_TABLE).select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        past_data = res.data[0] if res.data else {}
    except Exception as e:
        logging.error(f"Ошибка БД: {e}")
        past_data = {}

    # 2. Формируем промпт для GPT
    # Просим возвращать null, если данных нет, чтобы потом подставить из БД
    prompt = f"""
    Проанализируй текст и извлеки данные для объявления попутки (такси/передача). 
    Языки: кыргызский, русский или смесь.
    Текст: "{message.text}"
    
    Верни строго JSON объект со следующими ключами:
    "is_ad": boolean (true если это объявление о поиске машины/пассажира/посылки, false если просто общение),
    "role": string ("айдоочу", "жүргүнчү", "посылка" или null),
    "origin": string (откуда выезд, или null),
    "destination": string (куда едут, или null),
    "time": string (время/дата отправления, или null),
    "price": string (цена, или null),
    "passenger_count": string (количество мест/людей, или null),
    "phone_number": string (номер телефона, или null),
    "car_model": string (марка машины, или null)
    """

    try:
        # 3. Вызов OpenAI API (используем gpt-4o-mini и строгий формат JSON)
        response = await aclient.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2 # Низкая температура для точности
        )
        
        parsed_data = json.loads(response.choices[0].message.content)
        
        # Если GPT решил, что это просто сообщение (например "Привет всем"), игнорируем
        if not parsed_data.get("is_ad"):
            return

        # 4. Склеиваем данные: приоритет у GPT, запасной вариант - БД
        role = parsed_data.get("role") or past_data.get("role", "айдоочу")
        origin = parsed_data.get("origin") or past_data.get("origin", "Такталган жок")
        destination = parsed_data.get("destination") or past_data.get("destination", "Такталган жок")
        time = parsed_data.get("time") or "Сүйлөшүү боюнча"
        price = parsed_data.get("price") or past_data.get("price", "Келишим баада")
        passenger_count = parsed_data.get("passenger_count") or past_data.get("passenger_count", "1")
        phone = parsed_data.get("phone_number") or past_data.get("phone_num", "Номери жок")
        car_model = parsed_data.get("car_model") or past_data.get("car_model", "Көрсөтүлгөн жок")

        # 5. Удаляем оригинальное сообщение юзера (бот должен быть админом!)
        try:
            await message.delete()
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение: {e}")

        # 6. Формируем красивое сообщение
        clean_phone = phone.replace(" ", "").replace("-", "")
        if not clean_phone.startswith('+') and clean_phone.isdigit(): 
            clean_phone = '+' + clean_phone

        role_name = "АЙДООЧУ" if role == "айдоочу" else "ЖҮРГҮНЧҮ" if role == "жүргүнчү" else "ПОСЫЛКА"
        icon = "🚕" if role == "айдоочу" else "👤" if role == "жүргүнчү" else "📦"

        text = (f"{icon} <b>{role_name}</b>\n\n"
                f"📍 <b>Маршрут</b>: {origin} ➡️ {destination}\n"
                f"🕒 <b>Убакыт</b>: {time}\n")
        
        if role == "айдоочу":
            text += f"🚗 <b>Унаа</b>: {car_model}\n💰 <b>Баасы</b>: {price}\n"
            
        label = 'Орун' if role == 'айдоочу' else 'Адам'
        text += (f"👥 <b>{label}</b>: {passenger_count}\n"
                 f"📞 <b>Тел.</b>: <a href='tel:{clean_phone}'><code>{phone}</code></a>\n\n"
                 f"👤 <b>Жарыя ээси</b>: <a href='tg://user?id={user_id}'>{message.from_user.full_name}</a>")

        # 7. Отправляем отформатированный пост
        msg = await bot.send_message(
            chat_id=message.chat.id, # Или CHANNEL_ID, смотря где бот должен публиковать
            text=text, 
            parse_mode="HTML", 
            reply_markup=get_channel_publish_kb()
        )
        
        # 8. Сохраняем новые актуальные данные в БД
        db_payload = {
            "user_id": user_id, 
            "role": role, 
            "origin": origin,
            "destination": destination,
            "time": time, 
            "passenger_count": str(passenger_count), 
            "phone_num": phone, 
            "car_model": car_model, 
            "price": price, 
            "message_id": msg.message_id,
            "created_at": datetime.datetime.now(TZ_BISHKEK).isoformat()
        }
        supabase.table(TAXI_TABLE).insert(db_payload).execute()

    except Exception as e:
        logging.error(f"Ошибка GPT/Отправки: {e}")