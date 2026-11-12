
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

from content import CASES, SIMPLE_REPLIES, DEFAULT_REPLY, KEYWORD_ORDER
from flex_messages import build_flex, build_default_menu, build_call_1669


# ========== CONFIG ==========
load_dotenv()
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise RuntimeError("กรุณาตั้งค่า LINE_CHANNEL_ACCESS_TOKEN และ LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

OVERPASS_SERVERS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
HEADERS = {"User-Agent": "EmergencyHelpBot/1.1 (LINE Bot)"}
MAX_RESULTS = 5
SEARCH_RADIUS_METERS = 25000


# ========== MAP HELPERS ==========
def distance_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    a = min(1.0, max(0.0, a))
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def google_maps_nearby_url(latitude, longitude):
    word = quote("โรงพยาบาล", safe="")
    return f"https://www.google.com/maps/search/{word}/@{latitude},{longitude},13z"


def google_maps_pin_url(latitude, longitude):
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"


def build_hospital_query(latitude, longitude):
    return f"""
    [out:json][timeout:15];
    (
      nwr["amenity"="hospital"](around:{SEARCH_RADIUS_METERS},{latitude},{longitude});
      nwr["healthcare"="hospital"](around:{SEARCH_RADIUS_METERS},{latitude},{longitude});
      nwr["amenity"="clinic"](around:{SEARCH_RADIUS_METERS},{latitude},{longitude});
      nwr["healthcare"="clinic"](around:{SEARCH_RADIUS_METERS},{latitude},{longitude});
    );
    out center tags;
    """


def find_nearby_hospitals(latitude, longitude):
    query = build_hospital_query(latitude, longitude)
    elements = None
    last_error = None

    for server in OVERPASS_SERVERS:
        try:
            print("CONNECTING TO:", server, flush=True)
            response = requests.post(
                server,
                data={"data": query},
                headers=HEADERS,
                timeout=(3, 10),
            )
            response.raise_for_status()
            data = response.json()
            if data.get("remark"):
                raise RuntimeError(str(data["remark"]))
            elements = data.get("elements")
            if not isinstance(elements, list):
                raise ValueError("Invalid Overpass response")
            print("OVERPASS SUCCESS:", len(elements), flush=True)
            break
        except (requests.RequestException, ValueError, RuntimeError) as error:
            last_error = error
            print("OVERPASS ERROR:", repr(error), flush=True)

    if elements is None:
        raise RuntimeError(f"Overpass unavailable: {last_error}")

    results = []
    for item in elements:
        tags = item.get("tags") or {}
        if tags.get("amenity") == "veterinary" or tags.get("healthcare") == "veterinary":
            continue
        center = item.get("center") or {}
        lat = item.get("lat")
        lon = item.get("lon")
        if lat is None:
            lat = center.get("lat")
        if lon is None:
            lon = center.get("lon")
        if lat is None or lon is None:
            continue
        name = (
            tags.get("name:th") or tags.get("name")
            or tags.get("official_name") or tags.get("name:en")
        )
        if not name:
            continue
        is_hospital = tags.get("amenity") == "hospital" or tags.get("healthcare") == "hospital"
        results.append({
            "name": str(name).strip(),
            "lat": lat,
            "lng": lon,
            "distance": distance_km(latitude, longitude, lat, lon),
            "type": "โรงพยาบาล" if is_hospital else "คลินิก",
        })

    results.sort(key=lambda h: h["distance"])
    unique = []
    for hospital in results:
        if any(
            hospital["name"].casefold() == old["name"].casefold()
            and distance_km(hospital["lat"], hospital["lng"], old["lat"], old["lng"]) < 0.15
            for old in unique
        ):
            continue
        unique.append(hospital)
        if len(unique) >= MAX_RESULTS:
            break
    return unique


def build_hospital_reply(hospitals, latitude, longitude):
    if not hospitals:
        return (
            "🏥 ไม่พบข้อมูลสถานพยาบาลใน OpenStreetMap ภายใน 25 กม.\n"
            "ลองค้นหาใน Google Maps:\n"
            f"{google_maps_nearby_url(latitude, longitude)}\n\n"
            "🚑 กรณีฉุกเฉินในไทย โทร 1669"
        )
    lines = ["🏥 สถานพยาบาลใกล้ตำแหน่งที่ส่งมา\n"]
    for i, h in enumerate(hospitals, 1):
        lines.append(
            f"{i}. {h['name']} ({h['type']})\n"
            f"📍 ระยะเส้นตรงประมาณ {h['distance']:.1f} กม.\n"
            f"🗺️ {google_maps_pin_url(h['lat'], h['lng'])}\n"
        )
    lines.append("โปรดตรวจสอบเส้นทางและเวลาเปิดบริการก่อนเดินทาง\n🚑 ฉุกเฉินในไทย โทร 1669")
    return "\n".join(lines)[:4900]


def search_and_send_hospitals(target_id, latitude, longitude):
    print("START SEARCH", flush=True)
    try:
        hospitals = find_nearby_hospitals(latitude, longitude)
        message = build_hospital_reply(hospitals, latitude, longitude)
    except Exception as error:
        print("SEARCH ERROR:", repr(error), flush=True)
        message = (
            "⚠️ ระบบค้นหารายชื่อสถานพยาบาลยังไม่พร้อมใช้งาน\n"
            "ค้นหาโรงพยาบาลใน Google Maps ได้ที่:\n"
            f"{google_maps_nearby_url(latitude, longitude)}\n\n"
            "🚑 หากฉุกเฉินในไทย โทร 1669"
        )
    try:
        line_bot_api.push_message(target_id, TextSendMessage(text=message))
        print("PUSH SUCCESS", flush=True)
    except Exception as error:
        print("PUSH ERROR:", repr(error), flush=True)


# ========== FLASK / LINE WEBHOOK ==========
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
    text = event.message.text.strip()
    print("RECEIVED MESSAGE:", text, flush=True)
    if any(word in text for word in (
        "ค้นหาโรงพยาบาล", "โรงพยาบาลใกล้ฉัน", "โรงพยาบาลใกล้เคียง", "หาโรงพยาบาล"
    )):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🏥 กด + ใน LINE เลือก 'ตำแหน่งที่ตั้ง' แล้วส่งตำแหน่งให้บอท\n🚑 ฉุกเฉินในไทย โทร 1669"),
        )
        return
    matched = next((keyword for keyword in KEYWORD_ORDER if keyword in text), None)
    if matched is None:
        reply = build_default_menu()
    elif matched in CASES:
        reply = build_flex(matched, CASES[matched])
    elif matched in SIMPLE_REPLIES:
        reply = build_call_1669() if matched == "โทร 1669" else TextSendMessage(text=SIMPLE_REPLIES[matched])
    else:
        reply = TextSendMessage(text=DEFAULT_REPLY)
    line_bot_api.reply_message(event.reply_token, reply)


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
            TextSendMessage(text="ไม่พบปลายทางสำหรับส่งผล กรุณาลองใหม่ในแชตส่วนตัว"),
        )
        return

    # ส่งแผนที่สำรองทันที แม้ API ภายนอกช้าหรือระบบรีสตาร์ต
    maps_url = google_maps_nearby_url(latitude, longitude)
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text=(
                "📍 ได้รับตำแหน่งแล้ว กำลังค้นหาสถานพยาบาลใกล้คุณ...\n\n"
                "หากผลค้นหายังไม่มา เปิด Google Maps ได้ทันที:\n"
                f"{maps_url}\n\n🚑 ฉุกเฉินในไทย โทร 1669"
            )
        ),
    )
    threading.Thread(
        target=search_and_send_hospitals,
        args=(target_id, latitude, longitude),
        daemon=True,
    ).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
