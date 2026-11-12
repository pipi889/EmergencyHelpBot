from flask import Flask, request, abort
results.append({
            "name": name.strip(), "lat": lat, "lng": lon, "distance": d,
            "type": "โรงพยาบาล" if is_hospital else "คลินิก",
        })

    results.sort(key=lambda h: h["distance"])
    # หากเจอจุดและอาคารของสถานที่เดียวกันที่อยู่ใกล้กัน ให้เหลือหนึ่งรายการ
    unique = []
    for h in results:
        duplicate = any(
            h["name"].casefold() == prev["name"].casefold()
            and distance_km(h["lat"], h["lng"], prev["lat"], prev["lng"]) < 0.15
            for prev in unique
        )
        if not duplicate:
            unique.append(h)
        if len(unique) == MAX_RESULTS:
            break
    return unique


def google_maps_nearby_url(lat, lon):
    # Google Maps ค้นหาตามตำแหน่งที่ผู้ใช้ส่งมา ไม่ใช่ตำแหน่งของเซิร์ฟเวอร์
    query = quote("โรงพยาบาล", safe="")
    return f"https://www.google.com/maps/search/{query}/@{lat},{lon},13z"


def google_maps_pin_url(lat, lon):
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def build_hospital_reply(hospitals, latitude, longitude):
    if not hospitals:
        return (
            "🏥 ไม่พบข้อมูลสถานพยาบาลในแผนที่ OpenStreetMap ภายใน 25 กม.\n"
            "ข้อมูลอาจไม่ครบถ้วน ลองดู Google Maps:\n"
            f"{google_maps_nearby_url(latitude, longitude)}\n\n"
            "🚑 ฉุกเฉินในไทยโทร 1669"
        )
    lines = ["🏥 สถานพยาบาลใกล้ตำแหน่งที่ส่งมา\n"]
    for i, h in enumerate(hospitals, 1):
        lines.append(
            f"{i}. {h['name']} ({h['type']})\n"
            f"📍 เส้นตรงประมาณ {h['distance']:.1f} กม.\n"
            f"🗺️ {google_maps_pin_url(h['lat'], h['lng'])}\n"
        )
    lines.append("ตรวจสอบเวลาเปิดบริการและเส้นทางก่อนเดินทาง\n🚑 ฉุกเฉินในไทยโทร 1669")
    return "\n".join(lines)


def search_and_send_hospitals(target_id, latitude, longitude):
    print("START SEARCH", flush=True)
    try:
        hospitals = find_nearby_hospitals(latitude, longitude)
        message = build_hospital_reply(hospitals, latitude, longitude)
    except Exception as exc:
        print("SEARCH ERROR:", repr(exc), flush=True)
        # สำคัญ: หาก Overpass ใช้ไม่ได้ ต้องไม่ปล่อยให้ผู้ใช้รอโดยไม่มีทางเลือก
        message = (
            "⚠️ ระบบรายชื่อโรงพยาบาลชั่วคราวไม่พร้อมใช้งาน\n"
            "เปิด Google Maps เพื่อค้นหาโรงพยาบาลใกล้ตำแหน่งที่ส่งมา:\n"
            f"{google_maps_nearby_url(latitude, longitude)}\n\n"
            "🚑 หากฉุกเฉินในไทย โทร 1669"
        )
    try:
        line_bot_api.push_message(target_id, TextSendMessage(text=message))
        print("PUSH SUCCESS", flush=True)
    except Exception as exc:
        print("PUSH ERROR:", repr(exc), flush=True)


@app.route("/")
def home():
    return "Emergency Help Bot Running", 200


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK", 200


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text.strip()
    print("RECEIVED MESSAGE:", user_text, flush=True)

    if any(term in user_text for term in (
        "ค้นหาโรงพยาบาล", "โรงพยาบาลใกล้ฉัน", "โรงพยาบาลใกล้เคียง", "หาโรงพยาบาล"
    )):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🏥 กรุณากด + ใน LINE เลือก 'ตำแหน่งที่ตั้ง' แล้วส่งพิกัดให้บอท\n🚑 หากฉุกเฉินในไทย โทร 1669")
        )
        return

    matched_keyword = next((k for k in KEYWORD_ORDER if k in user_text), None)
    if matched_keyword is None:
        line_bot_api.reply_message(event.reply_token, build_default_menu())
    elif matched_keyword in CASES:
        line_bot_api.reply_message(event.reply_token, build_flex(matched_keyword, CASES[matched_keyword]))
    elif matched_keyword in SIMPLE_REPLIES:
        if matched_keyword == "โทร 1669":
            line_bot_api.reply_message(event.reply_token, build_call_1669())
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=SIMPLE_REPLIES[matched_keyword]))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=DEFAULT_REPLY))


@handler.add(MessageEvent, message=LocationMessage)
def handle_location(event):
    latitude = event.message.latitude
    longitude = event.message.longitude
    print("LOCATION RECEIVED", flush=True)
    source = event.source
    target_id = (getattr(source, "group_id", None) or getattr(source, "room_id", None)
                 or getattr(source, "user_id", None))

    if not target_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ไม่พบห้องแชตสำหรับส่งผลค้นหา กรุณาลองใหม่"))
        return

    # ตอบรับก่อนเพื่อไม่ใช้ reply token หลังการเรียก Overpass ที่อาจล่าช้า
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="📍 ได้รับตำแหน่งแล้ว กำลังค้นหาสถานพยาบาลใกล้คุณ...")
    )
    threading.Thread(
        target=search_and_send_hospitals,
        args=(target_id, latitude, longitude),
        daemon=True,
    ).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
