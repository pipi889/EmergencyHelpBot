
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


# ==================================
# ตั้งค่าระบบ
# ==================================

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise ValueError(
        "กรุณาตรวจสอบ LINE_CHANNEL_ACCESS_TOKEN "
        "และ LINE_CHANNEL_SECRET"
    )

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter"
]

HEADERS = {
    "User-Agent": "EmergencyHelpBot/1.0"
}


# ==================================
# คำนวณระยะทางระหว่าง 2 พิกัด
# ==================================

def distance_km(lat1, lon1, lat2, lon2):

    earth_radius = 6371.0

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1))
        * cos(radians(lat2))
        * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(
        sqrt(a),
        sqrt(1 - a)
    )

    return earth_radius * c


# ==================================
# สร้างคำค้นหาโรงพยาบาล
# ==================================

def build_hospital_query(latitude, longitude, radius):

    return f"""
    [out:json][timeout:40];
    (
      nwr["amenity"="hospital"]
        (around:{radius},{latitude},{longitude});

      nwr["healthcare"="hospital"]
        (around:{radius},{latitude},{longitude});

      nwr["amenity"="clinic"]
        (around:{radius},{latitude},{longitude});

      nwr["healthcare"="clinic"]
        (around:{radius},{latitude},{longitude});
    );
    out center tags;
    """


# ==================================
# ค้นหาโรงพยาบาลใกล้ฉัน
# ==================================

def find_nearby_hospitals(latitude, longitude):

    hospitals = []
    successful_search = False
    last_error = None

    # ค้นหา 10 กม. และขยายเป็น 25 กม.
    for radius in [10000, 25000]:

        query = build_hospital_query(
            latitude,
            longitude,
            radius
        )

        print(
            "SEARCH RADIUS:",
            radius / 1000,
            "KM"
        )

        # ทดลองเซิร์ฟเวอร์ทีละตัว
        for url in OVERPASS_SERVERS:

            try:

                print("CONNECTING TO:", url)

                response = requests.post(
                    url,
                    data={"data": query},
                    headers=HEADERS,
                    timeout=60
                )

                response.raise_for_status()

                data = response.json()

                if data.get("remark"):
                    raise ValueError(data["remark"])

                elements = data.get("elements", [])

                print("OVERPASS SUCCESS")
                print("FOUND ELEMENTS:", len(elements))

                successful_search = True

                for item in elements:

                    tags = item.get("tags", {})

                    # ไม่แสดงสถานพยาบาลสัตว์
                    if tags.get("healthcare") == "veterinary":
                        continue

                    if tags.get("amenity") == "veterinary":
                        continue

                    # ดึงพิกัด
                    center = item.get("center", {})

                    lat = item.get(
                        "lat",
                        center.get("lat")
                    )

                    lng = item.get(
                        "lon",
                        center.get("lon")
                    )

                    if lat is None or lng is None:
                        continue

                    # ดึงชื่อสถานที่
                    name = (
                        tags.get("name")
                        or tags.get("name:th")
                        or tags.get("official_name")
                        or tags.get("name:en")
                        or "สถานพยาบาลไม่มีชื่อ"
                    )

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

                # เชื่อมต่อสำเร็จแล้ว ไม่ต้องลองเซิร์ฟเวอร์อื่น
                break

            except Exception as error:

                last_error = error

                print(
                    "OVERPASS ERROR:",
                    url,
                    repr(error)
                )

        # ถ้าพบสถานพยาบาลแล้ว ไม่ต้องขยายรัศมี
        if hospitals:
            break

    # ถ้าเชื่อมต่อทุกเซิร์ฟเวอร์ไม่สำเร็จ
    if not successful_search:

        raise Exception(
            f"ไม่สามารถเชื่อมต่อ OpenStreetMap ได้: {last_error}"
        )

    # ลบข้อมูลซ้ำจากพิกัด
    unique_hospitals = {}

    for hospital in hospitals:

        key = (
            round(hospital["lat"], 6),
            round(hospital["lng"], 6)
        )

        if key not in unique_hospitals:
            unique_hospitals[key] = hospital

    hospitals = list(unique_hospitals.values())

    # เรียงจากใกล้ที่สุด
    hospitals.sort(
        key=lambda h: h["distance"]
    )

    print(
        "FINAL HOSPITAL COUNT:",
        len(hospitals)
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
        "X-Line-Signature",
        ""
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

    print("========== LOCATION RECEIVED ==========")
    print("LATITUDE:", latitude)
    print("LONGITUDE:", longitude)

    try:

        # ค้นหาสถานพยาบาลใกล้พิกัด
        hospitals = find_nearby_hospitals(
            latitude,
            longitude
        )

        reply_text = "🏥 สถานพยาบาลใกล้คุณ\n\n"

        if hospitals:

            for index, hospital in enumerate(
                hospitals,
                start=1
            ):

                map_link = (
                    "https://www.google.com/maps/search/?api=1&query="
                    f"{hospital['lat']},{hospital['lng']}"
                )

                reply_text += (
                    f"{index}. {hospital['name']}\n"
                    f"📍 ระยะประมาณ "
                    f"{hospital['distance']:.1f} กม.\n"
                    f"🗺️ {map_link}\n\n"
                )

        else:

            reply_text += (
                "ไม่พบข้อมูลโรงพยาบาลหรือคลินิก "
                "ในรัศมี 25 กิโลเมตร\n"
                "กรุณาลองส่งตำแหน่งอีกครั้ง\n\n"
            )

        reply_text += (
            "🚑 หากเป็นเหตุฉุกเฉิน "
            "โปรดโทร 1669"
        )

    except Exception as error:

        print(
            "HOSPITAL SEARCH ERROR:",
            repr(error)
        )

        reply_text = (
            "ขออภัย ระบบค้นหาสถานพยาบาลขัดข้อง\n"
            "กรุณาลองใหม่อีกครั้ง\n\n"
            "🚑 หากเป็นเหตุฉุกเฉิน โปรดโทร 1669"
        )

    # ส่งข้อความกลับ LINE
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
