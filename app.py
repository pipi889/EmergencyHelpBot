
from flask import Flask, request, abort
from dotenv import load_dotenv
import os
import requests
from math import radians, sin, cos, sqrt, atan2

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    LocationMessage
)

from content import CASES, SIMPLE_REPLIES, DEFAULT_REPLY, KEYWORD_ORDER
from flex_messages import build_flex, build_default_menu, build_call_1669

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise ValueError("กรุณาตรวจสอบ LINE_CHANNEL_ACCESS_TOKEN และ LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


# ==================================
# คำนวณระยะทางระหว่าง 2 พิกัด
# ==================================

def distance_km(lat1, lon1, lat2, lon2):

    R = 6371

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1))
        * cos(radians(lat2))
        * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


# ==================================
# ค้นหาโรงพยาบาลด้วย OpenStreetMap
# ==================================

def find_nearby_hospitals(latitude, longitude):

    urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter"
    ]

    query = f"""
    [out:json][timeout:30];
    (
      nwr["amenity"="hospital"]
        (around:10000,{latitude},{longitude});

      nwr["healthcare"="hospital"]
        (around:10000,{latitude},{longitude});
    );
    out center tags;
    """

    data = None

    # ทดลองเชื่อมต่อเซิร์ฟเวอร์ทีละตัว
    for url in urls:

        try:

            print("CONNECTING TO:", url)

            response = requests.get(
                url,
                params={"data": query},
                headers={
                    "User-Agent": "EmergencyHelpBot/1.0"
                },
                timeout=40
            )

            response.raise_for_status()

            data = response.json()

            print("OVERPASS SUCCESS")
            print(
                "FOUND ELEMENTS:",
                len(data.get("elements", []))
            )

            break

        except (requests.RequestException, ValueError) as e:

            print("OVERPASS ERROR:", str(e))

    if data is None:
        raise Exception("ไม่สามารถเชื่อมต่อฐานข้อมูลโรงพยาบาลได้")

    hospitals = []
    seen = set()

    for item in data.get("elements", []):

        tags = item.get("tags", {})

        # ตัดสถานพยาบาลสัตว์
        if tags.get("healthcare") == "veterinary":
            continue

        if tags.get("amenity") == "veterinary":
            continue

        # ชื่อโรงพยาบาล
        name = (
            tags.get("name")
            or tags.get("name:th")
            or tags.get("official_name")
            or tags.get("name:en")
        )

       if not name:
            name = "สถานพยาบาล (ไม่มีชื่อในฐานข้อมูล)"

        # พิกัด
        lat = item.get(
            "lat",
            item.get("center", {}).get("lat")
        )

        lng = item.get(
            "lon",
            item.get("center", {}).get("lon")
        )

        if lat is None or lng is None:
            continue

        # ป้องกันข้อมูลซ้ำ
        osm_id = (
            item.get("type"),
            item.get("id")
        )

        if osm_id in seen:
            continue

        seen.add(osm_id)

        # คำนวณระยะทาง
        distance = distance_km(
            latitude,
            longitude,
            lat,
            lng
        )

        hospitals.append({
            "name": name,
            "lat": lat,
            "lng": lng,
            "distance": distance
        })

    # เรียงจากใกล้ที่สุด
    hospitals.sort(
        key=lambda h: h["distance"]
    )

    return hospitals[:5]


# ==================================
# หน้าแรก
# ==================================

@app.route("/")
def home():

    return "Emergency Help Bot Running"


# ==================================
# LINE Webhook
# ==================================

@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers.get(
        "X-Line-Signature", ""
    )

    body = request.get_data(as_text=True)

    try:

        handler.handle(body, signature)

    except InvalidSignatureError:

        abort(400)

    return "OK"


# ==================================
# รับข้อความทั่วไป
# ==================================

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    user_text = event.message.text.strip()

    print("RECEIVED MESSAGE:", user_text)

    matched_keyword = None

    for keyword in KEYWORD_ORDER:

        if keyword in user_text:

            matched_keyword = keyword
            break

    if matched_keyword is None:

        line_bot_api.reply_message(
            event.reply_token,
            build_default_menu()
        )

        return

    if matched_keyword in CASES:

        flex_message = build_flex(
            matched_keyword,
            CASES[matched_keyword]
        )

        line_bot_api.reply_message(
            event.reply_token,
            flex_message
        )

    elif matched_keyword in SIMPLE_REPLIES:

        if matched_keyword == "โทร 1669":

            line_bot_api.reply_message(
                event.reply_token,
                build_call_1669()
            )

        else:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=SIMPLE_REPLIES[matched_keyword]
                )
            )

    else:

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=DEFAULT_REPLY
            )
        )


# ==================================
# รับตำแหน่งจาก LINE
# ==================================

@handler.add(MessageEvent, message=LocationMessage)
def handle_location(event):

    latitude = event.message.latitude
    longitude = event.message.longitude

    print("LOCATION RECEIVED")
    print("LATITUDE:", latitude)
    print("LONGITUDE:", longitude)

    try:

        # ค้นหาโรงพยาบาล
        hospitals = find_nearby_hospitals(
            latitude,
            longitude
        )

        reply_text = "🏥 โรงพยาบาลใกล้คุณ\n\n"

        if hospitals:

            for i, hospital in enumerate(
                hospitals,
                start=1
            ):

                map_link = (
                    "https://www.google.com/maps/search/?api=1&query="
                    f"{hospital['lat']},{hospital['lng']}"
                )

                reply_text += (
                    f"{i}. {hospital['name']}\n"
                    f"📍 ระยะประมาณ "
                    f"{hospital['distance']:.1f} กม.\n"
                    f"🗺️ {map_link}\n\n"
                )

        else:

            reply_text += (
                "ไม่พบข้อมูลโรงพยาบาลในรัศมี 10 กิโลเมตร\n"
                "กรุณาลองส่งตำแหน่งอื่นอีกครั้ง\n\n"
            )

        reply_text += (
            "🚑 หากเป็นเหตุฉุกเฉิน "
            "โปรดโทร 1669"
        )

    except Exception as e:

        print("HOSPITAL SEARCH ERROR:", str(e))

        reply_text = (
            "ขออภัย ระบบค้นหาโรงพยาบาลขัดข้อง\n"
            "กรุณาลองใหม่อีกครั้ง\n\n"
            "🚑 หากเป็นเหตุฉุกเฉิน โปรดโทร 1669"
        )

    # ส่งผลกลับ LINE
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text=reply_text
        )
    )


# ==================================
# เริ่มต้น Flask
# ==================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
