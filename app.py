
from flask import Flask, request, abort
from dotenv import load_dotenv
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import quote
import os
import threading
import requests

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    LocationMessage,
)

from content import (
    CASES, SIMPLE_REPLIES, DEFAULT_REPLY, KEYWORD_ORDER
)
from flex_messages import (
    build_flex, build_default_menu, build_call_1669
)

# ========== CONFIG ==========

load_dotenv()
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise RuntimeError(
        "กรุณาตั้งค่า LINE_CHANNEL_ACCESS_TOKEN และ LINE_CHANNEL_SECRET"
    )

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

MAX_RESULTS = 5
SEARCH_RADIUS_METERS = 25000


# ========== MAP HELPERS ==========

def distance_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1))
        * cos(radians(lat2))
        * sin(dlon / 2) ** 2
    )

    a = max(0.0, min(1.0, a))

    return 2 * radius * atan2(sqrt(a), sqrt(1 - a))


def google_maps_nearby_url(lat, lon):
    keyword = quote("โรงพยาบาล", safe="")
    return (
        f"https://www.google.com/maps/search/"
        f"{keyword}/@{lat},{lon},13z"
    )


def google_maps_pin_url(lat, lon):
    return (
        "https://www.google.com/maps/search/"
        f"?api=1&query={lat},{lon}"
    )


# ========== GEOAPIFY HOSPITAL SEARCH ==========

def find_nearby_hospitals(latitude, longitude):

    if not GEOAPIFY_API_KEY:
        raise RuntimeError(
            "ไม่พบ GEOAPIFY_API_KEY ใน Render Environment"
        )

    params = {
        "categories": "healthcare.hospital",
        "filter": (
            f"circle:{longitude},{latitude},"
            f"{SEARCH_RADIUS_METERS}"
        ),
        "bias": f"proximity:{longitude},{latitude}",
        "limit": 50,
        "lang": "th",
        "apiKey": GEOAPIFY_API_KEY,
    }

    print("GEOAPIFY SEARCH START", flush=True)

    response = requests.get(
        "https://api.geoapify.com/v2/places",
        params=params,
        timeout=(5, 15),
    )

    response.raise_for_status()

    data = response.json()

    features = data.get("features")

    if not isinstance(features, list):
        raise ValueError("Geoapify response ไม่ถูกต้อง")

    print("GEOAPIFY FOUND:", len(features), flush=True)

    results = []

    for feature in features:

        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []

        lat = props.get("lat")
        lon = props.get("lon")

        if (lat is None or lon is None) and len(coords) >= 2:
            lon, lat = coords[:2]

        name = props.get("name")

        if (
            lat is None
            or lon is None
            or not isinstance(name, str)
            or not name.strip()
        ):
            continue

        try:
            lat = float(lat)
            lon = float(lon)

            distance = distance_km(
                latitude, longitude, lat, lon
            )

        except (TypeError, ValueError):
            continue

        if distance > SEARCH_RADIUS_METERS / 1000:
            continue

        results.append({
            "name": name.strip(),
            "lat": lat,
            "lng": lon,
            "distance": distance,
            "type": "โรงพยาบาล",
        })

    # เรียงจากใกล้ที่สุด
    results.sort(key=lambda h: h["distance"])

    # ลบโรงพยาบาลที่ซ้ำกัน
    unique = []

    for hospital in results:

        duplicate = any(
            hospital["name"].casefold()
            == old["name"].casefold()
            and distance_km(
                hospital["lat"],
                hospital["lng"],
                old["lat"],
                old["lng"]
            ) < 0.15
            for old in unique
        )

        if duplicate:
            continue

        unique.append(hospital)

        if len(unique) >= MAX_RESULTS:
            break

    print("HOSPITAL RESULTS:", len(unique), flush=True)

    return unique


# ========== HOSPITAL REPLY ==========

def build_hospital_reply(hospitals, latitude, longitude):

    if not hospitals:
        return (
            "🏥 ไม่พบรายชื่อโรงพยาบาลในฐานข้อมูล"
            "ภายใน 25 กม.\n"
            "ลองดู Google Maps เพิ่มเติม:\n"
            f"{google_maps_nearby_url(latitude, longitude)}\n\n"
            "🚑 ฉุกเฉินในไทย โทร 1669"
        )

    lines = ["🏥 โรงพยาบาลใกล้ตำแหน่งที่ส่งมา\n"]

    for i, h in enumerate(hospitals, 1):

        lines.append(
            f"{i}. {h['name']}\n"
            f"📍 ระยะเส้นตรงประมาณ "
            f"{h['distance']:.1f} กม.\n"
            f"🗺️ {google_maps_pin_url(h['lat'], h['lng'])}\n"
        )

    lines.append(
        "โปรดตรวจสอบเวลาบริการและห้องฉุกเฉินก่อนเดินทาง\n"
        "🚑 ฉุกเฉินในไทย โทร 1669"
    )

    return "\n".join(lines)[:4900]


# ========== BACKGROUND SEARCH ==========

def search_and_send_hospitals(target_id, latitude, longitude):

    print("START SEARCH", flush=True)

    try:
        hospitals = find_nearby_hospitals(
            latitude, longitude
        )

        message = build_hospital_reply(
            hospitals, latitude, longitude
        )

    except Exception as error:

        print("SEARCH ERROR:", repr(error), flush=True)

        message = (
            "⚠️ ระบบค้นหารายชื่อโรงพยาบาล"
            "ไม่พร้อมใช้งานชั่วคราว\n"
            "ดูโรงพยาบาลบน Google Maps:\n"
            f"{google_maps_nearby_url(latitude, longitude)}\n\n"
            "🚑 ฉุกเฉินในไทย โทร 1669"
        )

    try:
        line_bot_api.push_message(
            target_id,
            TextSendMessage(text=message)
        )

        print("PUSH SUCCESS", flush=True)

    except Exception as error:
        print("PUSH ERROR:", repr(error), flush=True)


# ========== FLASK / LINE WEBHOOK ==========

@app.route("/")
def home():
    return "Emergency Help Bot Running", 200


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

    return "OK", 200


# ========== TEXT MESSAGE ==========

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    user_text = event.message.text.strip()

    print("RECEIVED MESSAGE:", user_text, flush=True)

    hospital_keywords = (
        "ค้นหาโรงพยาบาล",
        "โรงพยาบาลใกล้ฉัน",
        "โรงพยาบาลใกล้เคียง",
        "หาโรงพยาบาล"
    )

    if any(term in user_text for term in hospital_keywords):

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=(
                    "🏥 กด + ใน LINE แล้วเลือก "
                    "'ตำแหน่งที่ตั้ง' เพื่อส่งให้บอท\n"
                    "🚑 ฉุกเฉินในไทย โทร 1669"
                )
            )
        )

        return

    matched = next(
        (k for k in KEYWORD_ORDER if k in user_text),
        None
    )

    if matched is None:
        reply = build_default_menu()

    elif matched in CASES:
        reply = build_flex(
            matched, CASES[matched]
        )

    elif matched in SIMPLE_REPLIES:

        if matched == "โทร 1669":
            reply = build_call_1669()
        else:
            reply = TextSendMessage(
                text=SIMPLE_REPLIES[matched]
            )

    else:
        reply = TextSendMessage(
            text=DEFAULT_REPLY
        )

    line_bot_api.reply_message(
        event.reply_token, reply
    )


# ========== LOCATION MESSAGE ==========

@handler.add(MessageEvent, message=LocationMessage)
def handle_location(event):

    latitude = event.message.latitude
    longitude = event.message.longitude

    print("LOCATION RECEIVED", flush=True)

    source = event.source

    target_id = (
        getattr(source, "group_id", None)
        or getattr(source, "room_id", None)
        or getattr(source, "user_id", None)
    )

    if not target_id:

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="ไม่พบปลายทาง กรุณาลองใหม่ในแชตส่วนตัว"
            )
        )

        return

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text=(
                "📍 ได้รับตำแหน่งแล้ว "
                "กำลังค้นหาโรงพยาบาลใกล้คุณ...\n\n"
                "หากยังไม่มีผลลัพธ์ เปิด Google Maps ได้ที่:\n"
                f"{google_maps_nearby_url(latitude, longitude)}\n\n"
                "🚑 ฉุกเฉินในไทย โทร 1669"
            )
        )
    )

    threading.Thread(
        target=search_and_send_hospitals,
        args=(target_id, latitude, longitude),
        daemon=True
    ).start()


# ========== RUN FLASK ==========

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
